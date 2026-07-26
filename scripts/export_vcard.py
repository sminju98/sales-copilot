#!/usr/bin/env python3
"""로컬 CRM 연락처를 리멤버 등에서 가져올 수 있는 vCard(.vcf) / CSV 로 내보낸다.

(원본: myeongham-manager plugins/scripts/export_vcard.py — sales-copilot 내장판.
 로컬 저장소가 data/contacts.json 이 아니라 crm/contacts.jsonl 이고,
 출력 폴더는 config cards.vcard_dir — 미설정이면 data/cards/ — 를 쓴다)

리멤버는 공개 연동 API가 없으므로, 여기서 만든 파일을 리멤버 앱의
'가져오기' 또는 휴대폰 연락처 앱을 거쳐 반입한다.

사용:
  python3 export_vcard.py                # 전체 연락처 내보내기
  python3 export_vcard.py --since 2026-07-01
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA_DIR, load_config  # noqa: E402
import crm  # noqa: E402

CSV_FIELDS = ["name", "company", "title", "phone", "email", "address", "website", "met_at", "created"]


def _company(c):
    """contact 레코드의 회사 필드는 유입 경로에 따라 company/account 로 갈린다 — 모두 흡수."""
    return c.get("company") or c.get("company_name") or c.get("account") or ""


def _created(c):
    return c.get("created") or c.get("created_at") or ""


def out_dir():
    cards = load_config(soft=True).get("cards", {})
    d = (cards.get("vcard_dir") or "").strip()
    return os.path.expanduser(d) if d else os.path.join(DATA_DIR, "cards")


def to_vcard(c):
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    name = c.get("name", "")
    lines.append(f"N:{name};;;")
    lines.append(f"FN:{name}")
    if _company(c):
        lines.append(f"ORG:{_company(c)}")
    if c.get("title"):
        lines.append(f"TITLE:{c['title']}")
    if c.get("phone"):
        lines.append(f"TEL;TYPE=CELL:{c['phone']}")
    if c.get("email"):
        lines.append(f"EMAIL;TYPE=WORK:{c['email']}")
    if c.get("address"):
        lines.append(f"ADR;TYPE=WORK:;;{c['address']};;;;")
    if c.get("website"):
        lines.append(f"URL:{c['website']}")
    note_bits = [b for b in [c.get("met_at"), c.get("memo")] if b]
    if note_bits:
        lines.append("NOTE:" + " / ".join(note_bits))
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="로컬 CRM 연락처 vCard/CSV 내보내기 (리멤버·휴대폰 연락처 반입용)")
    ap.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 등록분만")
    args = ap.parse_args()

    contacts = crm.read_all("contact")
    if args.since:
        contacts = [c for c in contacts if _created(c)[:10] >= args.since]

    if not contacts:
        print("[내보낼 연락처 없음] crm/contacts.jsonl 이 비어 있습니다 (또는 --since 조건에 걸리는 건 없음).")
        return

    d = out_dir()
    os.makedirs(d, exist_ok=True)
    vcf_path = os.path.join(d, "remember_import.vcf")
    csv_path = os.path.join(d, "remember_import.csv")

    with open(vcf_path, "w", encoding="utf-8") as f:
        f.write("\r\n".join(to_vcard(c) for c in contacts) + "\r\n")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for c in contacts:
            row = {k: c.get(k, "") for k in CSV_FIELDS}
            row["company"] = _company(c)
            row["created"] = _created(c)
            w.writerow(row)

    print(f"[완료] {len(contacts)}건 내보냄")
    print(f"  vCard: {vcf_path}")
    print(f"  CSV  : {csv_path}")
    print("리멤버 앱 > 내보내기/가져오기 또는 휴대폰 연락처 앱으로 이 파일을 반입하세요.")


if __name__ == "__main__":
    main()
