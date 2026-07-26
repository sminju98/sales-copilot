#!/usr/bin/env python3
"""vox.ai(TryVox) AI 전화 에이전트 연동 — 검증된 v3 API만 호출한다.

vox.ai(주식회사 플릭, https://www.tryvox.co)는 **별도 가입·과금이 필요한 유료
서비스**다. 이 스크립트는 대시보드에서 발급한 조직 API 키로, docs.tryvox.co 에서
확인된 엔드포인트만 호출한다(api-reference/v3 문서로 경로·필드 검증 완료):

  GET  https://client-api.tryvox.co/v3/agents               — 에이전트 목록
  POST https://client-api.tryvox.co/v3/agents/validate-flow — 플로우 검증
  POST https://client-api.tryvox.co/v3/calls                — 아웃바운드 단건 콜
  GET  https://client-api.tryvox.co/v3/calls/{call_id}      — 통화 결과 조회(전사·분석)

문서에서 확인 안 된 경로는 추측으로 만들지 않는다. 대량(스프레드시트) 캠페인·
전화번호 발급·SIP 연결·통화 후 분석 웹훅 설정 등 나머지는 vox 대시보드/CLI 에서
수행한다(docs.tryvox.co 참조). 레이트리밋: 초당 5·분당 120 요청.

사용:
  python3 scripts/vox_call.py status                 # 설정·연결 점검(미설정이면 온보딩 안내, exit 0)
  python3 scripts/vox_call.py agents [--q 이름] [--limit N] [--cursor C]
  python3 scripts/vox_call.py validate --flow flow.json [--agent-id ID] [--level critical|runtime|all]
  python3 scripts/vox_call.py call --to 01012345678 [--from 07012345678] [--agent-id ID] \
      [--var k=v ...] [--meta lead_id=ld_1 ...] [--email a@b.com] [--dry-run]
  python3 scripts/vox_call.py result <call_id>       # 통화 상태·요약·분석 조회(CRM 기록용)

안전장치(발신 게이트 — call 서브커맨드):
  - config.vox.enabled=true 가 아니면 발신하지 않는다(조회·검증은 키만 있으면 가능).
  - --email 을 주면 suppressions(수신거부) 검사에 걸릴 경우 발신 거부.
  - quiet hours(config policy.quiet_hours, 기본 21:00-08:00) 안이면 발신 거부.
    사용자가 명시적으로 승인한 예외만 --override-quiet-hours 로 통과.
  - api_key 가 없으면 어떤 요청도 보내지 않고 안내만 출력한다.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import http_request, load_config

BASE = "https://client-api.tryvox.co/v3"
DOCS = "https://docs.tryvox.co"

ONBOARD = """[미설정] vox.ai 연동이 아직 없습니다.
AI 전화(아웃바운드 콜·인바운드 응대)는 이 플러그인에 내장된 기능이 아니라,
별도 가입·과금이 필요한 유료 서비스 vox.ai(주식회사 플릭)를 실행 레이어로 씁니다.

연동 순서:
  1) https://www.tryvox.co 가입 → 대시보드에서 전화 에이전트 구축
     (아웃바운드 콜용·인바운드 응대용을 따로 만들어 두면 좋습니다)
  2) 대시보드에서 조직 API 키 발급. 인바운드를 쓰려면 전화번호 발급 또는
     대표번호 SIP 연결, 통화 후 분석 웹훅도 대시보드에서 설정
  3) 설정 저장:
     python3 scripts/set_config.py vox.enabled=true vox.api_key=<키> \\
       vox.outbound_agent_id=<ID> vox.inbound_agent_id=<ID> vox.phone_number=<발신번호>
  4) 재점검: python3 scripts/vox_call.py status

자세한 방법(대시보드 퀵스타트·vox CLI·API): https://docs.tryvox.co"""


# ---------- 공통 ----------

def _vox(cfg_all=None):
    cfg_all = cfg_all if cfg_all is not None else load_config(soft=True)
    return cfg_all.get("vox", {}) or {}


def _mask(s):
    s = (s or "").strip()
    if not s:
        return "(없음)"
    return s[:4] + "…" + s[-2:] if len(s) > 8 else "설정됨"


def _headers(key):
    return {"Authorization": f"Bearer {key}"}


def _url(path, params=None):
    url = BASE + path
    if params:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        if qs:
            url += "?" + qs
    return url


