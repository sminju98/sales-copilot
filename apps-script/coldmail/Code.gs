/**
 * sales-copilot 콜드메일 엔진 — 발송층 (사용자 Google 계정 Apps Script)
 *
 * (원본: salesfactory-plugin skills/salesfactory/apps-script/Code.gs — sales-copilot 통합판.
 *  변경 4곳, 나머지는 원본 그대로:
 *  ① koreanGate_ — legalBasis에 SALES_PROPOSAL_1TO1(1:1 개별 영업 제안, 08 '영업 메일 판정') 추가.
 *  ② renderEmail_ — SALES_PROPOSAL_1TO1이면 (광고) 미표기 + 투명성 푸터, 광고성 경로는 원본 그대로.
 *  ③ dispatchTick 가드 0 — 내부 테스트 1건 의무를 코드로 강제(internalTestDone_, W2d ②).
 *  ④ doPost append_contacts/append_drafts — 적재 API 구현, 외부 행 status를 서버가
 *     pending_review(PAUSED)로 강제(apiAppendDrafts_, W2d ①).)
 *
 * dispatchTick(): time-driven 트리거로 페이싱마다 1회 실행. drafts 시트의 approved 초안을
 * 하나씩 발송한다. 모든 결정론 가드가 LockService 락 안에서 원자적으로 수행된다(중복 발송 0).
 *
 * ★ W2d 발송 안전장치 — drafts.status 컨벤션 (nextApprovedDraft_ 실제 동작 기준):
 *   dispatchTick은 status='approved'(=READY)인 행만 집는다. 그 외 상태는 무엇이든 발송되지 않는다.
 *   외부 주소 행은 'pending_review'(=PAUSED)로 적재된다 — append_drafts API가 서버측에서
 *   강제하므로 클라이언트가 다른 status를 보내도 무시된다. 승인(approved 전환)은 사용자가
 *   시트에서 직접 한다. 첫 캠페인 전 내 주소 테스트 1건(references/09 7번)도 코드 강제:
 *   dispatchTick 가드 0이 sends의 내 주소 실발송 이력을 검사해 없으면 외부 행 발송을 거부한다
 *   — approval_mode=auto여도 생략 불가. LIVE_SEND='true'가 아니면 approved여도 dry-run(실발송 0건).
 *
 * 설계 정본: skills/outreach/references/07-deliverability.md, 08-legal-kr.md
 * 상수·스키마: Sheets.gs
 *
 * ⚠️ 발송 scope는 gmail.send (민감). mail.google.com(제한) 금지 — OAuth 하드블록 회피.
 *    받은편지함 읽기 scope 미요청 — 답장추적은 시트 상태로만.
 *
 * ⚠️ 첫 실발송 전에는 LIVE_SEND=false(dry-run) 유지. 실계정 반복 발송 금지(Google Ads 정지 교훈).
 */

// ── 트리거 설치/제거 (온보딩) ────────────────────────────────
function installDispatchTrigger() {
  removeDispatchTrigger();
  const mins = parseInt(getConfig_().PACING_MINUTES, 10) || 15;
  // Apps Script는 everyMinutes에 1/5/10/15/30만 허용 → 가장 가까운 값으로 스냅
  const allowed = [1, 5, 10, 15, 30];
  const snap = allowed.reduce(function (a, b) {
    return Math.abs(b - mins) < Math.abs(a - mins) ? b : a;
  });
  ScriptApp.newTrigger('dispatchTick').timeBased().everyMinutes(snap).create();
  return '페이싱 트리거 설치: ' + snap + '분';
}

function removeDispatchTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'dispatchTick') ScriptApp.deleteTrigger(t);
  });
}

