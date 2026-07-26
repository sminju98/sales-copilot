/**
 * (출처: myeongham-manager 플러그인 apps_script/Code.gs — sales-copilot 내장판.
 *  코드는 원본과 동일하고, 설정 키 안내만 sales-copilot 기준(cards.*)으로 바꿨다)
 *
 * 명함관리 - 구글시트 수신용 Apps Script 웹앱
 *
 * 설치 방법:
 *  1) 저장할 구글시트 열기 → 확장 프로그램 > Apps Script
 *  2) 이 코드를 붙여넣고, 아래 SECRET 값을 config 의 cards.sheet_webhook_secret 과 동일하게 변경
 *  3) 배포 > 새 배포 > 유형: 웹 앱
 *       - 실행: 나
 *       - 액세스 권한: 모든 사용자
 *  4) 생성된 웹 앱 URL 을 config 의 cards.sheet_webhook_url 에 저장:
 *     python3 scripts/set_config.py cards.sheet_webhook_url=<URL> cards.sheet_webhook_secret=<SECRET>
 */

var SECRET = "여기에-공유-비밀키-바꾸기"; // config > cards.sheet_webhook_secret 과 반드시 동일
var SHEET_NAME = "명함";
var HEADERS = ["등록일시", "이름", "회사", "직함", "휴대폰", "이메일", "주소", "웹사이트", "만난곳", "메모"];

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    if (body.secret !== SECRET) {
      return _json({ ok: false, error: "unauthorized" });
    }
    var c = body.contact || {};
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(HEADERS);
    }
    sheet.appendRow([
      new Date(),
      c.name || "",
      c.company || "",
      c.title || "",
      c.phone || "",
      c.email || "",
      c.address || "",
      c.website || "",
      c.met_at || "",
      c.memo || ""
    ]);
    return _json({ ok: true });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
