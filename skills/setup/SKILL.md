---
name: setup
description: AI 영업맨 첫 설정 — 직급·직책·실제 권한 온보딩. 질문 11개로 발송·일정·가격·계약 권한과 승인 모드(5종)를 정해 config와 permissions.md에 기록하고, 내장 엔진 6종(명함 시트·문자·메일 발신·콜드메일 대장·Vox 전화·팀)은 선택으로 연결하며, 마지막에 아침·저녁·주간 루틴을 묻지 않고 기본 등록한다. "영업맨 설정 / 셋업 / 처음 사용 / 설정 계속 / 권한 변경 / 승인 모드 바꿔줘" 또는 첫 설치 시 클로드가 먼저 제안.
---

> **영업 루프(항상):** 오늘 연락할 사람 → 접촉 → 회신 처리 → 콜 → 미팅 → 다음 행동 갱신. 모든 리드에 다음 행동. 자세히 [[method]].

# setup — 직급·직책·권한 온보딩 (SET-01~14)

AI 영업맨은 초안만 쓰는 도구가 아니라 **실제로 연락을 보내는 도구**라, 권한을 잘못 잡으면 관계가 아니라 사고를 만든다. 직급만 보고 판단하면 안 된다 — 대표여도 특정 계정으로 대량 발송하지 않을 수 있고, 팀원이어도 승인된 캠페인은 자동 실행할 수 있다(일반 CRM도 소유자·역할 계층으로 접근을 나눈다). 여기서 받은 답이 이후 **모든 발송 게이트·상신·열람 경계의 기준**이 된다. 설정 전에는 어떤 스킬도 DRAFT ONLY로만 동작한다.

## 실행 유형: [H] 사용자 답변 + [A] 저장·규칙 생성 — 이 스킬이 다른 모든 스킬의 [A]/[P]/[E] 기준(`approval_mode`·`send_scope`)을 만든다. 첫 설치(설정 없음)를 훅이 감지하면 클로드가 먼저 이 스킬을 제안한다.

## 0. 준비 — 상태 진단
```bash
sales-copilot doctor        # config·context 8종·CRM 파일·수신거부 목록 점검
sales-copilot quicksetup    # ⚡ 빠른 길: 반자동 설정(config 골격·context/ 템플릿 준비)
```
- **사용자는 파일·JSON·터미널을 직접 건드리지 않는다.** 아래 질문을 한 번에 하나씩 쉬운 말로 묻고, 받은 값을 `set_config.py`로 네가 대신 저장한다. 어려운 항목은 "지금은 건너뛰기"를 제안 — 건너뛴 값은 안전한 기본(건별 승인·본인 열람)이다.
- 이미 `setup.completed=true`면 처음부터 다시 묻지 말고 **바꿀 항목만** 묻는다("승인 모드만 바꿔줘").

## 1. 누구인가 — 자격·직급·직책·담당 업무 (SET-01·02·03)
질문 1~3. 자격(rank): 대표·공동창업자 `founder` / C-Level·임원 `exec` / 영업본부장·총괄 `sales_head` / 영업팀장·리드 `team_lead` / 영업팀원·AE·BD·SDR `rep` / 타부서 `other_dept` / 개인사업자·프리랜서 `solo` / 외부 영업대행 `agency`. 직급·직책과 실제 담당 업무는 그대로 받아 적는다.
```bash
sales-copilot set_config me.name="홍길동" me.rank="rep" me.title="영업팀 대리 (AE)" me.functions="outbound,followup"
```
- 자격별 동작 차이(대표=인맥 자산화·대형 건 우선, 타부서=소개 요청까지만 등)는 [[role]]이 정한다. **모르겠다는 답은 낮은 권한으로 가정** — 안전한 쪽. 나중에 언제든 바꾼다.

## 2. 무엇을 누구에게 파는가 (SET-04)
질문 4. 한 줄이면 충분하다 — 상품·가격·사례·반론 등 컨텍스트 8종을 제대로 채우는 건 [[context]]의 몫.
```bash
sales-copilot set_config me.sell_what="중소 이커머스에 AI 광고 소재 구독" company.name="우리 회사" company.one_liner="무엇을, 누구에게, 왜 좋은지 한 줄"
```

