#!/usr/bin/env python3
"""반자동 셋업 — 설정 질문 11개(SPEC §1)의 답을 인자로 주면 config 생성·검증·테스트 발송까지 한 번에.

  python3 scripts/quicksetup.py                      # 인자 없이: 질문 11개 안내 출력(클로드가 대화로 진행)
  python3 scripts/quicksetup.py --rank rep --sell-what "중소 이커머스에 AI 광고 소재 구독" \
      --approval-mode per_item [--private "https://hooks.slack.com/services/..."] [--no-test]

설정은 ~/.sales-copilot/ 에 저장되어 플러그인을 업데이트/재설치해도 유지된다.
값 하나씩 고치려면 set_config.py 를 쓴다. 웹훅은 SALES_COPILOT_SLACK_PRIVATE/TEAM 환경변수로도 받는다.
(슬랙이 없어도 됩니다 — 이 단계 없이 채팅 미리보기만으로도 AI 영업맨은 동작합니다.)

내장 엔진 6종(cards/sms/email_send/coldmail/vox/team)은 전부 선택 입력 — 안 넣으면 빈 값으로
블록만 준비하고 기능 안내를 출력한다. 마지막에 아침 큐·저녁 마감·주간 리포트 **루틴 등록을
묻지 않고 기본 수행**한다(W5b — --no-routine 으로만 생략).
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

[선택 — 내장 엔진 6종(안 넣어도 나머지는 전부 동작, 나중에 set_config.py 로 추가 가능)]
  --cards-sheet-webhook <URL>            명함 구글시트 웹훅(apps-script/cards 배포 후)
  --vcard-dir <폴더>                     리멤버용 vCard 저장 폴더
  --sms-key/--sms-secret/--sms-sender    solapi 문자 발송(키 2개 + 등록 발신번호)
  --smtp-user/--smtp-app-password        Gmail 개별 메일 실발송(앱 비밀번호)
  --coldmail-sheet-webhook <URL>         콜드메일 대장(apps-script/coldmail 배포 후)
  --vox-key/--vox-phone                  TryVox AI 전화(아웃바운드·인바운드 콜)
  --team-members "이름,이름"             팀 관리(/team) 대상 팀원
  --watch-channels "#영업,#고객문의"      팀 현황을 읽을 슬랙 채널

[한 번에 실행 예]
  python3 scripts/quicksetup.py --name "홍길동" --company "우리회사" \\
      --rank rep --title "대리 / 신규영업" --sell-what "중소 이커머스에 AI 광고 소재 구독" \\
      --accounts-scope assigned --info-scope own \\
      --send-scope find_leads,register_contacts,draft_email,propose_meeting \\
      --approval-mode per_item --send-as self \\
      --escalate-rules "예상 계약금액 3천만원 이상, 할인율 10% 초과" \\
      --personal-contacts selected \\
      --private "https://hooks.slack.com/services/..."   # 나만 보기 채널 웹훅(선택)

값 하나만 고칠 땐: python3 scripts/set_config.py me.approval_mode=per_item

설정 저장 후 아침 8시 큐·저녁 5시 마감·금요일 주간 리포트 **루틴 등록을 기본으로 진행**합니다
(묻지 않음 — 루틴은 옵션이 아니라 뼈대. 생략은 --no-routine 뿐)."""


def valid_webhook(url):
    return bool(url) and url.startswith(SLACK_PREFIX)


