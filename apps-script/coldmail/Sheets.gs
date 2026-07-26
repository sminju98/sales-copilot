/**
 * sales-copilot 콜드메일 엔진 — Google Sheets 대장 스키마 & 접근 계층
 *
 * (원본: salesfactory-plugin skills/salesfactory/apps-script/Sheets.gs — sales-copilot 통합판.
 *  코드는 원본 그대로, 갱신 지점: legalBasis에 SALES_PROPOSAL_1TO1 추가(W2c),
 *  drafts.status의 PAUSED/READY 매핑 명시(W2d), ss_() 신규 생성 이름을 sales-copilot으로.)
 *
 * 상태(발송대장·수신거부·팔로업큐·설정)를 사용자 자기 스프레드시트에 둔다.
 * 별도 서버 0. Claude(플러그인)는 scripts/state.py를 통해 이 시트를 읽고 drafts에 적재하며,
 * 발송은 Code.gs dispatchTick()이 이 시트를 소비한다.
 *
 * 시트 6개: config / contacts / drafts / sends / suppression / followup_queue
 *
 * 설계 원칙: 모든 컬럼 접근은 이 파일의 헤더 상수 + colIndex_()로만. 하드코딩된 열 번호 금지
 * (컬럼 추가 시 전 코드가 깨지는 것 방지).
 */

// ── 시트 이름 ────────────────────────────────────────────────
const SHEET = {
  CONFIG: 'config',
  CONTACTS: 'contacts',
  DRAFTS: 'drafts',
  SENDS: 'sends',
  SUPPRESSION: 'suppression',
  FOLLOWUP: 'followup_queue',
};

// ── 헤더 정의 (순서 = 열 순서. 뒤에만 추가할 것) ──────────────
const HEADERS = {
  // 단일 행 key-value 가 아니라 [key, value] 2열 테이블
  config: ['key', 'value'],

  contacts: [
    'contactId',      // = normalize_contact.py 가 만든 결정론 키 (base64(lower(email)) 또는 회사+직책 해시)
    'email',          // 공개 출처 실재 주소만. 국내 트랙은 비어있을 수 있음(대표번호/문의폼으로)
    'name', 'company', 'title', 'country',
    'locale',         // ko | en (회사 소재국·사이트 언어 판정. 이름으로 국적 추론 금지)
    'audience',       // agency | brand
    'sourceType',     // webform | naver_openapi | localdata | dart | public_page | manual | direct_deal
    'sourceUrl',      // 수집 출처 (공개 페이지면 URL). 없으면 발송 게이트에서 차단
    'matchedSnippet', // 그 페이지에 이메일이 문자 그대로 있던 근거 스니펫
    'legalBasis',     // NONE | SALES_PROPOSAL_1TO1 | TRANSACTION_6M | BUSINESS_CARD_B2B | EXPLICIT_CONSENT (기본 NONE, 08 판정)
    'dealClosedAt',   // 거래관계 예외용 최종거래완료일 (ISO). TRANSACTION_6M 일 때 필수
    'dealItem',       // 거래 품목 (동종 판단)
    'hookContext',    // 개인화 후크 (04). 근거 URL 포함
    'poolStatus',     // backlog | drafted | sending | done | blocked
    'createdAt',
  ],

  drafts: [
    'draftId',        // = contactId + '_' + channel(cold|fu1)  ← 멱등키 (타임스탬프 금지)
    'contactId',
    'channel',        // cold | fu1
    'subject',
    'bodyMd',         // Claude 가 쓴 본문(마크다운). 발송 시 렌더러가 국내 표시의무 요소 삽입
    'status',         // pending_review(=PAUSED: 외부 행 기본 적재 상태) | approved(=READY: dispatchTick이 집는 유일 상태 — 사용자 승인 후에만) | sending | sent | failed | cancelled (W2d)
    'earliestSendAt', // 이 시각 이후 발송 가능 (fu1 은 cold 발송 + 7일)
    'createdAt',
  ],

  sends: [
    'sendId',         // = draftId  ← 멱등. 이미 있으면 재발송 금지 (dispatchTick 1번 게이트)
    'contactId', 'channel', 'email',
    'sentAt',
    'gmailMessageId', // 답장 매칭용
    'status',         // sent | failed
    'error',
  ],

  suppression: [
    'emailHmac',      // 원문 아닌 HMAC. "삭제했다면서 왜 갖고있냐" 모순 방지
    'domain',         // 도메인 단위 차단도 지원
    'reason',         // unsubscribe | bounce | complaint | manual | replied
    'createdAt',
  ],

  followup_queue: [
    'contactId',
    'dueAt',          // 팔로업 발송 예정 (cold sentAt + 7일)
    'status',         // pending | done | cancelled
    'reason',         // '7d_no_reply' | cancelled 사유(replied 등)
  ],
};