// ── 발송 1틱 (핵심) ──────────────────────────────────────────
function dispatchTick() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return; // 이전 틱 진행 중이면 skip (동시성 방지)
  try {
    const cfg = getConfig_();
    const quota = effectiveQuota_();

    // A. 자동 정지 조건 (바운스·신고율) — references/07
    if (shouldAutoPause_(cfg)) { logRun_('paused', '바운스/신고율 임계 초과 — 자동 정지'); return; }

    // B. 발송 창(업무시간) 밖이면 skip
    if (!inSendWindow_(cfg)) { logRun_('skip', '발송 시간대 아님'); return; }

    // C. 오늘 발송 카운터 vs 상한 (계정유형 + DAILY_TARGET 의 MIN)
    const sentToday = countSentToday_();
    const cap = Math.min(quota.DAILY_SEND_CAP, parseInt(cfg.DAILY_TARGET, 10) || quota.DAILY_SEND_CAP);
    // TODO(P0): SAFETY_MARGIN 0.8 적용 여부 — 창업자 승인 후
    if (sentToday >= cap) { logRun_('skip', '오늘 상한 도달 ' + sentToday + '/' + cap); return; }

    // D. 다음 발송할 approved 초안 1건 (earliestSendAt 도래한 것)
    const draft = nextApprovedDraft_();
    if (!draft) { logRun_('idle', '발송 대기 초안 없음'); return; }

    const contact = getContact_(draft.contactId);
    if (!contact) { markDraft_(draft.draftId, 'failed', 'contact 없음'); return; }

    // ── 결정론 가드 (references/07·08 순서) ──
    // 0. 내부 테스트 의무 — 코드 강제 (W2d ②, references/09 7번): LIVE 모드에서
    //    외부 주소로의 첫 실발송 전, sends에 내 주소(스크립트 소유자) 앞 실발송 성공
    //    기록이 1건 이상 있어야 한다. 없으면 외부 행 발송을 거부하고 대기(초안 상태 유지
    //    — 테스트 완료 후 다음 틱에 자동 재개). approval_mode=auto·approved여도 못 넘는다.
    //    dry-run(LIVE_SEND=false)은 실발송이 없으므로 이 가드를 타지 않는다.
    if (cfg.LIVE_SEND === 'true' && !isSelfRecipient_(contact) && !internalTestDone_()) {
      logRun_('blocked', '내부 테스트 미완료 — 외부 발송 보류: ' + contact.email +
        ' (내 주소(' + _selfEmail_() + ')로 테스트 1건을 먼저 실발송·확인하세요. references/09 7번)');
      return;
    }
    // 1. 멱등: 이미 보낸 sendId(=draftId)면 즉시 skip
    if (sendExists_(draft.draftId)) { markDraft_(draft.draftId, 'sent', '이미 발송(멱등 skip)'); return; }
    // 2. suppression (email HMAC + domain)
    if (isSuppressed_(contact.email)) { markDraft_(draft.draftId, 'cancelled', 'suppression'); return; }
    // 3. 출처 실재 (공개 출처 URL 없으면 차단 — 추측 주소 방지)
    if (!contact.sourceUrl) { markDraft_(draft.draftId, 'failed', 'sourceUrl 없음'); return; }
    // 4. 국내 트랙 hard block (references/08) — legal_basis 증빙 없으면 국내 발송 불가
    const krGate = koreanGate_(contact, cfg);
    if (!krGate.ok) { markDraft_(draft.draftId, 'cancelled', 'KR: ' + krGate.reason); return; }

    // 5. 렌더 (국내면 (광고)+4항목+수신거부 강제 삽입)
    const rendered = renderEmail_(draft, contact, cfg);

    // 6. 발송 (dry-run 기본). dry/live 는 실제 MailApp 호출 유무만 다르고
    //    나머지 상태 전이(draft·sends·contact·followup)는 동일 — dry-run이 전 파이프라인을 검증.
    const live = cfg.LIVE_SEND === 'true';
    try {
      if (live) {
        // Gmail Advanced Service messages.send(raw) — MailApp과 달리 커스텀 헤더(List-Unsubscribe)
        // 삽입 가능. scope는 gmail.send(민감)로 동일 — mail.google.com(제한) 회피(P0-2).
        sendRawEmail_({
          fromName: cfg.SENDER_NAME, fromEmail: _selfEmail_(),
          to: contact.email, subject: rendered.subject, html: rendered.html,
          listUnsub: rendered.listUnsub, listUnsubMailto: rendered.listUnsubMailto,
        });
      } else {
        logRun_('dryrun', 'DRY-RUN → ' + contact.email + ' | ' + rendered.subject +
          (rendered.listUnsub ? ' | List-Unsubscribe: ' + rendered.listUnsub : ''));
      }
      markDraft_(draft.draftId, 'sent', live ? '' : 'DRY-RUN');
      recordSend_(draft, contact, live ? '' : 'DRYRUN', 'sent', '');
      markContactStatus_(contact.contactId, 'done');           // 이미 보낸 상대 표시(state.py check)
      maybeEnqueueFollowup_(draft, contact);                   // cold 발송 성공 시 fu 큐잉(7일)
    } catch (e) {
      markDraft_(draft.draftId, 'failed', String(e));
      recordSend_(draft, contact, '', 'failed', String(e));
    }
  } finally {
    lock.releaseLock();
  }
}