## 3. 어디까지 보는가 — 계정·정보 범위 (SET-05)
질문 5(담당 고객 범위): 회사 전체 `all` / 특정 산업 `industry` / 특정 지역 `region` / 특정 상품 `product` / 배정 계정 `assigned` / 본인 인맥 `own_network` / 개인 고객 `personal`.
질문 6(열람 범위): 전사 `company` / 소속 팀 `team` / 본인 담당 `own` / 개인 연락처 `personal` / 공개 정보만 `public`.
```bash
sales-copilot set_config me.accounts_scope="assigned" me.info_scope="own"
```

## 4. 무엇을 직접 실행할 수 있는가 — 발송·일정·가격·계약 (SET-06·07·08·09)
질문 7. 체크리스트로 하나씩 확인해 `send_scope`에 담는다: 리드 발굴 `find_leads` · 연락처 등록 `register_contacts` · 메일 초안 `draft_email` · 메일 자동 발송 `auto_send_email` · 후속 자동 발송 `auto_send_followup` · 전화·미팅 제안 `propose_meeting` · **일정 확정 `confirm_schedule`(SET-07)** · 제안서 발송 `send_proposal` · **견적·가격 제안 `quote_price` · 할인 협상 `negotiate_discount`(SET-08)** · **계약조건 협상 `negotiate_contract`(SET-09)**. 범위 밖 행동은 전부 [E] 상신이다.
질문 8. 외부 발송 승인 방식 = **권한 모드 5종** (SET-06):

| 모드 | 동작 |
|---|---|
| `auto` **AUTO** | 허용범위 안에서 자동 실행 |
| `batch` **BATCH** | 하루/캠페인 단위로 묶어 승인 |
| `per_item` **PER-ITEM** | 발송·일정·제안 건별 승인 (기본값) |
| `draft_only` **DRAFT ONLY** | 조사·초안 작성까지만 |
| `escalate` **ESCALATE** | 권한을 넘으면 상급자 상신 |

```bash
sales-copilot set_config me.send_scope="find_leads,register_contacts,draft_email,propose_meeting" me.approval_mode="per_item"
```
- `send_scope`는 이 경로로는 쉼표 문자열, `quicksetup.py --send-scope`로는 JSON 리스트로 저장된다 — **둘 다 유효**(소비 스킬·훅은 두 형태 모두 처리). 리스트로 통일하려면 quicksetup.py 플래그를 권장.
- **미설정·`draft_only`면 어떤 스킬도 절대 자동 발송하지 않는다.** `other_dept`·`agency`·신입에게는 `auto`를 권하지 않는다(상신·소개 요청까지만) — 자세히 [[role]].

## 5. 누구 이름으로 보내는가 — 발신 계정·서명 (SET-11)
질문 9: 본인 `self` / 대표 `ceo` / 영업팀 공용 `team` / 회사 대표메일 `company` / 계정별 담당자 `per_account` / 상황별 `contextual`. 발신 메일 주소와 서명까지 받아 둔다 — 아웃바운드 문안의 발신자·목적 명확화에 그대로 쓰인다.
```bash
sales-copilot set_config me.send_as="self" me.sender_email="gildong@company.com" me.signature="홍길동 드림 | OO 영업팀"
```

## 6. 상급자 개입선과 개인 인맥 (SET-10)
질문 10. 상신 기준(escalate_rules) — 예: 예상 계약금액 3천만원 이상 / 대기업·투자사·전략 파트너 / 할인율 10% 초과 / 계약조건 변경 / 부정적 이슈. 여러 개는 쉼표로.
질문 11. 개인 명함·인맥 활용(SET-10): 전부 가능 `all` / 선택한 연락처만 `selected` / 기회만 알리고 자동 연락 금지 `signal_only` / 완전 비공개 `private`.
```bash
sales-copilot set_config me.escalate_rules="예상 계약금액 3천만원 이상,할인율 10% 초과,계약조건 변경" me.personal_contacts_policy="selected"
```
- `escalate_rules`도 `send_scope`처럼 이 경로는 쉼표 문자열, `quicksetup.py --escalate-rules`는 리스트 저장 — 둘 다 유효. 리스트로 통일하려면 quicksetup.py 플래그를 권장.

## 7. [A] 규칙 생성 — permissions.md·상급자 연결·브리핑 구성 (SET-12·13·14)
- 답변을 요약해 **`~/.sales-copilot/context/permissions.md`를 네가 대신 작성한다**(SET-12): ①직접 할 수 있는 것 ②승인 필요한 것 ③상신 대상과 기준 ④개인 인맥 정책. 모든 발송 스킬이 발송 게이트에서 이 파일과 config를 읽는다.
- 상신[E]이 갈 곳: `me.reports_to`에 상급자·승인자·대체 담당자를 연결한다(SET-14). 비어 있으면 상신 시마다 물어야 하니 지금 받아 둔다.
- 자격별 브리핑·오늘 큐 구성(SET-13)은 [[role]] 규칙대로 자동 적용되고, 아침 8시 큐·저녁 5시 마감·주간 리포트 루틴은 §10에서 **묻지 않고 기본으로 건다**.
```bash
sales-copilot set_config me.reports_to="김본부장" me.timezone="Asia/Seoul"
```