def _err_detail(res):
    """공통 에러 문구 — vox 에러 형식 {error:{code,message,details}} 우선."""
    body = res.get("body")
    if isinstance(body, dict):
        e = body.get("error") or {}
        if isinstance(e, dict) and e.get("message"):
            return f"{e.get('code') or ''} {e['message']}".strip()
    if isinstance(body, str) and body.strip():
        return body.strip()[:300]
    return res.get("error") or f"HTTP {res.get('status')}"


def _failed(res):
    return bool(res.get("error")) or (res.get("status") or 0) >= 300


def _digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _kv_dict(pairs, opt):
    out = {}
    for kv in pairs or []:
        if "=" not in kv:
            raise SystemExit(f"[형식 오류] {opt} 는 key=value 형식입니다: {kv!r}")
        k, v = kv.split("=", 1)
        out[k.strip()] = v
    return out


def _in_quiet_hours(cfg_all, now=None):
    """(안이면 True, 창 문자열). 창은 'HH:MM-HH:MM', 자정 넘김 지원. 파싱 실패 시 False."""
    win = (cfg_all.get("policy", {}) or {}).get("quiet_hours") or "21:00-08:00"
    try:
        s, e = str(win).split("-")
        sh, sm = (int(x) for x in s.strip().split(":"))
        eh, em = (int(x) for x in e.strip().split(":"))
    except (ValueError, AttributeError):
        return False, win
    now = now or datetime.datetime.now().time()
    start, end = datetime.time(sh, sm), datetime.time(eh, em)
    inside = (start <= now < end) if start <= end else (now >= start or now < end)
    return inside, win


# ---------- 서브커맨드 ----------

def cmd_status(args):
    cfg_all = load_config(soft=True)
    v = _vox(cfg_all)
    key = (v.get("api_key") or "").strip()
    print("📞 vox.ai(TryVox) AI 전화 — 설정 점검")
    print(f"  enabled           : {bool(v.get('enabled', False))}")
    print(f"  api_key           : {_mask(key)}")
    print(f"  outbound_agent_id : {v.get('outbound_agent_id') or '(없음)'}")
    print(f"  inbound_agent_id  : {v.get('inbound_agent_id') or '(없음)'}")
    print(f"  phone_number      : {v.get('phone_number') or '(없음)'}")
    if not key:
        print()
        print(ONBOARD)
        return 0
    res = http_request(_url("/agents", {"limit": 1}), headers=_headers(key))
    if _failed(res):
        print(f"\n⛔ 연결 실패: {_err_detail(res)}")
        print(f"  대시보드(조직 설정)에서 API 키가 유효한지 확인하세요. 문서: {DOCS}")
        return 1
    body = res.get("body") if isinstance(res.get("body"), dict) else {}
    total = body.get("total_count")
    print(f"\n✅ 연결 정상 — 조직 에이전트 {total if total is not None else '?'}개. 목록: vox_call.py agents")
    if not v.get("enabled"):
        print("  (vox.enabled=false — 발신 실행(call)은 잠겨 있습니다. 켜려면: python3 scripts/set_config.py vox.enabled=true)")
    return 0


def cmd_agents(args):
    v = _vox()
    key = (v.get("api_key") or "").strip()
    if not key:
        print(ONBOARD)
        return 2
    res = http_request(
        _url("/agents", {"limit": args.limit, "q": args.q, "cursor": args.cursor}),
        headers=_headers(key))
    if _failed(res):
        print(f"⛔ 목록 조회 실패: {_err_detail(res)}")
        return 1
    body = res.get("body") if isinstance(res.get("body"), dict) else {}
    items = body.get("items") or []
    if not items:
        print("에이전트가 없습니다 — vox 대시보드에서 먼저 아웃바운드/인바운드 에이전트를 만드세요. " + DOCS)
        return 0
    print(f"📞 vox 에이전트 {body.get('total_count', len(items))}개")
    for it in items:
        aid = it.get("agent_id", "?")
        mark = ""
        if aid == v.get("outbound_agent_id"):
            mark = "  ← config.outbound_agent_id"
        elif aid == v.get("inbound_agent_id"):
            mark = "  ← config.inbound_agent_id"
        prod = it.get("production_version") or "-"
        print(f"- {it.get('name') or '(이름 없음)'} · {it.get('type') or '?'} · prod={prod} · {aid}{mark}")
    if body.get("next_cursor"):
        print(f"(다음 페이지: --cursor {body['next_cursor']})")
    return 0


def _load_flow(path):
    if path == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            raise SystemExit(f"[파일 없음] {path}")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"[JSON 오류] {path}: {e}")
    # {"flow": {...}} 형태면 그대로, 플로우 본문({nodes, edges})만 있으면 감싼다.
    if isinstance(obj, dict) and "flow" in obj:
        return obj
    return {"flow": obj}


