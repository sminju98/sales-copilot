#!/usr/bin/env python3
"""자동 실행(클라우드 루틴 / `/schedule`)에 붙여넣을 프롬프트와 크론식을 보여준다(등록은 하지 않음).

  python3 scripts/schedule_brief.py                  # morning: 오전 8시 오늘의 큐+브리핑(기본)
  python3 scripts/schedule_brief.py --kind evening   # 오후 5시 마감 점검(접촉량·미처리 회신)
  python3 scripts/schedule_brief.py --kind weekly    # 금요일 주간 영업 리포트(행동량·전환량)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config

DOW = {"0": "일", "1": "월", "2": "화", "3": "수", "4": "목", "5": "금", "6": "토", "7": "일"}

RECIPES = {
    "morning": {
        "title": "매일 아침 오늘의 큐+브리핑(오늘 연락할 사람)",
        "cron": "0 8 * * 1-5",
        "cfg_key": "morning_schedule",
        "skill": "today",
        "lines": [
            "[예약 실행] sales-copilot 의 today 스킬(/today)을 실행해 오늘의 행동 큐를 만들고",
            "'사용자 확인 없이' 저장·전송해줘. 신규 접촉 대상·후속 연락 대상·오늘 미팅 브리핑·",
            "승인 대기 발송목록을 우선순위(따뜻한 인맥·강한 구매신호 먼저)로 담아 나만 보기",
            "채널로. 발송은 approval_mode 를 따르고 미설정/draft_only 면 초안까지만.",
            "없는 접촉이력·수치는 지어내지 말고 '확인 필요'로 표시할 것.",
        ],
    },
    "evening": {
        "title": "저녁 마감 점검(오늘 접촉량·미처리 회신·내일 우선순위)",
        "cron": "0 17 * * 1-5",
        "cfg_key": "evening_schedule",
        "skill": "today",
        "lines": [
            "[예약 실행] sales-copilot 의 today 스킬(/today)로 마감 점검을 실행해줘.",
            "오늘 접촉량·미처리 회신·다음 행동 없는 리드·내일 우선순위를 짧게 정리해",
            "나만 보기 채널로 보내라. 미완료 행동은 내일 큐로 재배치하고, 다음 행동 없는",
            "리드·기회는 경고로 표시할 것('다음 행동 없이 업무 종료 금지').",
            "근거 없는 수치는 지어내지 말고 '확인 필요'로.",
        ],
    },
    "weekly": {
        "title": "주간 영업 리포트(행동량·전환량, 금요일)",
        "cron": "0 9 * * 5",
        "cfg_key": "weekly_schedule",
        "skill": "metrics",
        "lines": [
            "[예약 실행] sales-copilot 의 metrics 스킬(/metrics)을 실행해 주간 리포트를 만들어라.",
            "신규 리드 수·첫 접촉·후속 접촉·회신/긍정 회신·예약된 콜·생성된 영업기회·",
            "수주/실주 이유·다음 행동 보유율과 마케팅 보충 필요성을 담아 전송해줘.",
            "민감 항목(개인 인맥·가격 하한·할인 한도)은 나만 보기 채널로만.",
            "근거 없는 수치는 지어내지 말고 '확인 필요'로 표시할 것.",
        ],
    },
}


def human(cron):
    try:
        m, h, dom, mon, dow = cron.split()
    except ValueError:
        return cron
    if "," in h:
        when = " · ".join(f"{int(x):02d}:{int(m):02d}" for x in h.split(","))
    else:
        when = f"{int(h):02d}:{int(m):02d}"
    if dow == "1-5":
        days = "평일(월~금)"
    elif dow == "*":
        days = "매일"
    else:
        days = ", ".join(DOW.get(d, d) for d in dow.split(",")) + "요일"
    return f"{days} {when}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=list(RECIPES), default="morning")
    args = ap.parse_args()
    r = RECIPES[args.kind]

    cfg = load_config(soft=True)
    cron = cfg.get("brief", {}).get(r["cfg_key"]) or r["cron"]

    print(f"=== {r['title']} 예약 안내 ===\n")
    print(f"원하는 시각: {human(cron)}   (크론식: {cron}, 최소 간격 1시간)\n")
    print("● 방법 A — 이 세션에서 바로:")
    print(f'   클로드에게 → "/schedule {human(cron)}에 AI 영업맨 {r["skill"]} 실행"\n')
    print("● 방법 B — claude.ai/code/routines 웹에서 New routine 생성 시, 아래 프롬프트를 넣으세요:")
    print("   (환경변수 SALES_COPILOT_SCHEDULED=1 도 함께 설정하면 더 확실합니다)")
    print("   ┌" + "─" * 58)
    for ln in r["lines"]:
        print("   │ " + ln)
    print("   └" + "─" * 58)
    print("\n● 루틴 환경변수(예약이 로컬 config.json 없이 동작하게 — 권장):")
    print("   SALES_COPILOT_SCHEDULED=1                     ← 무인 실행 표시(확인 없이 전송)")
    print("   SALES_COPILOT_SLACK_PRIVATE=<나만 보기 웹훅>    ← 필수")
    print("   SALES_COPILOT_SLACK_TEAM=<팀 공유 웹훅>        ← 팀 공유 시")
    print("   SALES_COPILOT_CONTEXT=<회사·상품·ICP 컨텍스트 텍스트>  ← 또는 커넥터로 대체")
    print("  routines 웹 UI의 '환경변수/시크릿'에 넣으면 로컬 설정 없이도 전송·컨텍스트가 동작합니다.")
    print("\n⚠️ 그 외:")
    print("  · 메일·캘린더·CRM 읽기는 claude.ai 계정 커넥터로 연결(로컬 CLI MCP는 예약에서 안 보임).")
    print("  · 웹 리서치(리드 발굴·회사 조사)가 필요하면 루틴의 네트워크 접근을 켜세요.")
    print("  · 무인 실행에서도 발송 게이트는 그대로다 — 수신거부·중복 검사 없이 자동 발송 금지.")
    print("  · 전달은 슬랙 Incoming Webhook 이 OAuth 만료 걱정 없이 가장 안정적입니다.")
    print("  · 첫 예약 후에는 실제 슬랙 도착을 반드시 눈으로 확인하세요.")
    if args.kind == "morning":
        print("\n등록을 마쳤으면 클로드에게 '예약 완료'라고 하거나 아래로 표시하세요(세션마다 재권유 안 함):")
        print('  python3 "$CLAUDE_PLUGIN_ROOT/scripts/set_config.py" brief.routine_enabled=true')


if __name__ == "__main__":
    main()