// ── 국내 트랙 게이트 (references/08 '영업 메일 판정') ──────────
function koreanGate_(contact, cfg) {
  if (!isKoreanRecipient_(contact)) return { ok: true };           // 해외 트랙은 통과
  // 국내 = legalBasis 필요: SALES_PROPOSAL_1TO1(1:1 개별 영업 제안 판정) 또는 동의 경로.
  const basis = (contact.legalBasis || 'NONE').toUpperCase();
  if (basis === 'NONE') return { ok: false, reason: "판정 없음(08 '영업 메일 판정' 또는 동의 경로 필요)" };
  if (basis === 'SALES_PROPOSAL_1TO1') {
    // [sales-copilot 수정 ①] W2c: 광고성 정보가 아닌 1:1 개별 거래 제안 — 발송 허용.
    // 단 판정 조건 ①(1:1 개별 작성)의 결정론 증빙으로 hookContext(개인화 근거+출처) 필수.
    if (!contact.hookContext) return { ok: false, reason: '1:1 개별 작성 근거(hookContext) 없음' };
  }
  if (basis === 'TRANSACTION_6M') {
    if (!contact.dealClosedAt) return { ok: false, reason: '거래완료일 없음' };
    const days = (Date.now() - new Date(contact.dealClosedAt).getTime()) / 86400000;
    if (days > 180) return { ok: false, reason: '거래 6개월 초과' };
    // dealItem 동종 여부는 사람이 입력 시 검증 — 여기선 존재만 체크
    if (!contact.dealItem) return { ok: false, reason: '거래품목(동종) 없음' };
  }
  // 국내 발송이면 발신자 4항목 필수 (표시의무)
  if (!cfg.SENDER_NAME || !cfg.SENDER_PHONE || !cfg.SENDER_ADDRESS) {
    return { ok: false, reason: '발신자 명칭/전화/주소 미입력(표시의무)' };
  }
  return { ok: true };
}

function isKoreanRecipient_(contact) {
  const email = (contact.email || '').toLowerCase();
  const domain = email.split('@')[1] || '';
  if ((contact.country || '').toLowerCase().indexOf('korea') >= 0) return true;
  if ((contact.locale || '') === 'ko') return true;
  if (/\.kr$|\.co\.kr$|\.or\.kr$/.test(domain)) return true;
  const krHosts = ['naver.com', 'daum.net', 'hanmail.net', 'kakao.com', 'nate.com'];
  if (krHosts.indexOf(domain) >= 0) return true;
  return false; // 판정 불가는 해외로 (국내 리드는 애초에 locale=ko/country 세팅됨)
}

// ── 렌더러 (references/08 §5) ─────────────────────────────────
function renderEmail_(draft, contact, cfg) {
  let subject = draft.subject || '';
  let bodyHtml = mdToHtml_(draft.bodyMd || '');

  // 수신거부 토큰 = hmac_(email). 웹앱(one-click)·mailto 양쪽에 실린다.
  // 웹앱이 이 토큰을 그대로 suppression.emailHmac 에 저장 → 이후 isSuppressed_(email) 이 재계산해 매칭.
  // (HMAC 이라 역산 불가·위조 불가 — 시크릿 없이는 남의 주소 토큰 생성 불가)
  const token = hmac_(String(contact.email || '').trim().toLowerCase());
  const web = _webappUrl_();
  const httpsUnsub = web ? (web + '?u=' + encodeURIComponent(token)) : '';
  const mailtoUnsub = 'mailto:' + _selfEmail_() + '?subject=' + encodeURIComponent('unsubscribe ' + token);
  const visibleLink = httpsUnsub || mailtoUnsub;

  if (isKoreanRecipient_(contact)) {
    // [sales-copilot 수정 ②] W2c: 국내는 legalBasis로 렌더링이 갈린다 (references/08).
    var krBasis = String(contact.legalBasis || 'NONE').toUpperCase();
    if (krBasis === 'SALES_PROPOSAL_1TO1') {
      // 1:1 개별 영업 제안 — 광고성 정보가 아니라는 판정이므로 (광고) 표기하지 않는다
      // (표기하면 오히려 허위 표시). 대신 판정 조건 ③④의 코드 보증:
      // 발신자 3항목(명칭·전화·주소) + 정중한 수신거부 링크를 푸터로 강제.
      bodyHtml +=
        '<hr><p style="font-size:12px;color:#666">' +
        '수신을 원치 않으시면 <a href="' + escapeAttr_(visibleLink) + '">[수신거부/Unsubscribe]</a>를 눌러 주세요. 더 연락드리지 않겠습니다.<br>' +
        escapeHtml_(cfg.SENDER_NAME) + ' | ' + escapeHtml_(cfg.SENDER_PHONE) + ' | ' + escapeHtml_(cfg.SENDER_ADDRESS) +
        '</p>';
    } else {
      // 광고성 경로(옵트인·거래관계 예외 등) — 원본 그대로: 제목 (광고) 멱등 prepend (변칙표기 방지)
      subject = subject.replace(/^\s*[\(（【\[]?\s*광\s*고\s*[\)）】\]]?\s*/, ''); // 기존 변형 제거
      subject = '(광고) ' + subject;
      // 본문 하단 법정 4항목(명칭·전화·주소) + 무료 수신거부
      bodyHtml +=
        '<hr><p style="font-size:12px;color:#666">' +
        '본 메일은 (광고)입니다. 수신을 원치 않으시면 <a href="' + escapeAttr_(visibleLink) + '">[수신거부/Unsubscribe]</a>를 눌러 주세요(무료).<br>' +
        escapeHtml_(cfg.SENDER_NAME) + ' | ' + escapeHtml_(cfg.SENDER_PHONE) + ' | ' + escapeHtml_(cfg.SENDER_ADDRESS) +
        '</p>';
    }
  } else {
    // 해외: 최소 수신거부 링크(위생·CAN-SPAM). 발신자 우편주소는 SENDER_ADDRESS 로.
    bodyHtml +=
      '<hr><p style="font-size:12px;color:#666">' +
      '<a href="' + escapeAttr_(visibleLink) + '">Unsubscribe</a>' +
      (cfg.SENDER_ADDRESS ? ' · ' + escapeHtml_(cfg.SENDER_ADDRESS) : '') +
      '</p>';
  }
  return { subject: subject, html: bodyHtml, listUnsub: httpsUnsub, listUnsubMailto: mailtoUnsub };
}