## 8. 커넥터 점검 — Gmail·캘린더·CRM (연결 없어도 동작)
- `claude.ai → 설정 → 커넥터`에서 Gmail·Google 캘린더·CRM 연결 여부를 확인하게 한다. **OAuth는 사용자가 직접 눌러야 한다**(이 세션에서 대신 못 함). 명함 사진 폴더가 있으면 `cards_inbox`로 등록.
```bash
sales-copilot set_config sources.use_email=true sources.use_calendar=true sources.use_crm=false sources.cards_inbox="~/Pictures/명함"
```
- **미연결이어도 막지 않는다.** 로컬 최소 CRM(`~/.sales-copilot/crm/` JSONL)이 자동 생성되어 `crm.py`로 전부 동작하고, 커넥터가 붙으면 그쪽 실측 데이터를 우선한다.

### 9-1. [A] 자동 갱신을 건다 — 지금 안 걸면 아무도 안 건다
Claude Code는 **공식 마켓플레이스만** 자동 갱신을 기본으로 켠다. 이 플러그인은 서드파티라 **기본이 꺼짐**이다. 여기서 걸어 두지 않으면 사용자는 몇 달 전 버전을 쓰면서 그 사실조차 모른다.
```bash
sales-copilot update_check --install-cron
```
매주 월요일 09:30 점검·갱신. **사용자가 손댄 파일은 자동으로 지켜진다.** 본체는 [[update]].
Claude Code 자체 자동 갱신은 대화형 패널이라 대신 눌러 줄 수 없으니 이 줄을 그대로 전달한다: `/plugin → Marketplaces → 이 플러그인 → Enable auto-update`

### 9-2. [A] 언어 — 묻지 말고 감지한다
기본값 `auto` 그대로 두고, 사용자가 요청하거나 팀 공용 산출물의 언어를 못 박아야 할 때만 값을 넣는다.
```bash
sales-copilot set_config language=auto   # en·ja·zh 등으로 고정 가능
```
- **대화 언어와 산출물 언어는 다르다.** 자세히 [[method]] 00절.

## 9. 내장 엔진 6종 연결 — 전부 선택, 안 붙여도 나머지는 전부 동작
여기서부터는 답을 안 해도 된다("나중에"면 그대로 넘어간다). 미설정은 막힘이 아니라 안내다 — 그 기능을 쓰려는 순간 해당 스킬이 무엇이 빠졌는지 다시 짚어준다.

| 엔진 | config 키 | 쓰는 곳 |
|---|---|---|
| 명함 시트 | `cards.sheet_webhook_url`(+`sheet_webhook_secret`)·`cards.vcard_dir` | [[import-cards]] — 구글시트 백업·리멤버 vCard. `apps-script/cards` 배포 후 |
| 문자 발송 | `sms.api_key`·`sms.api_secret`·`sms.sender` | solapi — 명함 인사·안부 문자 실발송 (등록된 발신번호 필수) |
| 메일 발신 | `email_send.username`·`email_send.app_password` | Gmail 앱 비밀번호 — 개별·소량 메일 실발송(`send_email.py`) |
| 콜드메일 대장 | `coldmail.sheet_webhook_url` | [[outreach]] 대량 발송 — `apps-script/coldmail` 배포 후. 발송은 dispatchTick 전담, 외부 행 기본 PAUSED |
| Vox 전화 | `vox.api_key`·`vox.phone_number` | [[phone-call]] — TryVox 아웃바운드·인바운드 AI 콜 |
| 팀 | `team.members`·`team.watch_channels` | [[team]] — 팀장·임원용 팀 현황·리드 배정 |

```bash
sales-copilot set_config cards.vcard_dir="~/명함" sms.sender="0212345678" email_send.username="me@gmail.com"
```
- 시크릿(웹훅 secret·API 키·앱 비밀번호)은 사용자가 직접 값을 줄 때만 저장하고 **화면 출력에 되풀이하지 않는다.** 한 번에 채우려면 `quicksetup.py`의 선택 플래그(`--cards-sheet-webhook`·`--sms-key`·`--smtp-user`·`--coldmail-sheet-webhook`·`--vox-key`·`--team-members` 등)를 쓴다.

