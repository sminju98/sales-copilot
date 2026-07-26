#!/usr/bin/env python3
"""명함 연락처를 로컬 CRM(upsert)과 구글시트(Apps Script 웹훅)에 저장한다.

(원본: myeongham-manager plugins/scripts/sheet_append.py — sales-copilot 내장판.
 로컬 저장소가 data/contacts.json 이 아니라 crm/contacts.jsonl 이고,
 시트 설정은 config.json 의 cards 블록을 쓴다: sheet_webhook_url / sheet_webhook_secret)

사용:
  echo '{"name":"김철수","company":"...","phone":"010-1234-5678"}' | python3 sheet_append.py
  python3 sheet_append.py --file contact.json [--dry-run] [--no-local]

- 로컬: 이메일·전화·이름+회사가 일치하면 기존 레코드에 병합(빈 값은 덮지 않음), 아니면 신규 등록.
- 시트: cards.sheet_webhook_url 미설정이면 조용히 건너뛴다(로컬 CRM만으로 완결).
  웹훅은 apps-script/cards/Code.gs 를 구글시트에 배포해 만든다.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import http_request, load_config  # noqa: E402
import crm  # noqa: E402


def upsert_local(contact):
    """(원본 common.upsert_contact 이식) 중복이면 병합, 아니면 신규. (레코드, 방식) 반환."""
    hits = crm.dedupe("contact", contact)
    patch = {k: v for k, v in contact.items() if v not in (None, "")}
    if hits:
        reason, rec = hits[0]
        merged = crm.update("contact", rec.get("id"), patch) or rec
        return merged, f"병합·{reason}"
    rec = dict(patch)
    rec.setdefault("source", "명함")
    rec.setdefault("followups_sent", 0)      # followup_send.py 가 쓰는 카운터
    rec.setdefault("last_contact_at", None)  # 마지막 접촉일(발송 시 갱신)
    return crm.add("contact", rec), "신규"


def append_to_sheet(contact, dry_run=False):
    cfg = load_config(soft=True)
    cards = cfg.get("cards", {})
    url = (cards.get("sheet_webhook_url") or "").strip()
    if not url:
        print("[시트 건너뜀] config cards.sheet_webhook_url 미설정 — 로컬 CRM에만 저장했습니다.")
        print("  (연결: apps-script/cards/Code.gs 배포 → set_config.py cards.sheet_webhook_url=... cards.sheet_webhook_secret=...)")
        return {"ok": True, "skipped": "no_webhook"}
    payload = {"secret": cards.get("sheet_webhook_secret", ""), "contact": contact}
    if dry_run:
        print("[DRY-RUN 시트]", json.dumps(payload, ensure_ascii=False, indent=2))
        return {"ok": True, "dry_run": True}
    res = http_request(url, payload)
    if res.get("error"):
        print(f"[시트 저장 실패] {contact.get('name', '')} -> {res['error']}")
        return res
    print(f"[시트 저장] {contact.get('name', '')} -> {res.get('body')}")
    return res


def main():
    ap = argparse.ArgumentParser(description="명함 연락처를 구글시트+로컬 CRM에 저장")
    ap.add_argument("--file", help="연락처 JSON 파일 경로 (없으면 stdin)")
    ap.add_argument("--dry-run", action="store_true", help="시트 전송 없이 페이로드 미리보기")
    ap.add_argument("--no-local", action="store_true", help="로컬 CRM 저장 건너뛰기")
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            contact = json.load(f)
    else:
        contact = json.load(sys.stdin)
    if not isinstance(contact, dict):
        raise SystemExit("[오류] 연락처 JSON은 객체({...})여야 합니다.")

    if not args.no_local:
        saved, how = upsert_local(contact)
        print(f"[로컬 저장] {saved.get('name', '')} ({how} · id={saved.get('id', '?')})")

    res = append_to_sheet(contact, dry_run=args.dry_run)
    if res.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
