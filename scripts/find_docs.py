#!/usr/bin/env python3
"""로컬 PC에서 영업 자료(명함 사진·제안서·견적서·고객사 자료)를 자동으로 찾아 목록으로 보여준다.

명함은 안 올려도 "보통 반드시 어딘가 있다" — 바탕화면/문서/다운로드와 명함 폴더
(config `sources.cards_inbox`)를 훑어 후보를 카테고리별로 정리한다. **읽기 전용**:
파일 내용을 열거나 어디로 보내지 않고, 경로·크기·수정일만 나열한다
(명함 등록은 import-cards, 자료 반영은 context 스킬이 사용자가 고른 뒤에 한다).

  python3 scripts/find_docs.py                 # 기본 위치 스캔, 사람이 읽는 표
  python3 scripts/find_docs.py --json          # 기계용 JSON(스킬이 파싱)
  python3 scripts/find_docs.py --root ~/명함   # 추가 위치도 스캔
  python3 scripts/find_docs.py --days 365      # 최근 N일 안에 수정된 것만(기본 1095=3년)
"""
import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config

# 명함 사진 확장자 / 문서(제안·견적·고객 자료) 확장자.
IMG_EXTS = {".jpg", ".jpeg", ".png", ".heic"}
DOC_EXTS = {
    ".pdf", ".ppt", ".pptx", ".key", ".doc", ".docx", ".hwp", ".hwpx",
    ".xls", ".xlsx", ".xlsm", ".csv", ".numbers", ".pages", ".md",
}
EXTS = IMG_EXTS | DOC_EXTS

# 명함 사진 판정: 파일명에 아래 키워드 포함(IMG_1234.jpg 같은 촬영본 포함),
# 또는 cards_inbox 폴더 안의 이미지 전체.
CARD_KEYWORDS = ["명함", "card", "img"]

# 문서 파일명 키워드 → 카테고리. 소문자로 비교. (한글은 그대로 부분일치.)
CATEGORY_KEYWORDS = [
    ("proposal", ["제안", "제안서", "proposal", "소개서", "회사소개", "company profile",
                  "one pager", "onepager", "브로셔", "brochure", "카탈로그", "catalog"]),
    ("quote", ["견적", "quote", "quotation", "estimate", "단가", "가격표", "price"]),
    ("customer", ["고객", "customer", "client", "발주", "rfp", "rfq", "요구사항", "계약"]),
]
CATEGORY_LABEL = {
    "business_card": "📇 명함 사진",
    "proposal": "📄 제안서·회사소개",
    "quote": "💰 견적서·가격",
    "customer": "🏢 고객사 자료",
}
CATEGORY_ORDER = ["business_card", "proposal", "quote", "customer"]

# 스캔에서 통째로 제외할 디렉토리 이름(시스템/캐시/코드 저장소 등).
SKIP_DIRS = {
    "library", "node_modules", ".git", "__pycache__", ".cache", "caches",
    ".npm", ".venv", "venv", "env", ".Trash", "trash", ".gradle", ".m2",
    "applications", "movies", "music", "public", ".pm-copilot",
    ".business-copilot", ".sales-copilot", "dist", "build", ".next", "vendor",
}
MAX_HITS = 200          # 후보 상한(너무 많으면 무의미).
MAX_DEPTH = 6           # 루트 기준 최대 탐색 깊이.
TIME_BUDGET_SEC = 12    # 전체 스캔 시간 예산(넘으면 조기 종료).


def categorize(name_lower, ext, in_cards_inbox):
    """파일명·확장자로 카테고리 판정. 해당 없으면 None(스킵)."""
    if ext in IMG_EXTS:
        if in_cards_inbox or any(kw in name_lower for kw in CARD_KEYWORDS):
            return "business_card"
        return None
    for cat, kws in CATEGORY_KEYWORDS:
        if any(kw in name_lower for kw in kws):
            return cat
    return None


