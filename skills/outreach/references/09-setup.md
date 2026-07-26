# 09 — 콜드메일 엔진 온보딩 (Apps Script 대장 연결)

<!-- 원본: salesfactory-plugin skills/salesfactory/references/09-setup.md — sales-copilot 통합판.
     파일 위치는 apps-script/coldmail/, 로컬 설정은 config.json coldmail 블록으로 변경됨. -->

대리 설치를 하지 않으므로 **설치 자체가 제품의 일부**다. 아래 단계를 Claude가 대화로 끌고 가고 각 단계를 스스로 검증한다. 이미 연결돼 있으면(`state.py summary`가 `{"ok": true}`) 이 문서 통째로 건너뛴다. 전체 소요 30~40분 — 한 번만 하면 이후 캠페인은 적재만 하면 된다.

## 순서 (Claude가 안내, 사용자는 클릭만)

1. **계정 유형 확인**(기본 Workspace): 영업은 회사 도메인 = Google Workspace 메일로(07). 하루 1,500 발송 여유. 개인 Gmail이면 consumer(100/일)로 강등 안내. 회사 도메인 관리자가 같은 도메인에 배포하면 "확인되지 않은 앱" 경고가 면제될 수 있음.
2. **Apps Script 세팅(컨테이너 바운드)**: 새 Google Sheet 생성 → **확장 프로그램 > Apps Script** → 플러그인의 `apps-script/coldmail/Sheets.gs`·`Code.gs` 붙여넣기 + `appsscript.json` 매니페스트 반영(프로젝트 설정에서 "매니페스트 표시" 켜기 — Gmail 고급 서비스 + `gmail.send`·`spreadsheets.currentonly` scope). **standalone이 아니라 시트 바운드**여야 `getActiveSpreadsheet()`가 이 시트를 가리키고 scope가 현재 문서로 좁혀진다. 저장 → 첫 실행.
3. **OAuth 동의**: 도메인 내부 배포면 경고 없이 바로 동의일 수 있음. 아니면 "확인되지 않은 앱" → **고급 → 안전하지 않은 페이지로 이동 → 모두 선택 → 계속**(정상 절차다 — 스크린샷과 함께 안내). scope는 전부 sensitive(restricted 없음), 받은편지함 읽기는 미요청.
4. **시트 대장 생성 + config**: `initSheets()` 실행 → 시트 6개(config/contacts/drafts/sends/suppression/followup_queue) 생성. config 시트에 `SENDER_NAME`·`SENDER_PHONE`·`SENDER_ADDRESS`(국내 발송 필수 3종, 08)·`DAILY_TARGET` 입력.
5. **웹앱 배포**(두 역할: ①`state.py` 상태 창구 ②수신거부 one-click 엔드포인트): 배포 > 새 배포 > 유형 "웹앱" > 실행 "나" / 액세스 "링크가 있는 모든 사용자"(one-click 수신거부는 익명 POST라 필수) → `…/exec` URL 복사. Apps Script **Script Properties**에 `SF_API_TOKEN`(임의 생성 — state.py 인증) + `SF_WEBAPP_URL`(이 URL — 수신거부 링크 생성용) 저장(`SF_HMAC_SECRET`은 자동 생성).
6. **로컬 연결** — sales-copilot config에 저장:
```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/set_config.py" coldmail.sheet_webhook_url=https://script.google.com/macros/s/…/exec coldmail.sheet_webhook_secret=<SF_API_TOKEN 값>
python3 "$CLAUDE_PLUGIN_ROOT/scripts/state.py" summary   # {"ok": true, ...} 나오면 연결 완료
```
7. **내부 테스트 1건 (의무 — 생략 불가, W2d)**: 캠페인 첫 실행 전 반드시 **내 주소(config `me.sender_email`)로 테스트 1건을 실발송**한다 — 본인 앞 테스트용 contact+draft를 `approved`로 적재하고 `LIVE_SEND=true`로 dispatchTick 1회 → 받은 메일에서 렌더링·서명·수신거부 문구·발신자 표기를 확인. `approval_mode=auto`여도 이 단계는 생략 불가.
8. **외부 발송 개시**: 테스트 통과 확인 후에만 외부 행을 PAUSED(`pending_review`)→READY(`approved`)로 승인 전환 + `installDispatchTrigger()`로 페이싱 트리거 설치. `LIVE_SEND`는 사용자가 명시적으로 켠 상태여야 실발송된다(기본 dry-run).

## 실행마다 1줄 리포트 (필수 — 조용한 실패 방지)

매 실행 시 `state.py summary`로 **오늘 발송/상한/남은 큐/마지막 성공 일시**를 출력한다. "아무 일도 안 일어나는데 잘 돌고 있다고 믿는" 상태가 자동화의 기본값이다 — 이 장치 없이 캠페인을 돌리지 마라.

## 자가 진단

연결 상태·할당량·큐를 사용자가 물으면 `state.py summary`/`check`/`followup_due`로 답한다. `{"ok": false, "degraded": true}`면 원인은 셋 중 하나: 온보딩 미완(6번), 웹앱 재배포로 URL 변경, 토큰 불일치 — 5~6번을 다시 확인한다. degraded 상태로도 원고 작성·적재는 진행 가능하다(발송 시 Code.gs가 게이트).