def cmd_validate(args):
    v = _vox()
    key = (v.get("api_key") or "").strip()
    if not key:
        print(ONBOARD)
        return 2
    payload = _load_flow(args.flow)
    res = http_request(
        _url("/agents/validate-flow", {"level": args.level, "agent_id": args.agent_id}),
        payload=payload, headers=_headers(key))
    if _failed(res):
        print(f"⛔ 검증 요청 실패: {_err_detail(res)}")
        return 1
    body = res.get("body") if isinstance(res.get("body"), dict) else {}
    errors = body.get("errors") or []
    advisories = body.get("advisories") or []
    if body.get("valid"):
        print(f"✅ 플로우 유효 (오류 0 · 어드바이저리 {len(advisories)})")
    else:
        print(f"⛔ 플로우 오류 {len(errors)}건")
    for e in errors:
        loc = e.get("node_id") or e.get("field") or "-"
        print(f"  [오류] {loc}: {e.get('message', '')}")
    for a in advisories:
        loc = a.get("node_id") or a.get("field") or "-"
        print(f"  [참고] {loc}: {a.get('message', '')}")
    if not body.get("valid"):
        return 1
    print("반영은 vox 대시보드에서: 에이전트 플로우 편집 → 배포. " + DOCS)
    return 0


def cmd_call(args):
    cfg_all = load_config(soft=True)
    v = _vox(cfg_all)
    key = (v.get("api_key") or "").strip()
    if not key:
        print(ONBOARD)
        return 2
    if not v.get("enabled"):
        print("⛔ 발신 잠김: vox.enabled=false — 점검(vox_call.py status) 후 켜세요: python3 scripts/set_config.py vox.enabled=true")
        return 2
    to = _digits(args.to)
    if not to:
        print("⛔ 수신번호(--to)가 비었거나 숫자가 없습니다.")
        return 2
    frm = _digits(args.from_number or v.get("phone_number") or "")
    if not frm:
        print("⛔ 발신번호 없음 — config vox.phone_number 를 설정하거나 --from 으로 지정하세요(vox 대시보드에서 발급한 번호).")
        return 2

    # 발신 게이트 1: 수신거부(이메일 기준 — 전화 거절·재접촉 금지는 스킬이 CRM 기록으로 확인)
    if args.email:
        from crm import suppress_check
        if suppress_check(args.email):
            print(f"⛔ 발신 중단: {args.email} 은 수신거부(suppressions) 등록 상태입니다.")
            return 2

    # 발신 게이트 2: 전화 금지 시간(quiet hours)
    inside, win = _in_quiet_hours(cfg_all)
    if inside and not args.override_quiet_hours:
        print(f"⛔ 발신 중단: 지금은 전화 금지 시간({win})입니다 — 업무시간에 다시 시도하세요.")
        print("  (수신자가 이 시간대 통화를 명시적으로 요청한 예외만 --override-quiet-hours)")
        return 2

    payload = {"from_number": frm, "to_number": to}
    pres = _digits(args.presentation_number or "")
    if pres:
        # 수신자 화면에 표시될 번호 — vox 대시보드에서 조직 승인(발신표기번호 등록)된 번호만 유효.
        payload["presentation_number"] = pres
    agent_id = (args.agent_id or v.get("outbound_agent_id") or "").strip()
    if agent_id:
        agent = {"agent_id": agent_id}
        if args.agent_version:
            agent["agent_version"] = args.agent_version
        payload["agent"] = agent
    dv = _kv_dict(args.var, "--var")
    if dv:
        payload["dynamic_variables"] = dv
    meta = _kv_dict(args.meta, "--meta")
    if meta:
        payload["metadata"] = meta

    if args.dry_run:
        print("[DRY-RUN] POST " + BASE + "/calls")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    res = http_request(_url("/calls"), payload=payload, headers=_headers(key))
    if _failed(res):
        print(f"⛔ 콜 생성 실패: {_err_detail(res)}")
        return 1
    body = res.get("body") if isinstance(res.get("body"), dict) else {}
    print(f"📞 콜 요청됨 · id={body.get('id', '?')} · status={body.get('status', '?')} · {frm} → {to}")
    print("  진행·전사·분석은 vox 대시보드/통화 후 웹훅으로 확인하세요. 결과가 확인되면 기록:")
    print('  python3 scripts/crm.py add activity --json \'{"type":"call","direction":"outbound","summary":"<통화 요약>","result":"<미팅 확정|재통화|거절|부재>"}\'')
    return 0