// ── raw MIME 발송 (Gmail Advanced Service) ───────────────────
// MailApp이 못 싣는 List-Unsubscribe/List-Unsubscribe-Post(RFC 8058)를 헤더로 넣는다.
function sendRawEmail_(o) {
  const raw = Utilities.base64EncodeWebSafe(buildMime_(o), Utilities.Charset.UTF_8);
  Gmail.Users.Messages.send({ raw: raw }, 'me'); // scope: gmail.send (민감)
}

function buildMime_(o) {
  const CRLF = '\r\n';
  const from = o.fromName ? (rfc2047_(o.fromName) + ' <' + o.fromEmail + '>') : o.fromEmail;
  const unsub = [];
  if (o.listUnsubMailto) unsub.push('<' + o.listUnsubMailto + '>');
  if (o.listUnsub) unsub.push('<' + o.listUnsub + '>');

  const lines = [];
  lines.push('From: ' + from);
  lines.push('To: ' + o.to);
  lines.push('Subject: ' + rfc2047_(o.subject || ''));
  lines.push('MIME-Version: 1.0');
  lines.push('Content-Type: text/html; charset="UTF-8"');
  lines.push('Content-Transfer-Encoding: base64');
  if (unsub.length) {
    lines.push('List-Unsubscribe: ' + unsub.join(', '));
    if (o.listUnsub) lines.push('List-Unsubscribe-Post: List-Unsubscribe=One-Click'); // one-click은 https만
  }
  lines.push(''); // 헤더/본문 구분
  lines.push(chunk76_(Utilities.base64Encode(o.html || '', Utilities.Charset.UTF_8)));
  return lines.join(CRLF);
}

/** RFC 2047 encoded-word. ASCII면 그대로, 아니면 UTF-8 B-encoding 을 75자 이하로 접어(fold) 연결. */
function rfc2047_(s) {
  s = String(s || '');
  if (/^[\x20-\x7E]*$/.test(s)) return s;
  const words = [];
  let cur = '';
  const cps = Array.from(s); // 코드포인트 단위 (멀티바이트 분할 방지)
  for (let i = 0; i < cps.length; i++) {
    const test = cur + cps[i];
    // encoded-word 전체 길이(=?UTF-8?B?...?=)가 75 이하가 되도록 base64 60자 근처에서 끊음
    if (Utilities.base64Encode(test, Utilities.Charset.UTF_8).length > 60 && cur) {
      words.push('=?UTF-8?B?' + Utilities.base64Encode(cur, Utilities.Charset.UTF_8) + '?=');
      cur = cps[i];
    } else {
      cur = test;
    }
  }
  if (cur) words.push('=?UTF-8?B?' + Utilities.base64Encode(cur, Utilities.Charset.UTF_8) + '?=');
  return words.join('\r\n '); // CRLF+SP 로 접기
}

/** base64 본문을 76자 줄로 분할 (RFC 2045) */
function chunk76_(b64) {
  return (String(b64).match(/.{1,76}/g) || ['']).join('\r\n');
}

