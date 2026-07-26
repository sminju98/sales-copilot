#!/usr/bin/env python3
"""설정·데이터 점검: AI 영업맨을 돌리기 위해 아직 채워야 할 것을 ✅/⚠️ 로 보여준다.

config 필수키 → approval_mode → 영업 컨텍스트 8종 → 로컬 CRM 레코드 → 수신거부 목록
→ 다음 행동 보유율(핵심 KPI) → 전달 채널 → 내장 엔진 6종(cards/sms/email_send/coldmail/
vox/team — 전부 선택, 미설정은 ℹ️ 안내만) → Apps Script 폴더 2종 → 파이썬 버전 순서로 훑는다.

  python3 scripts/doctor.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG_PATH, CONTEXT_DIR, CRM_DIR, ROOT, env_webhook

APPROVAL_MODES = ("auto", "batch", "per_item", "draft_only", "escalate")
# 영업 컨텍스트 8종(+ _policy 데이터 경계) — 아웃바운드 문안·미팅 브리핑의 뿌리.
CONTEXT_FILES = ["company.md", "products.md", "icp.md", "pricing.md",
                 "cases.md", "objections.md", "permissions.md", "message-style.md"]
CRM_ENTITIES = ["accounts", "contacts", "relationships", "leads",
                "activities", "opportunities", "suppressions"]
# 리드·기회에서 '닫힌' 상태로 취급하는 값 — 다음 행동 보유율 계산에서 제외.
CLOSED_STATUSES = {"won", "lost", "closed", "dropped", "archived", "disqualified", "suppressed"}
PLACEHOLDERS = ["홍길동", "우리 회사", "우리회사", "무엇을 누구에게 파는지",
                "무엇을, 누구에게", "example.com", "여기에", "XXXX"]


def missing(v):
    if v in (None, "", 0, [], {}):
        return True
    return any(p in str(v) for p in PLACEHOLDERS)


def read_jsonl(path):
    """JSONL(한 줄 = 한 레코드) 로드. 깨진 줄은 건너뛴다."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def next_action_rate():
    """활성 리드·기회 중 다음 행동(next_action/next_action_date)이 있는 비율 — 핵심 KPI.
    crm.py 가 없거나(아직 미생성) 어떤 이유로든 실패하면 None(항목 생략)."""
    try:
        import crm  # noqa: F401 — 데이터 포맷의 주인인 crm.py 존재 확인(없으면 계산도 생략)
    except Exception:
        return None
    try:
        active = with_next = 0
        for entity in ("leads", "opportunities"):
            p = os.path.join(CRM_DIR, entity + ".jsonl")
            if not os.path.exists(p):
                continue
            for r in read_jsonl(p):
                if str(r.get("status", "")).lower() in CLOSED_STATUSES:
                    continue
                active += 1
                if r.get("next_action") or r.get("next_action_date"):
                    with_next += 1
        if active == 0:
            return None
        return active, with_next
    except Exception:
        return None


