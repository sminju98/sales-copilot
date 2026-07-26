/**
 * sales-copilot 콜드메일 엔진 — 할당량 실측 하네스 (Google Apps Script)
 *
 * (원본: salesfactory-plugin skills/salesfactory/apps-script/quota-harness.gs —
 *  코드 그대로, 사용자 노출 문자열(생성 스프레드시트 이름)만 sales-copilot으로 교체.
 *  선택 도구: 본인 계정의 실제 발송 할당량·트리거 예산을 실측하고 싶을 때만 쓴다.)
 *
 * 목적: 발송 층의 추측 수치를, 본인 계정에서 실측한 값으로 확정.
 * 배경: 공식 할당량은 consumer @gmail.com = 발송 100/일·트리거 90분/일,
 *       Workspace = 1,500/일·6시간/일. 답장 폴링이 Gmail read(consumer 20,000/일)를
 *       갉아먹는 숨은 2차 병목. read '단위 정의'는 공식 미공개라 실측 필요.
 *
 * 사용법:
 *   1) script.google.com → 새 프로젝트 → 이 파일 전체 붙여넣기 → 저장
 *   2) TEST_RECIPIENT 를 본인 주소로 교체
 *   3) snapshotQuota() 실행 (최초 실행 시 "확인되지 않은 앱" 경고 →
 *      고급 → 안전하지 않은 페이지로 이동 → 허용. 이 화면 자체도 P0-2 실측 대상)
 *   4) measureSendCycle() 몇 번 실행 → 사이클 실행시간·read 소모 관찰
 *   5) installPacingProbe() → 5분 트리거로 며칠 자동 누적
 *   6) summarize() → 트리거 예산 대비 %·발송 소진·read 누적 확인
 *   7) removePacingProbe() → 측정 종료
 *
 * ⚠️ Google Ads 정지 사고(2026-07) 교훈: 실계정 반복 발송은 남용 탐지로 차단될 수 있음.
 *    소량·신중히. TEST_RECIPIENT 는 반드시 본인 주소.
 */

const TEST_RECIPIENT   = 'YOUR_EMAIL@gmail.com'; // ← 본인 주소로 교체
const HARNESS_SHEET    = 'quota_log';
const SEARCH_QUERY     = 'is:unread newer_than:7d'; // 실제 폴링과 유사한 쿼리로 read 소모 관찰
const THREADS_TO_READ  = 20; // 폴링 1사이클이 조회하는 스레드 수 가정치 (조정해 read 곡선 관찰)

function qlogSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet()
    || SpreadsheetApp.create('sales-copilot quota 하네스');
  let sh = ss.getSheetByName(HARNESS_SHEET);
  if (!sh) {
    sh = ss.insertSheet(HARNESS_SHEET);
    sh.appendRow(['ts', 'event', 'remaining_send', 'cycle_ms', 'threads_read', 'note']);
  }
  return sh;
}

/** 1) 남은 일일 발송 할당량(수신자 기준). MailApp/GmailApp 공유 카운터. */
function snapshotQuota() {
  const remaining = MailApp.getRemainingDailyQuota(); // 실제 공식 API
  qlogSheet_().appendRow([new Date(), 'snapshot', remaining, '', '', '']);
  Logger.log('남은 발송 할당량(수신자): ' + remaining);
  return remaining;
}

/** 2) 대표 사이클 실행시간 측정: 검색→읽기→자기자신 발송 1건. */
function measureSendCycle() {
  const t0 = Date.now();

  // (a) 답장 폴링 시뮬레이션: 검색 + 스레드 메시지 읽기 (Gmail read 소모)
  const threads = GmailApp.search(SEARCH_QUERY, 0, THREADS_TO_READ);
  let readCount = 0;
  threads.forEach(function (th) {
    th.getMessages().forEach(function (m) { m.getFrom(); readCount++; });
  });

  // (b) 발송 1건 (발송 카운터 1 소모) — MailApp = script.send_mail(민감 scope, 제한 아님)
  const before = MailApp.getRemainingDailyQuota();
  MailApp.sendEmail(TEST_RECIPIENT, '[하네스] cycle probe', '실행시간 측정용');
  const after = MailApp.getRemainingDailyQuota();

  const elapsed = Date.now() - t0;
  qlogSheet_().appendRow([
    new Date(), 'cycle', after, elapsed, readCount,
    'send_delta=' + (before - after) // 발송 1건이 카운터를 몇 감소시키는지 확인
  ]);
  Logger.log('사이클 ' + elapsed + 'ms / 읽은 메시지 ' + readCount +
             ' / 발송카운터 감소 ' + (before - after) + ' / 남은발송 ' + after);
  return { elapsed: elapsed, readCount: readCount, remaining: after };
}

/** 3) 5분 트리거 설치: 며칠 돌려 트리거 90분/일(consumer) 예산·read 접근·발송 소진 관찰. */
function installPacingProbe() {
  removePacingProbe();
  ScriptApp.newTrigger('measureSendCycle')
    .timeBased().everyMinutes(5).create();
  Logger.log('5분 페이싱 프로브 설치. summarize()로 누적 확인, 종료 시 removePacingProbe().');
}

function removePacingProbe() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'measureSendCycle') ScriptApp.deleteTrigger(t);
  });
}

/** 4) 누적 요약: 총 사이클 실행시간(트리거 예산 대비), 평균/최대, 발송 소진 곡선. */
function summarize() {
  const rows = qlogSheet_().getDataRange().getValues().slice(1);
  const cycles = rows.filter(function (r) { return r[1] === 'cycle' && r[3] !== ''; });
  if (!cycles.length) { Logger.log('cycle 데이터 없음'); return; }
  const totalMs = cycles.reduce(function (s, r) { return s + Number(r[3]); }, 0);
  const maxMs   = Math.max.apply(null, cycles.map(function (r) { return Number(r[3]); }));
  const avgMs   = Math.round(totalMs / cycles.length);
  const totalReads = cycles.reduce(function (s, r) { return s + Number(r[4] || 0); }, 0);

  // consumer 트리거 예산 90분=5,400,000ms / Workspace 6시간=21,600,000ms
  Logger.log(
    'cycle수=' + cycles.length +
    ' | 누적실행 ' + Math.round(totalMs / 1000) + 's' +
    ' (consumer예산 5400s 대비 ' + (totalMs / 5400000 * 100).toFixed(1) + '%)' +
    ' | 평균 ' + avgMs + 'ms / 최대 ' + maxMs + 'ms' +
    ' | 누적읽은메시지 ' + totalReads +
    ' (Gmail read 20000/일 대비 참고, read 단위는 공식 미명시)'
  );
}