def print_engine_status(cfg):
    """내장 엔진 6종 설정 상태 — 미설정은 막힘이 아니라 안내(전부 선택 사항)."""
    cards, sms = cfg.get("cards", {}), cfg.get("sms", {})
    email, cold = cfg.get("email_send", {}), cfg.get("coldmail", {})
    vox, team = cfg.get("vox", {}), cfg.get("team", {})
    rows = [
        ("명함 시트", bool(cards.get("sheet_webhook_url")),
         "스캔·CRM 등록은 지금도 동작 — 구글시트 백업은 apps-script/cards 배포 후 cards.sheet_webhook_url"),
        ("문자(sms)", bool(sms.get("api_key") and sms.get("sender")),
         "문자 발송은 solapi 키·발신번호 설정 후 (sms.api_key/api_secret/sender)"),
        ("메일 발송", bool(email.get("username") and email.get("app_password")),
         "개별 메일 실발송은 Gmail 앱 비밀번호 설정 후 (email_send.username/app_password)"),
        ("콜드메일 대장", bool(cold.get("sheet_webhook_url")),
         "대량 발송은 apps-script/coldmail 배포 후 연결 — 발송은 dispatchTick 전담, 외부 행 기본 PAUSED"),
        ("전화(vox)", bool(vox.get("enabled") and vox.get("api_key")),
         "TryVox API 키를 넣으면 아웃바운드·인바운드 콜 (vox.api_key, /phone-call)"),
        ("팀 관리", bool(team.get("members")),
         "팀원·감시 채널을 넣으면 팀 현황·리드 배정 (/team)"),
    ]
    print("\n[내장 엔진 6종 — 전부 선택, 미설정이어도 나머지는 동작]")
    for label, ok, guide in rows:
        print(f"  {'✅' if ok else 'ℹ️ '} {label}: " + ("설정됨" if ok else f"미설정 — {guide}"))