def _fmt_ms(ms):
    if not ms:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ms)


def cmd_result(args):
    """통화 결과 조회 (GET /v3/calls/{call_id}) — 요약·분석을 CRM 기록에 옮겨 적는 용도."""
    v = _vox()
    key = (v.get("api_key") or "").strip()
    if not key:
        print(ONBOARD)
        return 2
    res = http_request(_url(f"/calls/{args.call_id}"), headers=_headers(key))
    if _failed(res):
        print(f"⛔ 통화 조회 실패: {_err_detail(res)}")
        return 1
    body = res.get("body") if isinstance(res.get("body"), dict) else {}
    dur = ""
    if body.get("start_at") and body.get("end_at"):
        try:
            dur = f" · {round((int(body['end_at']) - int(body['start_at'])) / 1000)}초"
        except (ValueError, TypeError):
            pass
    print(f"📞 통화 {body.get('id', args.call_id)} · status={body.get('status', '?')}{dur}")
    print(f"  시작 {_fmt_ms(body.get('start_at'))} · 종료 {_fmt_ms(body.get('end_at'))}"
          f" · 종료사유 {body.get('disconnection_reason') or '-'}")
    ana = body.get("call_analysis")
    if isinstance(ana, dict) and ana:
        print("  [통화 후 분석]")
        print("  " + json.dumps(ana, ensure_ascii=False, indent=2).replace("\n", "\n  "))
    elif body.get("opt_out_sensitive_data_storage"):
        print("  (민감정보 저장 미동의 설정 — 전사·분석 없음)")
    else:
        print("  (분석 결과 아직 없음 — 통화 종료·분석 완료 후 다시 조회하거나 웹훅/대시보드 확인)")
    if body.get("status") == "ended":
        print("  결과가 확인되면 기록:")
        print('  python3 scripts/crm.py add activity --json \'{"type":"call","direction":"outbound","summary":"<분석 요약>","result":"<미팅 확정|재통화|거절|부재>"}\'')
    return 0


def main():
    ap = argparse.ArgumentParser(description="vox.ai(TryVox) AI 전화 — 검증된 v3 API만 사용")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="설정·연결 점검(미설정이면 온보딩 안내)")

    sp = sub.add_parser("agents", help="에이전트 목록 (GET /v3/agents)")
    sp.add_argument("--q", help="이름 검색어")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--cursor", help="이전 응답의 next_cursor")

    sp = sub.add_parser("validate", help="플로우 검증 (POST /v3/agents/validate-flow)")
    sp.add_argument("--flow", required=True, help="플로우 JSON 파일 경로('-'=stdin)")
    sp.add_argument("--agent-id", help="기존 에이전트 대비 패치 검증")
    sp.add_argument("--level", choices=["critical", "runtime", "all"])

    sp = sub.add_parser("call", help="아웃바운드 단건 콜 (POST /v3/calls) — 발신 게이트 통과 필수")
    sp.add_argument("--to", required=True, help="수신번호(하이픈 무관)")
    sp.add_argument("--from", dest="from_number", help="발신번호(기본: config vox.phone_number)")
    sp.add_argument("--agent-id", help="기본: config vox.outbound_agent_id")
    sp.add_argument("--agent-version", help='"current" | "production" | "v{n}"')
    sp.add_argument("--var", action="append", default=[], help="dynamic_variables k=v (반복 가능)")
    sp.add_argument("--meta", action="append", default=[], help="metadata k=v (반복 가능, 예: lead_id=ld_1)")
    sp.add_argument("--email", help="상대 이메일(알면) — 수신거부 검사에 사용")
    sp.add_argument("--presentation-number", help="수신자에게 표시될 번호(조직 승인된 발신표기번호만)")
    sp.add_argument("--override-quiet-hours", action="store_true",
                    help="전화 금지 시간 예외(수신자가 명시 요청한 경우만)")
    sp.add_argument("--dry-run", action="store_true", help="요청 본문만 출력하고 발신하지 않음")

    sp = sub.add_parser("result", help="통화 결과 조회 (GET /v3/calls/{call_id}) — 요약·분석을 CRM 기록용으로")
    sp.add_argument("call_id", help="call 실행 시 받은 통화 id")

    args = ap.parse_args()
    fn = {"status": cmd_status, "agents": cmd_agents, "validate": cmd_validate,
          "call": cmd_call, "result": cmd_result}[args.cmd]
    raise SystemExit(fn(args) or 0)


if __name__ == "__main__":
    main()