function _selfEmail_() { return Session.getActiveUser().getEmail() || ''; }
function _webappUrl_() { return PropertiesService.getScriptProperties().getProperty('SF_WEBAPP_URL') || ''; }
function escapeAttr_(s) { return escapeHtml_(s).replace(/'/g, '&#39;'); }

// ── 자동 정지 / 발송창 ───────────────────────────────────────
function shouldAutoPause_(cfg) {
  const stats = sendStats_(); // {total, bounced, complaints}
  if (stats.total < 20) return false; // 표본 부족
  if (stats.bounced / stats.total > parseFloat(cfg.BOUNCE_PAUSE_RATE)) return true;
  if (stats.complaints / stats.total > parseFloat(cfg.COMPLAINT_PAUSE_RATE)) return true;
  return false;
}

function inSendWindow_(cfg) {
  const tz = ss_().getSpreadsheetTimeZone();
  const h = parseInt(Utilities.formatDate(new Date(), tz, 'H'), 10);
  const dow = parseInt(Utilities.formatDate(new Date(), tz, 'u'), 10); // 1=월 7=일
  if (dow >= 6) return false; // 주말 제외
  return h >= parseInt(cfg.SEND_WINDOW_START, 10) && h < parseInt(cfg.SEND_WINDOW_END, 10);
}

// ── 팔로업 큐잉 (references/06) ──────────────────────────────
function maybeEnqueueFollowup_(draft, contact) {
  if (draft.channel !== 'cold') return;
  const due = new Date(Date.now() + 7 * 86400000).toISOString(); // 7일 후
  appendRow_(SHEET.FOLLOWUP, { contactId: contact.contactId, dueAt: due, status: 'pending', reason: '7d_no_reply' });
}

// ─────────────────────────────────────────────────────────────
// 웹앱 엔드포인트 — scripts/state.py 의 상대편.
//   Claude(플러그인)가 stdlib HTTP 로 상태를 조회/적재하는 유일 창구.
//   Google 인증은 여기(Apps Script)에 갇힌다. Python 은 순수 HTTP.
//   배포: 배포 > 새 배포 > 웹앱 > "실행: 나" / "액세스: 링크가 있는 모든 사용자".
//         URL(…/exec)과 아래 TOKEN 을 온보딩(references/09)에서 sales-copilot config의
//         coldmail.sheet_webhook_url·coldmail.sheet_webhook_secret 으로 저장.
//   ⚠️ TOKEN 은 PropertiesService 에 두고 코드에 하드코딩하지 말 것(배포 시 온보딩이 설정).
// ─────────────────────────────────────────────────────────────
function doPost(e) {
  // RFC 8058 one-click 수신거부: 수신자 메일 제공자가 URL(?u=TOKEN)로 POST(body=List-Unsubscribe=One-Click).
  // 토큰(hmac_(email)) 자체가 capability — API 토큰 인증 불요. 위조 불가(시크릿 없이 생성 불가).
  var u = e && e.parameter && e.parameter.u;
  if (u) { addSuppressionByHmac_(u, 'unsubscribe'); return _json_({ ok: true, unsubscribed: true }); }

  var out = { ok: false };
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (body.token !== _apiToken_()) return _json_({ ok: false, reason: 'unauthorized' });
    switch (body.action) {
      case 'summary':       out = apiSummary_(); break;
      case 'sent_today':    out = { ok: true, sentToday: countSentToday_() }; break;
      case 'check':         out = apiCheck_(body.email); break;
      case 'followup_due':  out = apiFollowupDue_(); break;
      case 'config':        out = { ok: true, config: getConfig_() }; break;
      // 적재(Claude가 contacts/drafts 를 쓰는 경로) — W2d ① 코드 보증:
      // 외부 행은 서버가 status='pending_review'(PAUSED)로 강제 적재 (apiAppendDrafts_ 참조).
      case 'append_contacts': out = apiAppendContacts_(body.contacts || []); break;
      case 'append_drafts':   out = apiAppendDrafts_(body.drafts || []); break;
      default:              out = { ok: false, reason: 'unknown_action' };
    }
  } catch (err) {
    out = { ok: false, reason: 'exception', detail: String(err) };
  }
  return _json_(out);
}

// 사람이 브라우저로 수신거부 링크 클릭(GET) — one-click POST가 아닌 경우.
function doGet(e) {
  var u = e && e.parameter && e.parameter.u;
  if (u) {
    addSuppressionByHmac_(u, 'unsubscribe');
    return HtmlService.createHtmlOutput('<p>수신거부 처리되었습니다. 다시 발송되지 않습니다.<br>You have been unsubscribed.</p>');
  }
  return HtmlService.createHtmlOutput('OK');
}

// 수신거부 토큰(=hmac_(email))을 그대로 suppression.emailHmac 에 저장.
// isSuppressed_(email)이 hmac_(email)을 재계산해 매칭 → 원문 미보관으로도 재발송 차단.
function addSuppressionByHmac_(hmacToken, reason) {
  appendRow_(SHEET.SUPPRESSION, {
    emailHmac: String(hmacToken), domain: '', reason: reason, createdAt: new Date().toISOString(),
  });
}

function _json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function _apiToken_() {
  // 온보딩이 PropertiesService 에 저장. 없으면 빈 문자열 → 모든 요청 unauthorized (안전측).
  return PropertiesService.getScriptProperties().getProperty('SF_API_TOKEN') || '';
}

// state.py 계약에 맞춘 응답 조립 (내부 스텁 재사용)
function apiSummary_() {
  var cfg = getConfig_(), q = effectiveQuota_();
  var sent = countSentToday_();
  var cap = Math.min(q.DAILY_SEND_CAP, parseInt(cfg.DAILY_TARGET, 10) || q.DAILY_SEND_CAP);
  return {
    ok: true, sentToday: sent, capToday: cap, remaining: Math.max(0, cap - sent),
    lastSuccessAt: lastSuccessAt_(), queuePending: followupPendingCount_(),
    accountType: cfg.ACCOUNT_TYPE,
  };
}
function apiCheck_(email) {
  email = String(email || '').trim().toLowerCase();
  if (!email) return { ok: false, reason: 'empty_email' };
  var c = getContactByEmail_(email); // {poolStatus} | null
  return {
    ok: true, email: email,
    contacted: !!(c && (c.poolStatus === 'sending' || c.poolStatus === 'done')),
    suppressed: isSuppressed_(email),
    poolStatus: c ? c.poolStatus : null,
  };
}
function apiFollowupDue_() {
  return { ok: true, due: followupDue_() }; // [{contactId, dueAt}]
}

// ── 적재 API (W2d ① 'PAUSED 기본 적재'의 코드 보증) ──────────
// 클라이언트(Claude/state.py)가 무슨 status를 보내든 서버가 무시하고
// 외부 행은 무조건 'pending_review'(=PAUSED)로 적재한다. approved(READY) 전환은
// 사용자가 시트에서 직접 하는 것이 유일한 경로 (doPost에 상태 변경 액션 없음).
// 유일한 예외: internal_test=true 이고 수신자가 스크립트 소유자 본인 주소인
// 내부 테스트 행 — 그 행만 approved로 적재를 허용한다 (두 조건 모두 필요).
function apiAppendContacts_(list) {
  if (!Array.isArray(list)) return { ok: false, reason: 'contacts_not_array' };
  var appended = 0, skipped = [];
  for (var i = 0; i < list.length; i++) {
    var c = list[i] || {};
    var id = String(c.contactId || '').trim();
    if (!id) { skipped.push({ index: i, reason: 'no_contactId' }); continue; }
    if (getContact_(id)) { skipped.push({ index: i, contactId: id, reason: 'duplicate' }); continue; } // 멱등
    if (!c.createdAt) c.createdAt = new Date().toISOString();
    if (!c.poolStatus) c.poolStatus = 'backlog';
    appendRow_(SHEET.CONTACTS, c); // HEADERS 밖 필드는 appendRow_ 가 버린다
    appended++;
  }
  return { ok: true, appended: appended, skipped: skipped };
}

function apiAppendDrafts_(list) {
  if (!Array.isArray(list)) return { ok: false, reason: 'drafts_not_array' };
  var appended = 0, skipped = [];
  for (var i = 0; i < list.length; i++) {
    var d = list[i] || {};
    var draftId = String(d.draftId || '').trim();
    var contactId = String(d.contactId || '').trim();
    if (!draftId || !contactId) { skipped.push({ index: i, reason: 'no_draftId_or_contactId' }); continue; }
    if (draftExists_(draftId)) { skipped.push({ index: i, draftId: draftId, reason: 'duplicate' }); continue; } // 멱등
    var contact = getContact_(contactId);
    if (!contact) { skipped.push({ index: i, draftId: draftId, reason: 'contact_not_found' }); continue; }
    // ★ 서버측 상태 강제: 외부 행은 클라이언트 status 무시 → pending_review(PAUSED).
    var status = 'pending_review';
    if (d.internal_test === true && isSelfRecipient_(contact)) status = 'approved'; // 내부 테스트 행만 예외
    appendRow_(SHEET.DRAFTS, {
      draftId: draftId, contactId: contactId,
      channel: d.channel || 'cold', subject: d.subject || '', bodyMd: d.bodyMd || '',
      status: status, earliestSendAt: d.earliestSendAt || '',
      createdAt: d.createdAt || new Date().toISOString(),
    });
    appended++;
  }
  return { ok: true, appended: appended, skipped: skipped };
}

function draftExists_(draftId) {
  var data = sheetRows_('drafts').data;
  var idi = colIndex_('drafts', 'draftId');
  for (var i = 0; i < data.length; i++) if (String(data[i][idi]) === String(draftId)) return true;
  return false;
}

// ─────────────────────────────────────────────────────────────
// 시트 I/O 기반 — Sheets.gs HEADERS 스키마 + colIndex_() 로만 접근.
// (sheetName === HEADERS 키 === SHEET.* 값. 셋이 일치하도록 Sheets.gs에서 유지)
// ─────────────────────────────────────────────────────────────
function sheetRows_(name) {
  const sh = getSheet_(name);
  const values = sh.getDataRange().getValues();
  return { sh: sh, header: values[0] || HEADERS[name], data: values.slice(1) };
}
function toObj_(name, rowArr) {
  const h = HEADERS[name], o = {};
  for (let i = 0; i < h.length; i++) o[h[i]] = rowArr[i];
  return o;
}
/** obj → HEADERS 순서 행으로 append. 누락 필드는 빈 문자열. */
function appendRow_(name, obj) {
  const row = HEADERS[name].map(function (k) {
    return obj[k] !== undefined && obj[k] !== null ? obj[k] : '';
  });
  getSheet_(name).appendRow(row);
}
/** data 인덱스(0-based, 헤더 제외) 행의 한 셀 갱신 */
function setCell_(name, dataIdx, colName, value) {
  getSheet_(name).getRange(dataIdx + 2, colIndex_(name, colName) + 1).setValue(value);
}
function toMs_(v) {
  if (!v) return 0;
  const d = (v instanceof Date) ? v : new Date(v);
  const t = d.getTime();
  return isNaN(t) ? 0 : t;
}

// ── drafts ───────────────────────────────────────────────────
function nextApprovedDraft_() {
  const { data } = sheetRows_('drafts');
  const now = Date.now();
  const sIdx = colIndex_('drafts', 'status'), eIdx = colIndex_('drafts', 'earliestSendAt');
  let best = null;
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][sIdx]) !== 'approved') continue;
    const eMs = toMs_(data[i][eIdx]);          // 빈 값(0)이면 즉시 발송 가능
    if (eMs > now) continue;
    if (best === null || eMs < best.eMs) best = { row: i, eMs: eMs };
  }
  return best === null ? null : toObj_('drafts', data[best.row]);
}
function markDraft_(draftId, status, note) {
  const { data } = sheetRows_('drafts');
  const idi = colIndex_('drafts', 'draftId');
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][idi]) === String(draftId)) { setCell_('drafts', i, 'status', status); break; }
  }
  logRun_('draft', draftId + ' → ' + status + (note ? ' (' + note + ')' : ''));
}