def print_routine_default(cfg):
    """W5b: 루틴 등록은 옵션이 아니라 기본 동작 — 묻지 않고 진행한다(--no-routine 만 예외).
    이 스크립트 자체는 스케줄러가 없으므로 ①클로드에 스케줄 도구가 있으면 지금 바로 등록하라는
    지시를 출력하고(등록 후 brief.routine_enabled=true 저장) ②없는 환경이면 schedule_brief.py
    레시피·크론 라인을 보여준다. 상태는 config brief.routine_enabled 로 기록된다."""
    try:
        import schedule_brief as sb  # noqa: PLC0415 — 같은 폴더, 표준 라이브러리만 사용
        recipes = [(k, sb.RECIPES[k]) for k in ("morning", "evening", "weekly")]
        human = sb.human
    except Exception:
        recipes, human = [], str
    brief = cfg.get("brief", {})
    print("\n[루틴 등록 — 기본 진행] 매일 자동은 옵션이 아니라 뼈대입니다(묻지 않음, 거부 시 --no-routine).")
    for _kind, r in recipes:
        cron = brief.get(r["cfg_key"]) or r["cron"]
        print(f"  · {r['title']}: {human(cron)}  (크론식 {cron}, 스킬 /{r['skill']})")
    print("  → 클로드: 스케줄 도구(scheduled-tasks/클라우드 루틴)가 있으면 위 3개를 지금 바로 등록하고")
    print("    (사용자에게 다시 묻지 말 것 — 명시적 거부일 때만 생략) 등록 완료 후 실행:")
    print('      python3 "$CLAUDE_PLUGIN_ROOT/scripts/set_config.py" brief.routine_enabled=true')
    print("  → 스케줄 도구가 없는 환경이면 레시피 출력: python3 scripts/schedule_brief.py --kind morning")
    print("    (claude.ai/code/routines 웹 등록 또는 crontab — 등록 확인 전까지 brief.routine_enabled=false 유지)")


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
    # 내장 엔진 6종 — 전부 선택 입력. 미입력이면 빈 값으로 블록만 준비하고 기능 안내를 출력한다.
    ap.add_argument("--cards-sheet-webhook", default="", help="명함 구글시트 웹훅 URL(선택)")
    ap.add_argument("--vcard-dir", default="", help="리멤버용 vCard 저장 폴더(선택)")
    ap.add_argument("--sms-key", default="", help="solapi API 키(선택)")
    ap.add_argument("--sms-secret", default="", help="solapi API 시크릿(선택)")
    ap.add_argument("--sms-sender", default="", help="문자 발신번호(사전 등록된 번호, 선택)")
    ap.add_argument("--smtp-user", default="", help="Gmail SMTP 계정(선택)")
    ap.add_argument("--smtp-app-password", default="", help="Gmail 앱 비밀번호(선택)")
    ap.add_argument("--coldmail-sheet-webhook", default="", help="콜드메일 대장 웹훅 URL(선택)")
    ap.add_argument("--vox-key", default="", help="TryVox 조직 API 키(선택)")
    ap.add_argument("--vox-phone", default="", help="Vox 발급/연결 전화번호(선택)")
    ap.add_argument("--team-members", default="", help="팀원 이름(쉼표 구분, 선택)")
    ap.add_argument("--watch-channels", default="", help="팀 현황을 읽을 슬랙 채널(쉼표 구분, 선택)")
    ap.add_argument("--no-test", action="store_true", help="테스트 메시지 발송 생략")
    ap.add_argument("--no-routine", action="store_true",
                    help="루틴 등록 기본 수행 생략(루틴은 기본값 — 명시적으로 거부할 때만)")
    ap.add_argument("--guide", action="store_true", help="질문 11개 안내만 출력")
    args = ap.parse_args()

    # 인자 없이 실행 = 대화 모드 안내(클로드가 이 안내를 읽고 질문 11개를 대신 묻는다).
    provided = any([args.name, args.company, args.rank, args.title, args.sell_what,
                    args.accounts_scope, args.info_scope, args.send_scope, args.approval_mode,
                    args.send_as, args.sender_email, args.signature, args.escalate_rules,
                    args.personal_contacts, args.cards_inbox, args.private, args.team,
                    args.cards_sheet_webhook, args.vcard_dir, args.sms_key, args.sms_secret,
                    args.sms_sender, args.smtp_user, args.smtp_app_password,
                    args.coldmail_sheet_webhook, args.vox_key, args.vox_phone,
                    args.team_members, args.watch_channels])
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

    # 내장 엔진 6종 블록 — 구버전 config에도 블록을 채워 넣는다(기존 값 보존, 없는 키만 예시값).
    try:
        with open(EXAMPLE_CONFIG, encoding="utf-8") as f:
            example = json.load(f)
    except (OSError, ValueError):
        example = {}
    for blk in ("cards", "sms", "email_send", "coldmail", "vox", "team"):
        block = cfg.setdefault(blk, {})
        for k, v in (example.get(blk, {}) or {}).items():
            block.setdefault(k, v)
    for key, val in (
        (("cards", "sheet_webhook_url"), args.cards_sheet_webhook),
        (("cards", "vcard_dir"), args.vcard_dir),
        (("sms", "api_key"), args.sms_key),
        (("sms", "api_secret"), args.sms_secret),
        (("sms", "sender"), args.sms_sender),
        (("email_send", "username"), args.smtp_user),
        (("email_send", "app_password"), args.smtp_app_password),
        (("coldmail", "sheet_webhook_url"), args.coldmail_sheet_webhook),
        (("vox", "api_key"), args.vox_key),
        (("vox", "phone_number"), args.vox_phone),
    ):
        if val:
            cfg[key[0]][key[1]] = val
    if args.vox_key:
        cfg["vox"]["enabled"] = True
    if args.team_members:
        cfg["team"]["members"] = [{"name": n} for n in split_list(args.team_members)]
    if args.watch_channels:
        cfg["team"]["watch_channels"] = split_list(args.watch_channels)

    # 루틴 상태 기록 — 등록 전이면 false 로 명시(등록 완료 시 set_config 로 true 전환).
    brief = cfg.setdefault("brief", {})
    brief.setdefault("routine_enabled", False)

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
    print_engine_status(cfg)

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

    # 5) 루틴 등록 — 기본 수행(W5b). 묻지 않는다. --no-routine(명시적 거부)일 때만 생략.
    if args.no_routine:
        print("\n[루틴] --no-routine 지정 — 등록 생략(brief.routine_enabled=false 유지). "
              "나중에 /routine 으로 언제든 등록.")
    else:
        print_routine_default(cfg)

    print("\n다음 할 일:")
    print("  1) 점검: python3 scripts/doctor.py  (남은 설정·데이터 상태를 ✅/⚠️ 로)")
    print(f"  2) 컨텍스트 채우기: {CONTEXT_DIR}/  (또는 클로드에게 '영업 컨텍스트 채우자')")
    print("  3) 자료 자동 탐색: python3 scripts/find_docs.py  로 명함 사진·제안서·고객 자료 찾기")
    print("  아직 답 안 한 질문이 있으면 클로드에게 'AI 영업맨 설정 계속하자'라고 하세요.")


if __name__ == "__main__":
    main()