## 10. [A] 루틴 등록 — 묻지 않고 기본으로 건다 (SET-13·§7)
설정 저장이 끝나면 **아침 8시 큐 · 저녁 5시 마감 · 금요일 주간 리포트 3종을 바로 등록한다.** "등록할까요?"라고 묻지 않는다 — 루틴은 옵션이 아니라 이 플러그인의 뼈대고, 사용자가 명시적으로 거부할 때만 생략한다(`quicksetup.py`도 `--no-routine`일 때만 건너뛴다).
1. **스케줄 도구가 있으면**(scheduled-tasks·클라우드 루틴·`/schedule`) 그 도구로 3종을 즉시 등록한다. 크론식은 config `brief.morning_schedule`(기본 `0 8 * * 1-5`)·`evening_schedule`(`0 17 * * 1-5`)·`weekly_schedule`(`0 9 * * 5`), 예약 프롬프트는 `schedule_brief.py` 출력을 그대로 쓴다.
2. **없으면** 레시피(크론식+프롬프트+crontab 경로)를 제시한다:
```bash
sales-copilot schedule_brief --kind morning   # evening·weekly 동일
```
3. 등록이 확인되면(도구 등록 완료 또는 사용자의 "예약 완료") 상태를 기록한다:
```bash
sales-copilot set_config brief.routine_enabled=true
```
- 등록 방법 우선순위·확인·수정·해제의 본체는 [[routine]] — 여기서는 기본 3종을 걸어 놓고 "등록해놨습니다" 한 줄로 통보한다. 미등록으로 끝나면 훅이 다음 세션에서 다시 들이민다.

## 11. 마무리 — 검증 후 완료 처리
```bash
sales-copilot set_config setup.completed=true
sales-copilot doctor
```

## 출력 (설정 요약 — 마지막에 반드시 보여준다)
```
📞 설정 완료 — {이름} · {자격/직책}. 내일 아침 8시부터 큐가 알아서 옵니다.
판매: {sell_what}
범위: 계정 {accounts_scope} · 열람 {info_scope}
직접 실행: {send_scope 요약} — 이 밖은 전부 [E] 상신 → {reports_to}
승인 모드: {approval_mode} · 발신: {send_as}
상신 기준: {escalate_rules 요약} · 개인 인맥: {personal_contacts_policy}
커넥터: Gmail {✓/✗} · 캘린더 {✓/✗} · CRM {✓/✗ → 로컬 CRM으로 동작}
내장 엔진: 명함 {✓/－} · 문자 {✓/－} · 메일 {✓/－} · 콜드메일 {✓/－} · 전화 {✓/－} · 팀 {✓/－} (－는 나중에 연결)
루틴: 아침 8시 · 저녁 5시 · 금요일 주간 — {등록해놨습니다 / 레시피 전달됨(등록 확인 대기)}
다음: 명함 사진이 있으면 "명함 등록", 아니면 "오늘 뭐부터?" → [[today]]가 오늘의 행동 큐를 만든다
```

## 원칙
- **묻는 건 설정 질문 11개([H])와 발송 게이트뿐이다.** 저장·permissions.md 작성·루틴 등록 같은 내부 작업은 "할까요?" 없이 실행하고 "~해놨습니다. 확인만 해주세요" 한 줄로 통보한다.
- **환각 금지.** 답하지 않은 항목을 임의로 채우지 않는다 — 빈 값은 안전 기본(건별 승인·본인 열람)으로 두고, 무엇이 비었는지는 `doctor.py`가 짚는다.
- **자동 발송을 기본값처럼 만들지 않는다.** `auto`는 사용자가 명시적으로 골랐을 때만. 기본은 초안+승인.
- **데이터 경계**: 개인 인맥·가격 하한·할인 한도는 비공개(`context/_policy.md`) — 팀 채널·웹 검색어로 내보내지 않는다. `personal_contacts_policy=private`인 연락처는 어떤 출력에도 노출 금지(SET-10·REL-17 연동).
- **권한 인식**: 설정 변경은 본인 것만. 팀원 권한 부여·캠페인 승인은 팀장·임원의 영역 — 자세히 [[role]]. 전체 스킬 사용법은 [[help]].

관련: [[role]] · [[context]] · [[routine]] · [[help]] · [[method]]
