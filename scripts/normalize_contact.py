#!/usr/bin/env python3
"""콜드메일 contact 결정론 정규화 (Python 표준 라이브러리만).

(원본: salesfactory-plugin skills/salesfactory/scripts/normalize_contact.py — sales-copilot 이식판.
 W2c에 맞춰 legalBasis 에 SALES_PROPOSAL_1TO1(1:1 개별 영업 제안 — 08 판정)을 추가하고,
 국내 경고 문구를 '무조건 hard block'에서 '판정 필요'로 바꿨다. 스키마는
 apps-script/coldmail/Sheets.gs HEADERS.contacts 와 일치를 유지한다.)

Claude가 발굴·리서치한 raw contact(JSON)를 받아, contacts 시트에 적재 가능한
결정론 필드로 정리한다. "LLM은 판단, 결정론 코드는 운영" 원칙(references/04)의 운영 파트.

여기서 하는 것(전부 결정론):
  - contactId 생성 (멱등 키: 이메일 있으면 base64(lower(email)), 없으면 회사+직책 해시)
  - email 정규화 + 형식 검증 (없으면 빈 값 — 국내 트랙은 대표번호/문의폼으로)
  - locale 판정 (country 기반. ⚠️ 이름으로 국적 추론 안 함 — KOREAN_SURNAMES 이식 금지)
  - legalBasis 기본 NONE 강제 + 증빙 필드 검증 (SALES_PROPOSAL_1TO1 은 hookContext 필수)
  - sourceUrl 없으면 발송 부적격 표시

여기서 안 하는 것:
  - 이메일 주소 추측 생성 (형사 리스크 — 철칙 3). 입력에 없으면 만들지 않는다.
  - 이름→민족/국적 추론 (프로파일링 리스크 — references/04).

사용: echo '<raw json>' | python3 normalize_contact.py   또는   python3 normalize_contact.py raw.json
출력: contacts 시트 스키마에 맞춘 정규화 JSON (stdout)
"""
import sys, json, re, base64, hashlib
from datetime import datetime, timezone

# contacts 시트 컬럼 (apps-script/coldmail/Sheets.gs HEADERS.contacts 와 일치 유지)
CONTACT_FIELDS = [
    "contactId", "email", "name", "company", "title", "country",
    "locale", "audience", "sourceType", "sourceUrl", "matchedSnippet",
    "legalBasis", "dealClosedAt", "dealItem", "hookContext", "poolStatus", "createdAt",
]

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
FREE_MAIL = {  # 개인 웹메일 — 담당자 개인주소일 확률↑, 발송 대상서 제외 신호
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com",
    "naver.com", "daum.net", "hanmail.net", "kakao.com", "nate.com",
}
KR_TLD = re.compile(r"\.kr$|\.co\.kr$|\.or\.kr$|\.re\.kr$")
# W2c: SALES_PROPOSAL_1TO1 = 08 '영업 메일 판정' 통과(1:1 개별 거래 제안 — 광고성 정보 아님 판정).
#      나머지는 광고성 정보의 동의 경로. NONE 은 국내 발송 차단.
LEGAL_BASES = {"NONE", "SALES_PROPOSAL_1TO1", "TRANSACTION_6M", "BUSINESS_CARD_B2B", "EXPLICIT_CONSENT"}


def norm_email(raw):
    e = (raw or "").strip().lower()
    if not e or not EMAIL_RE.match(e):
        return ""  # 추측 생성 금지 — 유효하지 않으면 빈 값
    return e


def make_contact_id(email, company, title):
    """멱등 결정론 키. 이메일 우선, 없으면 회사+직책 해시(문의폼·전화 트랙)."""
    if email:
        return "e_" + base64.urlsafe_b64encode(email.encode()).decode().rstrip("=")
    seed = (company or "").strip().lower() + "|" + (title or "").strip().lower()
    return "c_" + hashlib.sha1(seed.encode()).hexdigest()[:16]


def resolve_locale(country, explicit):
    """country 기반 결정론. ⚠️ 이름 토큰으로 국적 추론하지 않는다(프로파일링 금지)."""
    if explicit in ("ko", "en"):
        return explicit
    c = (country or "").strip().lower()
    if "korea" in c or c in ("kr", "대한민국", "한국"):
        return "ko"
    if c:
        return "en"
    return ""  # 불명 — 발굴 단계에서 회사 소재국/사이트 언어로 채워라


def is_korean(email, country, locale):
    dom = email.split("@")[1] if "@" in email else ""
    if "korea" in (country or "").lower():
        return True
    if locale == "ko":
        return True
    if dom and KR_TLD.search(dom):
        return True
    if dom in {"naver.com", "daum.net", "hanmail.net", "kakao.com", "nate.com"}:
        return True
    return False


def normalize(raw):
    email = norm_email(raw.get("email"))
    country = raw.get("country") or ""
    locale = resolve_locale(country, raw.get("locale"))
    company = raw.get("company") or ""
    title = raw.get("title") or ""
    hook = raw.get("hookContext") or ""

    # legalBasis: 기본 NONE 강제. 증빙 없으면 NONE 으로 강등(국내 발송 게이트에서 차단됨)
    basis = str(raw.get("legalBasis") or "NONE").upper()
    if basis not in LEGAL_BASES:
        basis = "NONE"
    deal_closed = raw.get("dealClosedAt") or ""
    deal_item = raw.get("dealItem") or ""
    if basis == "TRANSACTION_6M" and not (deal_closed and deal_item):
        basis = "NONE"  # 증빙 불완전 → 강등
    if basis == "SALES_PROPOSAL_1TO1" and not hook:
        basis = "NONE"  # 1:1 개별 작성 근거(hookContext) 없으면 판정 불성립 → 강등 (08 ①)

    warnings = []
    dom = email.split("@")[1] if "@" in email else ""
    if email and dom in FREE_MAIL:
        warnings.append("free_webmail(개인주소 가능성 — 발송 대상 재검토)")
    if not raw.get("sourceUrl"):
        warnings.append("sourceUrl 없음(발송 게이트에서 차단됨 — 공개 출처 근거 필요)")
    if is_korean(email, country, locale) and basis == "NONE":
        warnings.append(
            "국내 수신자 + legalBasis NONE → 발송 차단. 08 '영업 메일 판정' 통과 시 "
            "SALES_PROPOSAL_1TO1(hookContext 필수)로 적재, 아니면 동의 경로 또는 문의폼·전화 리포트로"
        )

    out = {
        "contactId": make_contact_id(email, company, title),
        "email": email,
        "name": raw.get("name") or "",
        "company": company,
        "title": title,
        "country": country,
        "locale": locale,
        "audience": raw.get("audience") if raw.get("audience") in ("agency", "brand") else "",
        "sourceType": raw.get("sourceType") or "",
        "sourceUrl": raw.get("sourceUrl") or "",
        "matchedSnippet": raw.get("matchedSnippet") or "",
        "legalBasis": basis,
        "dealClosedAt": deal_closed,
        "dealItem": deal_item,
        "hookContext": hook,
        "poolStatus": "backlog",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    return out, warnings


def main():
    src = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    data = json.loads(src)
    items = data if isinstance(data, list) else [data]
    results, all_warn = [], []
    for raw in items:
        norm, warnings = normalize(raw)
        results.append(norm)
        if warnings:
            all_warn.append({"contactId": norm["contactId"], "warnings": warnings})
    json.dump({"contacts": results, "warnings": all_warn}, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
