#!/usr/bin/env python3
"""생성된 영업 브리핑을 로컬(data/briefs/)에 저장한다.

kind: morning(아침 행동 큐) · evening(저녁 마감) · weekly(주간 영업 리포트) ·
      meeting(미팅 전 브리핑) · team(팀 공유용) · private(나만 보기)

사용:
  python3 scripts/save_brief.py --kind morning --file draft.md
  echo "본문" | python3 scripts/save_brief.py --kind evening
  python3 scripts/save_brief.py --kind weekly --date 2026-07-26 --file draft.md
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA_DIR, routine_guard, save_brief


def main():
    routine_guard()   # 예약인데 설정이 덜 됐으면 조용히 끝낸다(오류 아님)
    ap = argparse.ArgumentParser(description="영업 브리핑 로컬 저장")
    ap.add_argument("--kind", choices=["morning", "evening", "weekly", "meeting", "team", "private"],
                    default="morning")
    ap.add_argument("--file")
    ap.add_argument("--text")
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘)")
    args = ap.parse_args()

    if args.text is not None:
        text = args.text
    elif args.file:
        path = args.file if os.path.isabs(args.file) else os.path.join(DATA_DIR, args.file)
        with open(path, encoding="utf-8") as f:
            text = f.read()
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise SystemExit("저장할 내용이 없습니다(--text/--file/stdin).")

    path = save_brief(text, date=args.date, kind=args.kind)
    print(f"[저장됨] {path}")


if __name__ == "__main__":
    main()