// ── contacts ─────────────────────────────────────────────────
function getContact_(id) {
  const { data } = sheetRows_('contacts');
  const ci = colIndex_('contacts', 'contactId');
  for (let i = 0; i < data.length; i++) if (String(data[i][ci]) === String(id)) return toObj_('contacts', data[i]);
  return null;
}
function getContactByEmail_(email) {
  email = String(email || '').trim().toLowerCase();
  const { data } = sheetRows_('contacts');
  const ei = colIndex_('contacts', 'email');
  for (let i = 0; i < data.length; i++) if (String(data[i][ei]).trim().toLowerCase() === email) return toObj_('contacts', data[i]);
  return null;
}
function markContactStatus_(contactId, status) {
  const { data } = sheetRows_('contacts');
  const ci = colIndex_('contacts', 'contactId');
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][ci]) === String(contactId)) { setCell_('contacts', i, 'poolStatus', status); break; }
  }
}

// ── sends (멱등·카운터·통계) ─────────────────────────────────
function sendExists_(sendId) {
  const { data } = sheetRows_('sends');
  const idi = colIndex_('sends', 'sendId'), sti = colIndex_('sends', 'status');
  for (let i = 0; i < data.length; i++) if (String(data[i][idi]) === String(sendId) && String(data[i][sti]) === 'sent') return true;
  return false;
}
/** 수신자가 스크립트 소유자 본인인가 (내부 테스트 행 판정, W2d). */
function isSelfRecipient_(contact) {
  const self = _selfEmail_().trim().toLowerCase();
  return !!self && String(contact.email || '').trim().toLowerCase() === self;
}
/** sends에 내 주소 앞 실발송 성공(sent, DRY-RUN 아님) 기록이 1건 이상 있는가 (W2d 내부 테스트 이력).
 *  dry-run 기록은 gmailMessageId='DRYRUN' 으로 남으므로 제외 — 렌더링·서명·수신거부 문구를
 *  실제 받은편지함에서 확인하는 것이 테스트의 목적이라 실발송만 인정한다. */