def main():
    if not os.path.exists(CONFIG_PATH):
        print("⚠️  아직 설정 전입니다. 클로드에게 'AI 영업맨 설정 시작하자'라고 말해보세요.")
        print("   (또는 python3 scripts/quicksetup.py 로 질문 11개 안내를 볼 수 있습니다)")
        return

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    me = cfg.get("me", {})
    company = cfg.get("company", {})
    delivery = cfg.get("delivery", {})

    print("=== AI 영업맨(Sales Copilot) 설정 점검 ===\n")
    ready = True

    # 1) 필수키 — 이름·자격·판매상품·승인 방식·회사
    left = [f"me.{k}" for k in ("name", "rank", "sell_what", "approval_mode") if missing(me.get(k))]
    left += [f"company.{k}" for k in ("name",) if missing(company.get(k))]
    if left:
        ready = False
        print(f"  ⚠️  기본 설정: 채울 값 → {', '.join(left)}")
    else:
        print(f"  ✅ 기본 설정: {me.get('rank')} · {me.get('title') or '직책 미입력'} · \"{me.get('sell_what')}\"")

    # 2) approval_mode 유효값 — 발송 게이트의 축. 잘못된 값이면 모든 발송 스킬이 멈춘다.
    mode = me.get("approval_mode", "")
    if mode in APPROVAL_MODES:
        note = " (자동 발송 없음 — 초안까지만)" if mode == "draft_only" else ""
        print(f"  ✅ 승인 방식: {mode}{note}")
    else:
        ready = False
        print(f"  ⚠️  승인 방식: '{mode}' 은 유효값이 아님 → {' / '.join(APPROVAL_MODES)} 중 하나로. "
              "미설정이면 자동 발송은 전면 금지로 동작합니다.")

    # 3) 영업 컨텍스트 8종 — 존재 + '템플릿 그대로'인지(레포 템플릿과 동일하면 미작성)
    absent, untouched = [], []
    for fn in CONTEXT_FILES:
        p = os.path.join(CONTEXT_DIR, fn)
        if not os.path.exists(p):
            absent.append(fn)
            continue
        tpl = os.path.join(ROOT, "templates", "context", fn)
        try:
            if os.path.exists(tpl):
                with open(p, encoding="utf-8") as f1, open(tpl, encoding="utf-8") as f2:
                    if f1.read().strip() == f2.read().strip():
                        untouched.append(fn)
        except OSError:
            pass
    if absent:
        ready = False
        print(f"  ⚠️  영업 컨텍스트: {len(absent)}개 없음({', '.join(absent)}) — "
              "quicksetup.py 가 템플릿을 복사해 줍니다.")
    elif untouched:
        ready = False
        print(f"  ⚠️  영업 컨텍스트: 8종 있음, {len(untouched)}개는 아직 템플릿 그대로"
              f"({', '.join(untouched)}) — 클로드에게 '영업 컨텍스트 채우자'라고 하세요.")
    else:
        print("  ✅ 영업 컨텍스트: 8종 모두 작성됨")
    if not os.path.exists(os.path.join(CONTEXT_DIR, "_policy.md")):
        print("  ⚠️  데이터 경계(_policy.md) 없음 — 개인 인맥·할인 한도 보호 규칙이 비어 있습니다.")

    # 4) 로컬 CRM — 파일·레코드 수 (커넥터 CRM을 쓰더라도 로컬은 큐·수신거부의 기준)
    counts = {}
    for e in CRM_ENTITIES:
        p = os.path.join(CRM_DIR, e + ".jsonl")
        if os.path.exists(p):
            try:
                counts[e] = sum(1 for ln in open(p, encoding="utf-8") if ln.strip())
            except OSError:
                pass
    total = sum(v for k, v in counts.items() if k != "suppressions")
    if total:
        shown = " · ".join(f"{e} {counts[e]}" for e in CRM_ENTITIES if counts.get(e))
        print(f"  ✅ 로컬 CRM({CRM_DIR}): {shown}")
    else:
        print("  ⚠️  로컬 CRM: 아직 비어 있음 — 명함 등록(import-cards)이나 리드 발굴(find-leads)부터 시작하세요.")

    # 5) 수신거부 목록 — 발송 전 suppress-check 의 근거
    sup = counts.get("suppressions", 0)
    print(f"  {'✅' if sup else 'ℹ️ '} 수신거부 목록: {sup}건 등록"
          + ("" if sup else " (회신에서 수신거부가 오면 crm.py suppress add 로 즉시 반영)"))

    # 6) 다음 행동 보유율 — 핵심 KPI(100%에 가깝게). crm.py 미생성 등 실패 시 생략.
    stats = next_action_rate()
    if stats:
        active, with_next = stats
        rate = with_next / active * 100
        mark = "✅" if rate >= 90 else "⚠️ "
        if rate < 90:
            ready = False
        print(f"  {mark} 다음 행동 보유율: {rate:.0f}% ({with_next}/{active}) — "
              + ("좋습니다" if rate >= 90 else "crm.py next-missing 으로 빠진 리드부터 채우세요"))

    # 7) 전달 채널 — 나만 보기/팀 공유 (환경변수 웹훅도 인정)
    dests = []
    if delivery.get("private", {}).get("enabled"):
        dests.append(("나만 보기", bool(delivery["private"].get("slack_webhook") or env_webhook("private"))))
    if delivery.get("team", {}).get("enabled"):
        dests.append(("팀 공유", bool(delivery["team"].get("slack_webhook") or env_webhook("team"))))
    if not dests:
        print("  ℹ️  전달 채널: 미설정 — 채팅 미리보기로만 동작(브리핑을 슬랙으로 받으려면 웹훅 연결)")
    else:
        for label, ok in dests:
            if ok:
                print(f"  ✅ 전달 채널({label}): 준비 완료")
            else:
                print(f"  ℹ️  전달 채널({label}): 켜져 있는데 웹훅 없음 — 채팅 미리보기는 됩니다(전송하려면 연결).")

    # 8) 내장 엔진 6종 — 전부 선택 사항. 미설정은 ⚠️ 가 아니라 ℹ️(기능 안내)로만 표시하고
    #    ready 판정에 영향을 주지 않는다(핵심 영업 루프는 엔진 없이도 동작).
    cards, sms = cfg.get("cards", {}), cfg.get("sms", {})
    email_send, cold = cfg.get("email_send", {}), cfg.get("coldmail", {})
    vox, team = cfg.get("vox", {}), cfg.get("team", {})
    engines = [
        ("명함 시트(cards)", bool(cards.get("sheet_webhook_url")),
         "스캔·CRM 등록은 지금도 동작. 구글시트 백업은 apps-script/cards 배포 후 "
         "cards.sheet_webhook_url 연결"),
        ("문자(sms)", bool(sms.get("api_key") and sms.get("sender")),
         "solapi 키·발신번호(sms.api_key/api_secret/sender)를 넣으면 문자 발송"),
        ("메일 발송(email_send)", bool(email_send.get("username") and email_send.get("app_password")),
         "Gmail 앱 비밀번호(email_send.username/app_password)를 넣으면 개별 메일 실발송"),
        ("콜드메일 대장(coldmail)", bool(cold.get("sheet_webhook_url")),
         "apps-script/coldmail 배포 후 coldmail.sheet_webhook_url 연결. "
         "발송은 dispatchTick 전담, 외부 행 기본 PAUSED"),
        ("전화(vox)", bool(vox.get("enabled") and vox.get("api_key")),
         "TryVox API 키(vox.api_key)를 넣으면 아웃바운드·인바운드 콜(/phone-call)"),
        ("팀 관리(team)", bool(team.get("members")),
         "team.members·watch_channels 를 넣으면 팀 현황·리드 배정(/team)"),
    ]
    for label, ok, guide in engines:
        print(f"  {'✅' if ok else 'ℹ️ '} {label}: " + ("설정됨" if ok else f"미설정(선택) — {guide}"))

    # 9) Apps Script 폴더 2종 — 플러그인 패키지에 동봉되는 배포 원본(cards/coldmail).
    #    없으면 설치가 불완전한 것(웹훅 배포를 진행할 수 없음). ready 판정에는 미반영.
    for sub, what in (("cards", "명함 구글시트 웹훅"), ("coldmail", "콜드메일 대장·dispatchTick 발송")):
        d = os.path.join(ROOT, "apps-script", sub)
        if os.path.isdir(d):
            print(f"  ✅ Apps Script({sub}): 동봉됨 — {what} 배포 원본 ({d})")
        else:
            print(f"  ⚠️  Apps Script({sub}) 폴더 없음 — {what} 배포 원본이 빠졌습니다. "
                  "플러그인 재설치를 권장합니다.")

    # 10) 파이썬 버전
    v = sys.version_info
    if (v.major, v.minor) >= (3, 9):
        print(f"  ✅ 파이썬: {v.major}.{v.minor}.{v.micro}")
    else:
        ready = False
        print(f"  ⚠️  파이썬: {v.major}.{v.minor} — 3.9 이상을 권장합니다.")

    print()
    if ready:
        print("설정 완료! 클로드에게 '오늘 누구한테 연락하지'(/today)라고 물어보세요 — 오늘의 행동 큐부터 시작합니다.")
    else:
        print("아직 남은 항목이 있어요. 클로드에게 'AI 영업맨 설정 계속하자'라고 하면 이어서 안내합니다.")


if __name__ == "__main__":
    main()
