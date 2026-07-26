#!/usr/bin/env python3
"""반자동 셋업 — 설정 질문 11개(SPEC §1)의 답을 인자로 주면 config 생성·검증·테스트 발송까지 한 번에.

  python3 scripts/quicksetup.py                      # 인자 없이: 질문 11개 안내 출력(클로드가 대화로 진행)
  python3 scripts/quicksetup.py --rank rep --sell-what "중소 이커머스에 AI 광고 소재 구독" \
      --approval-mode per_item [--private "https://hooks.slack.com/services/..."] [--no-test]

설정은 ~/.sales-copilot/ 에 저장되어 플러그인을 업데이트/재설치해도 유지된다.
값 하나씩 고치려면 set_config.py 를 쓴다. 웹훅은 SALES_COPILOT_SLACK_PRIVATE/TEAM 환경변수로도 받는다.
(슬랙이 없어도 됩니다 — 이 단계 없이 채팅 미리보기만으로도 AI 영업맨은 동작합니다.)
"""
import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (CONFIG_PATH, CONTEXT_DIR, CRM_DIR, DATA_DIR, EXAMPLE_CONFIG, QUEUE_DIR,
                    ROOT, env_webhook, http_request)

SLACK_PREFIX = "https://hooks.slack.com/"

# 질문 1~11의 선택지 값 — config.example.json 의 키·값과 1:1 (스킬·훅이 이 값으로 분기한다).
RANKS = ("founder", "exec", "sales_head", "team_lead", "rep", "other_dept", "solo", "agency")
ACCOUNTS_SCOPES = ("all", "industry", "region", "product", "assigned", "network", "personal")
INFO_SCOPES = ("all", "team", "own", "personal", "public")
SEND_ACTIONS = ("find_leads", "register_contacts", "draft_email", "auto_send_email",
                "auto_followup", "propose_meeting", "confirm_schedule", "send_proposal",
                "quote_price", "negotiate_discount", "negotiate_contract")
APPROVAL_MODES = ("auto", "batch", "per_item", "draft_only", "escalate")
SEND_AS = ("self", "ceo", "team", "company", "account_owner", "by_case")
PERSONAL_POLICIES = ("all", "selected", "signal_only", "private")

GUIDE = """=== AI 영업맨(Sales Copilot) 반자동 설정 ===
클로드에게 "영업맨 설정 시작하자"라고 하면 아래 11개를 대화로 묻고 대신 저장합니다.
직접 한 번에 채우려면 맨 아래 실행 예를 쓰세요.

[설정 질문 11개 — 직급·직책·권한부터 (SPEC §1)]
 1. 어떤 자격으로 쓰나 (--rank): founder(대표·공동창업자) / exec(C-Level·임원) /
    sales_head(영업본부장·총괄) / team_lead(영업팀장·리드) / rep(영업팀원·AE·BD·SDR) /
    other_dept(사업기획·마케팅·CS 등 타부서) / solo(개인사업자·프리랜서) / agency(외부 영업대행)
 2~3. 직급·직책과 실제 담당 업무 (--title): 예 "과장 / 신규 영업 담당"
 4. 무엇을 누구에게 파나 (--sell-what): 한 줄. 예 "중소 이커머스에 AI 광고 소재 구독"
 5. 담당 고객 범위 (--accounts-scope): all(회사 전체) / industry(특정 산업) / region(특정 지역) /
    product(특정 상품) / assigned(배정받은 계정) / network(본인 인맥) / personal(개인 고객)
 6. 볼 수 있는 정보 범위 (--info-scope): all(전사 연락처·영업기회) / team(소속 팀) /
    own(본인 담당) / personal(개인 연락처) / public(공개된 정보만)
 7. 직접 실행 가능한 행동 (--send-scope, 쉼표로 여러 개): find_leads(리드 발굴) /
    register_contacts(연락처 등록) / draft_email(메일 초안) / auto_send_email(메일 자동 발송) /
    auto_followup(후속 자동 발송) / propose_meeting(전화·미팅 제안) / confirm_schedule(일정 확정) /
    send_proposal(제안서 발송) / quote_price(견적·가격 제안) / negotiate_discount(할인 협상) /
    negotiate_contract(계약조건 협상)
 8. 외부 발송 승인 방식 (--approval-mode): auto(승인 없이 자동) / batch(묶어서 승인) /
    per_item(건별 승인) / draft_only(초안만 작성) / escalate(권한 넘으면 상신)
 9. 누구의 이름으로 연락하나 (--send-as): self(본인) / ceo(대표) / team(영업팀 공용) /
    company(회사 대표메일) / account_owner(계정별 담당자) / by_case(상황별 선택)
10. 상급자가 직접 나서야 하는 기준 (--escalate-rules, 쉼표): 예 "예상 계약금액 3천만원 이상,
    대기업·투자사·전략적 파트너, 할인율 10% 초과, 계약조건 변경, 부정적 이슈 발생"
11. 개인 명함·인맥을 회사 영업에 활용해도 되나 (--personal-contacts): all(전부 가능) /
    selected(사용자가 고른 연락처만) / signal_only(기회만 알리고 자동 연락 금지) / private(완전 비공개)

[한 번에 실행 예]
  python3 scripts/quicksetup.py --name "홍길동" --company "우리회사" \\
      --rank rep --title "대리 / 신규영업" --sell-what "중소 이커머스에 AI 광고 소재 구독" \\
      --accounts-scope assigned --info-scope own \\
      --send-scope find_leads,register_contacts,draft_email,propose_meeting \\
      --approval-mode per_item --send-as self \\
      --escalate-rules "예상 계약금액 3천만원 이상, 할인율 10% 초과" \\
      --personal-contacts selected \\
      --private "https://hooks.slack.com/services/..."   # 나만 보기 채널 웹훅(선택)

값 하나만 고칠 땐: python3 scripts/set_config.py me.approval_mode=per_item"""