function internalTestDone_() {
  const self = _selfEmail_().trim().toLowerCase();
  if (!self) return false;
  const { data } = sheetRows_('sends');
  const ei = colIndex_('sends', 'email'), sti = colIndex_('sends', 'status'), gi = colIndex_('sends', 'gmailMessageId');
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][sti]) !== 'sent') continue;
    if (String(data[i][gi]) === 'DRYRUN') continue;
    if (String(data[i][ei]).trim().toLowerCase() === self) return true;
  }
  return false;
}
function recordSend_(draft, contact, gmailId, status, err) {
  appendRow_(SHEET.SENDS, {
    sendId: draft.draftId, contactId: contact.contactId, channel: draft.channel,
    email: contact.email, sentAt: new Date().toISOString(),
    gmailMessageId: gmailId || '', status: status, error: err || '',
  });
}
function countSentToday_() {
  const tz = ss_().getSpreadsheetTimeZone();
  const today = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  const { data } = sheetRows_('sends');
  const ti = colIndex_('sends', 'sentAt'), sti = colIndex_('sends', 'status');
  let n = 0;
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][sti]) !== 'sent') continue;
    const ms = toMs_(data[i][ti]);
    if (ms && Utilities.formatDate(new Date(ms), tz, 'yyyy-MM-dd') === today) n++;
  }
  return n;
}
function sendStats_() {
  const { data: sends } = sheetRows_('sends');
  const sti = colIndex_('sends', 'status');
  let total = 0;
  for (let i = 0; i < sends.length; i++) if (String(sends[i][sti]) === 'sent') total++;
  const { data: sup } = sheetRows_('suppression');
  const ri = colIndex_('suppression', 'reason');
  let bounced = 0, complaints = 0;
  for (let i = 0; i < sup.length; i++) {
    const r = String(sup[i][ri]);
    if (r === 'bounce') bounced++; else if (r === 'complaint') complaints++;
  }
  return { total: total, bounced: bounced, complaints: complaints };
}
function lastSuccessAt_() {
  const { data } = sheetRows_('sends');
  const ti = colIndex_('sends', 'sentAt'), sti = colIndex_('sends', 'status');
  let best = 0;
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][sti]) !== 'sent') continue;
    const ms = toMs_(data[i][ti]); if (ms > best) best = ms;
  }
  return best ? new Date(best).toISOString() : null;
}