// ── config 기본값 ────────────────────────────────────────────
// 타깃 = 회사 도메인 Workspace 사장(영업은 회사메일로 한다). 개인 Gmail은 예외 케이스.
const CONFIG_DEFAULTS = {
  ACCOUNT_TYPE: 'workspace',       // workspace 기본(타깃=회사 도메인). consumer는 온보딩에서 강등
  LIVE_SEND: 'false',              // 사용자가 명시적으로 'true' 로 켜야 실발송 (기본 dry-run)
  DAILY_TARGET: '50',              // 하루 신규 목표. Workspace 캡 1,500이라 여유. 램프업이 실제 상향
  PACING_MINUTES: '5',             // Workspace 트리거 6시간/일 예산이면 5분 페이싱 여유. consumer면 온보딩이 15로
  SEND_WINDOW_START: '9',          // 업무시간 발송 (로컬 시)
  SEND_WINDOW_END: '18',
  BOUNCE_PAUSE_RATE: '0.02',       // 바운스율 2% 초과 자동 감속→정지
  COMPLAINT_PAUSE_RATE: '0.001',   // 스팸신고율 0.1% 목표 — 회사 도메인 평판 보호(발송 도메인 격리 권장, 07)
  SENDER_NAME: '',                 // 국내 발송 필수 (표시의무). 비면 KR hard block
  SENDER_PHONE: '',                // 국내 발송 필수
  SENDER_ADDRESS: '',              // 국내 발송 필수
};

// ── 계정유형별 하드 상한 (P0-1 확정, 사용자 상향 불가) ─────────
const QUOTA = {
  consumer:  { DAILY_SEND_CAP: 100,  TRIGGER_RUNTIME_SEC: 5400,  GMAIL_READ_CAP: 20000 },
  workspace: { DAILY_SEND_CAP: 1500, TRIGGER_RUNTIME_SEC: 21600, GMAIL_READ_CAP: 50000 },
};
const RECIPIENTS_PER_MSG = 50; // 양쪽 동일

// ── 시트 접근 헬퍼 ───────────────────────────────────────────
function ss_() {
  return SpreadsheetApp.getActiveSpreadsheet()
    || SpreadsheetApp.create('sales-copilot 콜드메일 대장');
}

/** 시트 6개 + 헤더 + config 기본값을 멱등 생성. 온보딩 1회 호출.
 *  HEADERS 키 === 시트 이름 === SHEET.* 값 (셋이 일치). 시트명은 HEADERS 키를 직접 쓴다
 *  (SHEET[key.toUpperCase()]는 'followup_queue'→'FOLLOWUP_QUEUE' 불일치로 깨졌던 버그). */
function initSheets() {
  const ss = ss_();
  Object.keys(HEADERS).forEach(function (name) { // name = HEADERS 키 = 시트 이름
    let sh = ss.getSheetByName(name);
    if (!sh) sh = ss.insertSheet(name);
    if (sh.getLastRow() === 0) sh.appendRow(HEADERS[name]);
  });
  // config 기본값 채우기 (없는 키만)
  const cfg = getSheet_(SHEET.CONFIG);
  const existing = {};
  cfg.getDataRange().getValues().slice(1).forEach(function (r) { existing[r[0]] = true; });
  Object.keys(CONFIG_DEFAULTS).forEach(function (k) {
    if (!existing[k]) cfg.appendRow([k, CONFIG_DEFAULTS[k]]);
  });
  return '초기화 완료';
}

function getSheet_(name) {
  const sh = ss_().getSheetByName(name);
  if (!sh) throw new Error('시트 없음: ' + name + ' — initSheets() 먼저 실행');
  return sh;
}

/** 헤더명 → 0-based 열 인덱스. 하드코딩 열번호 대신 항상 이걸로. */
function colIndex_(name, header) {
  const i = HEADERS[name].indexOf(header);
  if (i < 0) throw new Error('알 수 없는 컬럼: ' + name + '.' + header);
  return i;
}

// ── config 읽기/쓰기 ─────────────────────────────────────────
function getConfig_() {
  const rows = getSheet_(SHEET.CONFIG).getDataRange().getValues().slice(1);
  const out = Object.assign({}, CONFIG_DEFAULTS);
  rows.forEach(function (r) { if (r[0]) out[r[0]] = String(r[1]); });
  return out;
}

/** 계정유형 반영된 실효 상한 */
function effectiveQuota_() {
  const cfg = getConfig_();
  const t = cfg.ACCOUNT_TYPE === 'workspace' ? 'workspace' : 'consumer';
  return QUOTA[t];
}
