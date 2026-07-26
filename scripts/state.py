#!/usr/bin/env python3
"""콜드메일 대장 상태 조회 단일 창구 (Python 표준 라이브러리만).

(원본: salesfactory-plugin skills/salesfactory/scripts/state.py — sales-copilot 이식판.
 설정을 ~/.salesfactory/config.local.json 대신 sales-copilot config.json 의 coldmail 블록에서
 읽도록 매핑했다. 레거시 설정 파일은 폴백으로 계속 지원한다.)

outreach 철칙 1: "오늘 몇 통 보냈는지·이미 보낸 상대인지를 네 기억으로 판단하지 마라.
반드시 state.py로 시트에 물어라." — 이 파일이 그 창구다.

■ 아키텍처
  Claude(플러그인)는 사용자 Google Sheets를 직접 못 읽는다(OAuth·googleapis 부담).
  대신 Apps Script(apps-script/coldmail/Code.gs)가 웹앱 엔드포인트로 상태를 JSON으로 내주고,
  state.py는 stdlib urllib로 그 URL에 POST 질의한다. Google 인증은 전부 Apps Script 안.

■ 성격 (중요)
  state.py는 "안전장치"가 아니라 "최적화 계층"이다. 실제 중복 발송·상한·suppression 강제는
  Code.gs dispatchTick()의 결정론 게이트가 한다. state.py가 못 읽으면(온보딩 전·네트워크 실패)
  {"ok": false, "degraded": true}를 반환하고 Claude는 그냥 진행한다 — 발송 시 Code.gs가 막는다.
  즉 state.py 실패가 사고로 이어지지 않는다.

■ 설정 (references/09-setup.md 6번 단계에서 저장)
  1순위: config.json coldmail 블록 — sheet_webhook_url(웹앱 …/exec URL),
         sheet_webhook_secret(또는 token — Apps Script Script Properties 의 SF_API_TOKEN 값)
         저장: python3 scripts/set_config.py coldmail.sheet_webhook_url=… coldmail.sheet_webhook_secret=…
  2순위(레거시 폴백): 환경변수 SALESFACTORY_CONFIG 경로 또는 ~/.salesfactory/config.local.json
         내용 {"webAppUrl": "...", "token": "..."} — 기존 세일즈팩토리 사용자 이전용.

■ 대장(drafts) status 컨벤션 (W2d — Code.gs dispatchTick 실제 동작 기준)
  pending_review(=PAUSED 개념: 외부 주소 행의 기본 적재 상태 — dispatchTick이 절대 집지 않음)
  approved(=READY: dispatchTick이 발송하는 유일한 상태 — 사용자 승인 후에만 전환)
  sending | sent | failed | cancelled (엔진이 전이). 어떤 경우에도 AI가 approved 로 직접
  적재하지 않는다 — 예외는 09-setup 7번의 '내 주소 내부 테스트 1건'뿐.

■ Apps Script 측 계약 (Code.gs doPost 가 이 형태로 응답)
  요청  : POST {webAppUrl}   body(JSON)= {"token": "...", "action": "...", ...params}
  액션/응답:
    summary       → {ok, sentToday, capToday, remaining, lastSuccessAt, queuePending, accountType}
    sent_today    → {ok, sentToday}
    check         → {ok, email, contacted: bool, suppressed: bool, poolStatus}   (param: email)
    followup_due  → {ok, due: [{contactId, dueAt}]}
    config        → {ok, config: {...}}
  (email 은 URL 쿼리가 아니라 POST 본문으로 — 개인정보 URL 노출 금지)

■ CLI (Claude bash 에서 호출)
    python3 state.py summary
    python3 state.py sent_today
    python3 state.py check someone@example.com
    python3 state.py followup_due
    python3 state.py config
  출력: 항상 JSON 한 줄 (stdout). 실패도 JSON({"ok": false, ...})로 — 파싱 실패 유발 안 함.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import common  # sales-copilot 공통 설정 로더
except ImportError:  # 단독 복사돼 쓰여도 레거시 폴백으로 동작
    common = None

LEGACY_CONFIG = os.path.expanduser("~/.salesfactory/config.local.json")
TIMEOUT_SEC = 15


def _emit(obj):
    """항상 JSON 한 줄로 출력. degraded/실패도 예외 대신 JSON."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")


