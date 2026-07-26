#!/usr/bin/env python3
"""UserPromptSubmit 훅 — 요청에서 영업 상황을 감지해 AI 영업맨의 한마디를 선제 주입한다.
관련 없으면 조용히 종료(아무 것도 출력 안 함). 같은 상황은 세션당 1회만(소음 방지)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# (트리거 키워드들, 주입할 영업맨 한마디) — 전략 강의가 아니라 행동 재촉
SITUATIONS = [
    (("명함", "business card", "명함첩", "명함 스캔", "명함 등록"),
     "📞 영업맨: 명함은 저장이 끝이 아니라 **다음 행동까지가 등록**이다 — 판독·CRM·후속 초안까지 "
     "먼저 해놓고 '발송할까요?'만 남기자. ([[import-cards]])"),
    (("콜드메일", "콜드 메일", "cold email", "아웃바운드", "outbound", "첫 메일", "시퀀스"),
     "📞 영업맨: 조사 30분 금지 — 핵심 사실만 확인하고 **오늘 보낼 초안부터** 만들자. "
     "발송 전엔 반드시 수신거부·중복 검사→사실 검사→승인 모드. ([[outreach]])"),
    (("회신", "답장", "답신", "reply"),
     "📞 영업맨: 회신 왔다! 식기 전에 처리 — 긍정이면 그 자리에서 **콜 제안**, 수신거부면 즉시 suppress. "
     "([[classify-reply]])"),
    (("미팅 준비", "미팅 전", "콜 준비", "데모 준비", "상담 준비", "미팅 브리핑"),
     "📞 영업맨: 미팅 전 브리핑 — 접촉 이력·참석자·미팅 목표·예상 반론에 "
     "**약속 금지선(가격 허용범위)**까지 챙겨서 들어가자. ([[prepare-meeting]])"),
    (("회의록", "미팅 끝", "미팅 후", "미팅 정리", "미팅했", "녹취"),
     "📞 영업맨: 회의는 **후속 메일이나 다음 일정이 생겨야 끝**난다 — 회의록에서 약속·BANT 뽑고 "
     "오늘 후속까지 가자. ([[after-meeting]])"),
    (("가격", "할인", "견적", "협상", "제안서", "계약 조건", "계약조건"),
     "📞 영업맨: 권한 넘는 가격·할인은 단정하지 않는다 — escalate_rules 넘으면 **상신[E]**, "
     "견적 발송은 승인부터. ([[deal]])"),
    (("리드", "발굴", "잠재고객", "잠재 고객", "타깃 리스트", "prospect"),
     "📞 영업맨: 조사는 시간 제한 — 끝은 보고서가 아니라 **접촉·소개요청·보류 중 하나**다. "
     "오늘 큐에 올리자. ([[find-leads]])"),
    (("수신거부", "스팸", "unsubscribe", "옵트아웃", "그만 보내", "빼달라"),
     "📞 영업맨: 수신거부는 즉시 **suppress + 전체 발송목록 반영**, 재접촉 금지. 예외 없다. "
     "([[send-policy]])"),
    (("인바운드", "문의가 왔", "문의 왔", "폼 접수", "홈페이지 문의", "도입 문의"),
     "📞 영업맨: 인바운드는 속도가 전부 — 완벽한 답 대신 **먼저 응답**하고 자격확인 질문을 붙이자. "
     "([[inbound]])"),
    (("휴면", "재접촉", "다시 연락", "오래된 리드", "방치", "실주"),
     "📞 영업맨: 무응답은 거절이 아니다 — 새 명분(회사 변화·갱신시점) 찾아 **다른 각도로 다시** 가자. "
     "([[revive]])"),
    (("전화", "통화", "폰콜", "phone call", "아웃바운드 콜", "인바운드 콜", "전화 돌"),
     "📞 영업맨: 전화는 미루면 식는 채널 — Vox 에이전트로 **오늘 바로 콜** 걸자. 발신 전 "
     "suppress-check·통화 가능 시간(quiet hours)·승인 모드 게이트는 그대로. ([[phone-call]])"),
    (("팀원", "팀 현황", "팀 실적", "우리 팀", "팀 관리", "조직 관리", "조직관리", "리드 배정"),
     "📞 영업맨: 팀은 숫자로 관리 — 팀원별 **접촉량·방치 리드·정체 기회**부터 뽑고 다음 행동을 "
     "할당하자. 개인 평가·비교는 나만 보기 전용. ([[team]])"),
]


def _throttle(session, key):
    """세션당 같은 상황은 1회만. 이미 봤으면 True."""
    p = os.path.join(common.DATA_DIR, "_nudge_seen.json")
    try:
        d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except Exception:
        d = {}
    seen = set(d.get(session, []))
    if key in seen:
        return True
    seen.add(key)
    try:
        os.makedirs(common.DATA_DIR, exist_ok=True)
        json.dump({session: sorted(seen)}, open(p, "w", encoding="utf-8"))  # 현재 세션만 유지
    except Exception:
        pass
    return False


def main():
    p = common.proactive_cfg()
    if p is None or p.get("prompt_nudge", True) is False:
        return
    data = common.read_hook_input()
    prompt = (data.get("prompt") or "").lower()
    session = data.get("session_id") or "-"
    if not prompt:
        return

    notes = []
    for i, (keys, msg) in enumerate(SITUATIONS):
        if any(k in prompt for k in keys) and not _throttle(session, i):
            notes.append(msg)

    if not notes:
        return
    common.emit_context("UserPromptSubmit", "\n".join(notes[:3]))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 훅은 어떤 경우에도 세션을 방해하지 않는다
    sys.exit(0)