// ── suppression ──────────────────────────────────────────────
function isSuppressed_(email) {
  email = String(email || '').trim().toLowerCase();
  if (!email) return false;
  const domain = email.split('@')[1] || '';
  const h = hmac_(email);
  const { data } = sheetRows_('suppression');
  const hi = colIndex_('suppression', 'emailHmac'), di = colIndex_('suppression', 'domain');
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][hi]) === h) return true;
    if (domain && String(data[i][di]) === domain) return true;
  }
  return false;
}
/** 수신거부·바운스·신고 시 호출 (references/06). 원문 아닌 HMAC 저장. */
function addSuppression_(email, reason) {
  appendRow_(SHEET.SUPPRESSION, {
    emailHmac: hmac_(String(email || '').trim().toLowerCase()),
    domain: '', reason: reason, createdAt: new Date().toISOString(),
  });
}

// ── followup_queue ───────────────────────────────────────────
function followupPendingCount_() {
  const { data } = sheetRows_('followup_queue');
  const si = colIndex_('followup_queue', 'status');
  let n = 0;
  for (let i = 0; i < data.length; i++) if (String(data[i][si]) === 'pending') n++;
  return n;
}
function followupDue_() {
  const now = Date.now();
  const { data } = sheetRows_('followup_queue');
  const si = colIndex_('followup_queue', 'status'), di = colIndex_('followup_queue', 'dueAt'), ci = colIndex_('followup_queue', 'contactId');
  const out = [];
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][si]) !== 'pending') continue;
    if (toMs_(data[i][di]) <= now) out.push({ contactId: String(data[i][ci]), dueAt: String(data[i][di]) });
  }
  return out;
}

// ── 로그·유틸 ────────────────────────────────────────────────
function logRun_(kind, msg) { Logger.log('[dispatchTick] ' + kind + ': ' + msg); } // 1줄 리포트(references/09)
function mdToHtml_(md) {
  // 최소 마크다운: 이스케이프 → **bold** → 링크 [t](u) → 개행 <br>
  let h = escapeHtml_(md);
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2">$1</a>');
  return h.replace(/\n/g, '<br>');
}
function escapeHtml_(s) { return String(s || '').replace(/[&<>"]/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]; }); }
function hmac_(s) { return Utilities.base64Encode(Utilities.computeHmacSha256Signature(String(s), _hmacSecret_())); }
function _hmacSecret_() {
  const p = PropertiesService.getScriptProperties();
  let k = p.getProperty('SF_HMAC_SECRET');
  if (!k) { k = Utilities.getUuid(); p.setProperty('SF_HMAC_SECRET', k); }
  return k;
}
