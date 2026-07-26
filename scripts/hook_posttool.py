#!/usr/bin/env python3
"""PostToolUse(Write|Edit) 훅 — 영업 작업물 저장을 활동 원장에 남기고,
저장된 파일이 아웃바운드 초안으로 보이면 발송 전 게이트를 리마인드한다(세션당 1회).
영업 산출물이 아니면 조용히 종료."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# 영업 작업물로 볼 파일명 키워드 (활동 원장 기록용)
SALESLIKE = (
    "outreach", "아웃바운드", "outbound", "콜드", "cold", "메일", "mail", "제안", "proposal",
    "견적", "quote", "리드", "lead", "명함", "card", "미팅", "meeting", "회의록", "콜", "call",
    "후속", "follow", "시퀀스", "sequence", "브리핑", "brief", "영업", "sales", "큐", "queue",
)

# 아웃바운드 초안으로 볼 파일명 키워드 (발송 게이트 리마인드용)
OUTBOUNDLIKE = ("outreach", "outbound", "아웃바운드", "콜드", "cold", "coldmail", "후속", "followup", "시퀀스", "sequence")


def is_sales_artifact(path):
    if not path:
        return False
    p = path.lower()
    if "/.sales-copilot/" in p:  # 플러그인이 다루는 데이터 폴더
        return True
    if not (p.endswith(".md") or p.endswith(".html") or p.endswith(".txt")):
        return False
    base = os.path.basename(p)
    return any(k in base for k in SALESLIKE)


def looks_outbound(path, content):
    """경로·내용으로 '대외 발송용 초안'인지 판별. 확신 없으면 False(소음 방지)."""
    base = os.path.basename((path or "").lower())
    if any(k in base for k in OUTBOUNDLIKE):
        return True
    c = (content or "")[:4000].lower()
    if not c:
        return False
    has_subject = ("제목:" in c) or ("subject:" in c)
    has_recipient = ("수신:" in c) or ("받는사람" in c) or ("받는 사람" in c) or ("to:" in c)
    return has_subject and (has_recipient or "안녕하세요" in c or "드립니다" in c)


def gate_seen(session):
    """발송 게이트 리마인드는 세션당 1회만. 이미 봤으면 True."""
    p = os.path.join(common.DATA_DIR, "_gate_seen.json")
    try:
        d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except Exception:
        d = {}
    if d.get(session):
        return True
    try:
        os.makedirs(common.DATA_DIR, exist_ok=True)
        json.dump({session: True}, open(p, "w", encoding="utf-8"))  # 현재 세션만 유지
    except Exception:
        pass
    return False


def main():
    p = common.proactive_cfg()
    if p is None or p.get("review_on_save", True) is False:
        return
    data = common.read_hook_input()
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("path") or ""
    content = ti.get("content") or ti.get("new_string") or ""
    if not is_sales_artifact(path):
        return

    common.log_activity("작업물 저장", path)  # 저녁 마감·활동 로그의 원천

    if not looks_outbound(path, content):
        return
    session = data.get("session_id") or "-"
    if gate_seen(session):
        return
    name = os.path.basename(path)
    common.emit_context(
        "PostToolUse",
        f"📞 영업맨: 방금 저장한 **{name}** 은 아웃바운드 초안으로 보인다. 발송 전 게이트 순서대로 — "
        f"①수신거부·중복 검사(`crm.py suppress-check`) ②사실 오류·과장 검사(사실 vs 추론 구분, 단정 금지) "
        f"③승인 모드(approval_mode) 적용. **DRAFT ONLY거나 미설정이면 절대 자동 발송 금지.**",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 훅은 어떤 경우에도 세션을 방해하지 않는다
    sys.exit(0)