def valid_webhook(url):
    return bool(url) and url.startswith(SLACK_PREFIX)


def split_list(raw):
    return [s.strip() for s in re.split(r"[|,]", raw or "") if s.strip()]


def send_test(url, label):
    res = http_request(url, payload={"text": f"✅ AI 영업맨 연결 완료 — 이 채널은 *[{label}]* 입니다."})
    if res.get("error") or (res.get("status") or 0) >= 300:
        return False, res.get("error") or f"HTTP {res.get('status')}"
    return True, "ok"


def main():
    ap = argparse.ArgumentParser(description="AI 영업맨 반자동 셋업 (설정 질문 11개)")
    ap.add_argument("--name", default="", help="내 이름")
    ap.add_argument("--company", default="", help="회사 이름")
    ap.add_argument("--rank", choices=RANKS, help="질문1: 사용 자격")
    ap.add_argument("--title", default="", help="질문2~3: 직급·직책·실제 담당 업무")
    ap.add_argument("--sell-what", default="", help="질문4: 무엇을 누구에게 파는지 한 줄")
    ap.add_argument("--accounts-scope", choices=ACCOUNTS_SCOPES, help="질문5: 담당 고객 범위")
    ap.add_argument("--info-scope", choices=INFO_SCOPES, help="질문6: 정보 열람 범위")
    ap.add_argument("--send-scope", default="", help="질문7: 실행 가능 행동(쉼표 구분)")
    ap.add_argument("--approval-mode", choices=APPROVAL_MODES, help="질문8: 외부 발송 승인 방식")
    ap.add_argument("--send-as", choices=SEND_AS, help="질문9: 누구 이름으로 연락하나")
    ap.add_argument("--sender-email", default="", help="질문9 보조: 발신 메일 주소")
    ap.add_argument("--signature", default="", help="질문9 보조: 메일 서명")
    ap.add_argument("--escalate-rules", default="", help="질문10: 상급자 개입 기준(쉼표 구분)")
    ap.add_argument("--personal-contacts", choices=PERSONAL_POLICIES,
                    help="질문11: 개인 명함·인맥 활용 범위")
    ap.add_argument("--cards-inbox", default="", help="명함 사진을 모아두는 폴더(선택)")
    ap.add_argument("--private", default="", help="나만 보기 슬랙 Incoming Webhook URL(선택)")
    ap.add_argument("--team", default="", help="팀 공유 슬랙 웹훅 URL(선택)")
    ap.add_argument("--no-test", action="store_true", help="테스트 메시지 발송 생략")
    ap.add_argument("--guide", action="store_true", help="질문 11개 안내만 출력")
    args = ap.parse_args()

    # 인자 없이 실행 = 대화 모드 안내(클로드가 이 안내를 읽고 질문 11개를 대신 묻는다).
    provided = any([args.name, args.company, args.rank, args.title, args.sell_what,
                    args.accounts_scope, args.info_scope, args.send_scope, args.approval_mode,
                    args.send_as, args.sender_email, args.signature, args.escalate_rules,
                    args.personal_contacts, args.cards_inbox, args.private, args.team])
    if args.guide or not provided:
        print(GUIDE)
        return

    private_wh = args.private or env_webhook("private")
    team_wh = args.team or env_webhook("team")
    if args.private and not valid_webhook(args.private):
        raise SystemExit("⛔ --private 는 https://hooks.slack.com/ 로 시작하는 슬랙 웹훅이어야 합니다.")
    if args.team and not valid_webhook(args.team):
        raise SystemExit("⛔ --team 웹훅 형식이 올바르지 않습니다.")

    send_scope = split_list(args.send_scope)
    unknown = [s for s in send_scope if s not in SEND_ACTIONS]
    if unknown:
        print(f"[주의] send_scope 에 표준 밖 값이 있습니다(그대로 저장): {', '.join(unknown)}")
        print(f"       표준 값: {', '.join(SEND_ACTIONS)}")

    # 1) 안정적 폴더(~/.sales-copilot)에 설정 생성 — 이미 있으면 답한 항목만 갱신(기존 값 보존).
    os.makedirs(DATA_DIR, exist_ok=True)
    src = CONFIG_PATH if os.path.exists(CONFIG_PATH) else EXAMPLE_CONFIG
    with open(src, encoding="utf-8") as f:
        cfg = json.load(f)
    me = cfg.setdefault("me", {})
    for key, val in (
        ("name", args.name), ("rank", args.rank), ("title", args.title),
        ("sell_what", args.sell_what), ("accounts_scope", args.accounts_scope),
        ("info_scope", args.info_scope), ("send_scope", send_scope or None),
        ("approval_mode", args.approval_mode), ("send_as", args.send_as),
        ("sender_email", args.sender_email), ("signature", args.signature),
        ("escalate_rules", split_list(args.escalate_rules) or None),
        ("personal_contacts_policy", args.personal_contacts),
    ):
        if val:
            me[key] = val
    if args.company:
        cfg.setdefault("company", {})["name"] = args.company
    if args.cards_inbox:
        cfg.setdefault("sources", {})["cards_inbox"] = args.cards_inbox
    delivery = cfg.setdefault("delivery", {})
    if private_wh:
        delivery.setdefault("private", {}).update(enabled=True, slack_webhook=private_wh)
    if team_wh:
        delivery.setdefault("team", {}).update(enabled=True, slack_webhook=team_wh)
    # 핵심 답(자격·판매상품·승인 방식)이 모이면 설정 완료로 표시 — 훅이 온보딩 재권유를 멈춘다.
    if all(me.get(k) for k in ("rank", "sell_what", "approval_mode")):
        cfg.setdefault("setup", {})["completed"] = True
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[설정 저장] {CONFIG_PATH}")

    # 2) 영업 컨텍스트(context/ 8종 + _policy) 준비 — 템플릿 복사(없는 파일만)
    src_dir = os.path.join(ROOT, "templates", "context")
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    copied = 0
    for fn in sorted(os.listdir(src_dir)):
        dst = os.path.join(CONTEXT_DIR, fn)
        if not os.path.exists(dst):
            shutil.copy(os.path.join(src_dir, fn), dst)
            copied += 1
    print(f"[영업 컨텍스트] {CONTEXT_DIR}/  ← {copied}개 파일 준비(클로드가 회사 자료에서 대신 채움)")

    # 3) 로컬 최소 CRM·오늘 큐 폴더 준비(CRM 커넥터가 없어도 여기서 동작)
    for d in (CRM_DIR, QUEUE_DIR):
        os.makedirs(d, exist_ok=True)
    print(f"[로컬 CRM] {CRM_DIR}/  (accounts·contacts·leads… 는 crm.py 가 기록하며 생성)")

    # 4) 테스트 발송(웹훅 검증 + 나만 보기/팀 공유 뒤바뀜 확인)
    if not args.no_test:
        if private_wh:
            ok, msg = send_test(private_wh, "나만 보기")
            print(f"[테스트→나만 보기] {'✅ 전송 — 슬랙 확인' if ok else '⚠️ 실패: ' + msg}")
        if team_wh:
            ok, msg = send_test(team_wh, "팀 공유")
            print(f"[테스트→팀 공유] {'✅ 전송 — 슬랙 확인' if ok else '⚠️ 실패: ' + msg}")

    print("\n다음 할 일:")
    print("  1) 점검: python3 scripts/doctor.py  (남은 설정·데이터 상태를 ✅/⚠️ 로)")
    print(f"  2) 컨텍스트 채우기: {CONTEXT_DIR}/  (또는 클로드에게 '영업 컨텍스트 채우자')")
    print("  3) 자료 자동 탐색: python3 scripts/find_docs.py  로 명함 사진·제안서·고객 자료 찾기")
    print("  4) 매일 루틴 등록: python3 scripts/schedule_brief.py --kind morning  (아침 8시 오늘의 큐)")
    print("  아직 답 안 한 질문이 있으면 클로드에게 'AI 영업맨 설정 계속하자'라고 하세요.")


if __name__ == "__main__":
    main()