def human_size(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def cards_inbox_path():
    """config `sources.cards_inbox` 폴더(설정돼 있고 실제로 있으면). 없으면 ''."""
    try:
        p = load_config(soft=True).get("sources", {}).get("cards_inbox", "")
    except Exception:
        return ""
    p = os.path.abspath(os.path.expanduser(p)) if p else ""
    return p if p and os.path.isdir(p) else ""


def default_roots(cards_inbox):
    home = os.path.expanduser("~")
    roots = [os.path.join(home, d) for d in ("Desktop", "Documents", "Downloads")]
    # 한글 macOS는 데스크탑/문서가 영문 심볼릭이라 위로 커버됨.
    roots = [r for r in roots if os.path.isdir(r)]
    if cards_inbox and cards_inbox not in roots:
        roots.append(cards_inbox)
    return roots


def scan(roots, cutoff_ts, cards_inbox):
    hits = []
    start = time.time()
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() - start > TIME_BUDGET_SEC:
                return hits, True
            depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= MAX_DEPTH:
                dirnames[:] = []
                continue
            # 숨김/제외 디렉토리 프루닝
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d.lower() not in SKIP_DIRS]
            in_inbox = bool(cards_inbox) and (dirpath == cards_inbox
                                              or dirpath.startswith(cards_inbox + os.sep))
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in EXTS:
                    continue
                cat = categorize(fn.lower(), ext, in_inbox)
                if not cat:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_mtime < cutoff_ts:
                    continue
                hits.append({
                    "path": full,
                    "name": fn,
                    "category": cat,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
                if len(hits) >= MAX_HITS:
                    return hits, True
    return hits, False


def main():
    ap = argparse.ArgumentParser(description="로컬 영업 자료 탐색(명함·제안서·견적·고객 자료)")
    ap.add_argument("--root", action="append", default=[], help="추가 스캔 위치(여러 번 가능)")
    ap.add_argument("--days", type=int, default=1095, help="최근 N일 내 수정된 것만(기본 3년)")
    ap.add_argument("--json", action="store_true", help="기계용 JSON 출력")
    args = ap.parse_args()

    cards_inbox = cards_inbox_path()
    roots = default_roots(cards_inbox) + args.root
    if not roots:
        print("스캔할 위치가 없습니다. --root 로 폴더를 지정하세요.")
        return
    cutoff_ts = time.time() - args.days * 86400

    hits, truncated = scan(roots, cutoff_ts, cards_inbox)
    # 카테고리 → 최신순 정렬.
    for h in hits:
        h["mtime_str"] = datetime.date.fromtimestamp(h["mtime"]).isoformat()
        h["size_str"] = human_size(h["size"])
    hits.sort(key=lambda h: (CATEGORY_ORDER.index(h["category"]), -h["mtime"]))

    if args.json:
        print(json.dumps({"roots": [os.path.expanduser(r) for r in roots],
                          "cards_inbox": cards_inbox,
                          "count": len(hits), "truncated": truncated,
                          "hits": hits}, ensure_ascii=False, indent=2))
        return

    print("=== 로컬에서 찾은 영업 자료 후보 ===")
    print(f"(스캔 위치: {', '.join(os.path.expanduser(r) for r in roots)} · 최근 {args.days}일)\n")
    if not hits:
        print("자료를 못 찾았어요. 다른 폴더에 있다면 클로드에게 위치를 알려주거나,")
        print("  python3 scripts/find_docs.py --root <폴더경로>  로 다시 찾아보세요.")
        print("명함 폴더를 지정해두면 다음부터 자동으로 봅니다: set_config.py sources.cards_inbox=<경로>")
        return

    by_cat = {}
    for h in hits:
        by_cat.setdefault(h["category"], []).append(h)
    for cat in CATEGORY_ORDER:
        rows = by_cat.get(cat)
        if not rows:
            continue
        print(f"{CATEGORY_LABEL[cat]}  ({len(rows)}개)")
        for h in rows[:12]:
            print(f"  · {h['name']}  —  {h['size_str']}, 수정 {h['mtime_str']}")
            print(f"      {h['path']}")
        if len(rows) > 12:
            print(f"  … 외 {len(rows)-12}개")
        print()
    if truncated:
        print("(후보가 많아 일부만 표시 — 필요하면 --days 를 줄여 좁히세요.)")
    print("다음: 명함 사진은 클로드에게 '명함 등록하자'(import-cards)로 연락처·다음 행동까지 만들고,")
    print("  제안서·고객 자료는 '영업 컨텍스트에 반영하자'(context)로 넘기세요.")
    print("  (내용은 이 스캔에서 열지 않았습니다 — 경로만 나열.)")


if __name__ == "__main__":
    main()