def load_endpoint():
    """웹앱 URL·토큰을 로드. 없거나 불완전하면 None (→ degraded 진행).

    1순위: sales-copilot config.json coldmail 블록 (sheet_webhook_url + sheet_webhook_secret|token)
    2순위: 레거시 세일즈팩토리 설정 (SALESFACTORY_CONFIG 또는 ~/.salesfactory/config.local.json)
    """
    if common is not None:
        try:
            cold = common.load_config(soft=True).get("coldmail", {}) or {}
        except Exception:
            cold = {}
        url = (cold.get("sheet_webhook_url") or "").strip()
        token = (cold.get("sheet_webhook_secret") or cold.get("token") or "").strip()
        if url and token:
            return {"webAppUrl": url, "token": token}
    path = os.environ.get("SALESFACTORY_CONFIG") or LEGACY_CONFIG
    try:
        with open(path, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not legacy.get("webAppUrl") or not legacy.get("token"):
        return None
    return legacy


def call(action, params=None):
    """Apps Script 웹앱에 POST 질의. 실패는 예외 대신 degraded 딕셔너리로.
    반환: dict. 성공 시 {"ok": True, ...}, 실패 시 {"ok": False, "degraded": True, "reason": ...}
    """
    cfg = load_endpoint()
    if cfg is None:
        return {
            "ok": False,
            "degraded": True,
            "reason": "not_configured",
            "hint": "콜드메일 온보딩(references/09-setup.md) 미완료 — set_config.py 로 "
                    "coldmail.sheet_webhook_url·coldmail.sheet_webhook_secret 설정. "
                    "Claude는 이대로 진행하고 발송 시 Code.gs가 가드.",
        }

    body = {"token": cfg["token"], "action": action}
    if params:
        body.update(params)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cfg["webAppUrl"],
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {"ok": False, "degraded": True, "reason": "bad_response"}
        parsed.setdefault("ok", True)
        return parsed
    except urllib.error.HTTPError as e:
        return {"ok": False, "degraded": True, "reason": "http_%s" % e.code}
    except (urllib.error.URLError, TimeoutError) as e:
        return {"ok": False, "degraded": True, "reason": "unreachable",
                "detail": str(e.reason if hasattr(e, "reason") else e)}
    except json.JSONDecodeError:
        return {"ok": False, "degraded": True, "reason": "unparseable_response"}


# ── 편의 함수 (import 해서도 사용) ───────────────────────────
def summary():
    """references/09 의 1줄 리포트 재료: 오늘 발송/상한/남은큐/마지막 성공."""
    return call("summary")


def sent_today():
    return call("sent_today")


def check(email):
    """이미 보낸 상대인가 / suppression 인가. email 은 POST 본문으로만 전송(URL 노출 금지).
    ⚠️ 이 검사와 별개로 로컬 수신거부도 항상 함께 본다: crm.py suppress-check --email <e>"""
    email = (email or "").strip().lower()
    if not email:
        return {"ok": False, "reason": "empty_email"}
    return call("check", {"email": email})


def followup_due():
    return call("followup_due")


def get_config():
    return call("config")


# ── CLI ──────────────────────────────────────────────────────
_ACTIONS = {
    "summary": lambda a: summary(),
    "sent_today": lambda a: sent_today(),
    "check": lambda a: check(a[0]) if a else {"ok": False, "reason": "email 인자 필요: state.py check <email>"},
    "followup_due": lambda a: followup_due(),
    "config": lambda a: get_config(),
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        _emit({"ok": False, "usage": "state.py {%s} [args]" % "|".join(_ACTIONS)})
        return
    action = argv[0]
    fn = _ACTIONS.get(action)
    if not fn:
        _emit({"ok": False, "reason": "unknown_action", "action": action, "known": list(_ACTIONS)})
        return
    _emit(fn(argv[1:]))


if __name__ == "__main__":
    main(sys.argv[1:])
