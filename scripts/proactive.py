#!/usr/bin/env python3
"""SessionStart 훅 — AI 영업맨이 시키지 않아도 오늘의 행동부터 먼저 챙긴다.
stdout이 세션 컨텍스트로 주입되어 클로드가 먼저 언급/제안한다.
원칙: 개인 인맥·가격 하한·할인 한도 같은 민감 원문은 노출하지 않는다(건수·안내만).
user-scope라 모든 프로젝트에서 뜨므로 proactive.enabled=false 로 끌 수 있다.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import CONFIG_PATH, DATA_DIR, PENDING_NEXT_ACTIONS, QUEUE_DIR, load_config

IMG_EXT = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")


def _read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _pending_next_actions():
    """지난 세션 종료 때 남긴 '다음 행동 없는 리드·기회' 인계 메모.
    읽으면 삭제한다(1회성 — 다음 행동 없이 업무를 종료할 수 없음 원칙의 인계 장치)."""
    if not os.path.exists(PENDING_NEXT_ACTIONS):
        return None
    note = None
    try:
        raw = _read(PENDING_NEXT_ACTIONS).strip()
        try:
            d = json.loads(raw)
            note = (int(d.get("count", 0)), [str(x) for x in d.get("top", [])][:5])
        except Exception:
            if raw:  # 구형/손상 파일도 첫 줄은 살린다
                note = (0, [raw.splitlines()[0][:120]])
    except Exception:
        note = None
    try:
        os.remove(PENDING_NEXT_ACTIONS)
    except Exception:
        pass
    return note


def _crm_next_missing():
    """crm.py 는 병렬 제작 중 — 없거나 실패해도 조용히 빈 목록."""
    try:
        import crm  # noqa: PLC0415
        return list(crm.next_missing() or [])
    except Exception:
        return []


def _new_cards(cfg, days=3):
    """cards_inbox 폴더에 최근 N일 내 들어온 명함 이미지 수. 미설정/오류면 0."""
    inbox = (cfg.get("sources", {}) or {}).get("cards_inbox") or ""
    inbox = inbox.strip()
    if not inbox:
        return 0
    d = os.path.expanduser(inbox)
    if not os.path.isdir(d):
        return 0
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    n = 0
    try:
        for fn in os.listdir(d):
            if fn.lower().endswith(IMG_EXT):
                try:
                    if os.path.getmtime(os.path.join(d, fn)) >= cutoff:
                        n += 1
                except OSError:
                    pass
    except OSError:
        return 0
    return n


def _nudges(cfg):
    today = datetime.date.today().isoformat()
    out = []

    # ① 지난 세션이 남긴 '다음 행동 없는 리드' — 가장 먼저 짚는다
    pending = _pending_next_actions()
    if pending:
        cnt, top = pending
        head = (f"어제 다음 행동 없이 남은 리드 {cnt}건이 있어요" if cnt
                else "어제 다음 행동 없이 남은 리드가 있어요")
        tail = f" (예: {' / '.join(top[:2])})" if top else ""
        out.append(f"{head} — '다음 행동 채우자'부터.{tail}")
    else:
        # ② 지금 기준으로도 다음 행동 없는 리드·기회가 있으면 경고 (핵심 KPI: 보유율 100%)
        missing = _crm_next_missing()
        if missing:
            out.append(f"다음 행동 없는 리드·기회 {len(missing)}건 — 방치된 데이터예요. '다음 행동 채우자'")

    # ③ 오늘의 행동 큐 — 없으면 오늘 연락할 사람부터
    if not os.path.exists(os.path.join(QUEUE_DIR, today + ".md")):
        out.append("오늘의 행동 큐가 아직 없어요 — 오늘 연락할 사람부터 만들까요? ('/today')")

    # ④ 지난 브리핑 미결 항목
    for kind in ("morning", "evening", "weekly"):
        if "확인 필요" in _read(os.path.join(DATA_DIR, f"last_brief_{kind}.md")):
            out.append("지난 브리핑에 '확인 필요' 항목이 남아 있어요 — '브리핑 다시 보여줘'")
            break

    # ⑤ 새 명함 — 등록은 저장이 아니라 다음 행동까지
    n = _new_cards(cfg)
    if n:
        out.append(f"최근 3일 새 명함 {n}장이 등록 대기예요 — 다음 행동까지 만들어요. ('/import-cards')")

    # ⑥ 아침 루틴 미등록
    if not cfg.get("brief", {}).get("routine_enabled", False):
        out.append("아침 8시 '오늘의 행동 큐' 루틴이 아직 없어요 — 걸어드릴까요? ('/routine')")

    return out[:5]


def main():
    # 첫 설치(설정 전) → 선제 온보딩: 클로드가 먼저 설정을 제안하게 한다
    if not os.path.exists(CONFIG_PATH):
        print("📞 [AI 영업맨] 아직 설정 전이에요. 방금 설치하셨다면 먼저 반갑게 인사하고 "
              "'지금 3분 설정을 도와드릴까요?'라고 물어보세요. 원하면 'AI 영업맨 설정 시작하자'로 "
              "직급·권한·판매상품·승인 모드·발신 계정까지 setup 스킬이 쫙 안내하고, 원치 않으면 존중하세요.")
        return
    cfg = load_config(soft=True)
    if cfg.get("proactive", {}).get("enabled", True) is False:
        return
    items = _nudges(cfg)
    if not items:
        return
    print("📞 [AI 영업맨이 먼저 챙긴 것] — 발송·일정 확정은 승인 모드에 따라")
    for it in items:
        print(f"  · {it}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 훅은 어떤 경우에도 세션을 방해하지 않는다
    sys.exit(0)
