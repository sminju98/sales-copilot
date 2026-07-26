# 06 — 팔로업 & 답장 처리

<!-- 원본: salesfactory-plugin skills/salesfactory/references/06-followup.md — sales-copilot 통합판.
     답장 분류는 [[classify-reply]] 스킬과 정합(같은 분류 체계). -->

## 팔로업 — 트랙별로 엔진이 다르다

- **대량 트랙(Apps Script 대장)**: cold 발송 성공 시 엔진이 자동으로 **7일 후 fu1**을 큐잉한다(`followup_queue`). 팔로업 원고는 05에서 이미 작성돼 `drafts`에 있다. **대장 트랙의 자동 팔로업은 1회(fu1)로 제한** — 발송·페이싱·수신거부 게이트를 cold와 동일하게 통과한다.
- **개별·소량 트랙(send_email.py·커넥터)**: outreach의 5터치 시퀀스(1·3·6·10·15일차, config `cadence`)를 따른다. 각 터치는 발송 게이트를 매번 다시 통과한다.
- 오늘 팔로업 대상 조회: `python3 "$CLAUDE_PLUGIN_ROOT/scripts/state.py" followup_due` (대장) + `queue_today.py` (개별).
- 답장이 오면 그 상대의 팔로업은 **즉시 취소**(큐에서 제거 + 로컬 next_action 갱신).

## 답장 감지 (Apps Script가 시트로 추적, Claude가 분류)

- 엔진은 받은편지함 읽기 scope를 쓰지 않는다 — 발신 스레드 상태를 시트로 추적한다. 답장 텍스트가 확보되면(사용자 붙여넣기·Gmail 커넥터) 의도 분류는 [[classify-reply]]가 한다.
- **분류는 LLM, 후속 행동은 결정론.**

## 답장 5분류 → 행동 ([[classify-reply]]와 동일 체계)

| 분류 | 신호 | 행동 |
|---|---|---|
| **positive** | 관심·질문·미팅 요청 | 팔로업 취소. 사용자에게 **즉시 알림 + 스레드 전문 전달** → 콜 제안([[book-call]]) |
| **neutral** | 나중에·정보 요청 | 팔로업 취소. 요청 정보 회신 초안 생성 → 사용자 확인 |
| **objection** | 가격·타이밍·경쟁사 이의 | 회신 초안 생성(`context/objections.md` 참조) → 사용자 확인 후 발송 |
| **unsubscribe** | 수신거부·"보내지 마" | **즉시·무조건 suppression 등록**(시트는 HMAC 해시, 로컬은 `crm.py suppress add`). 재발송 영구 차단 |
| **bounce** | 반송·주소 오류 | suppression(bounce). 바운스율 카운터 증가 → 임계 초과 시 자동 감속(07) |

## 수신거부는 즉시·무조건 (절대 불변 — 08)

- 수신거부 의사가 감지되면 **로그인·본인확인 등 절차 없이 즉시 반영.** 다시는 그 주소로 안 나간다. 시트 suppression과 로컬 `crm/suppressions.jsonl` **양쪽에** 반영한다.
- 광고성 트랙(옵트인·거래관계 예외)에서 수신동의/거부/철회가 오면 **14일 이내 처리결과 통지**(08) — 엔진이 자동 확인메일. 통지에 광고 넣지 마라.
- suppression 검사는 발송 직전 게이트에서 (email HMAC, domain) 두 키로. 억제 레코드는 사유와 함께 영구 보존, 삭제 API 없음.

## 답장 회신 초안 (허용 — 대필)

콜드메일 답장 응대는 **실존 발신자의 대필**이다. 자동 회신 초안 생성은 허용된다. 다만 오발송·톤 사고 방지로 **회신 발송은 항상 사용자 확인 후**(approval_mode와 무관하게 초기 N건은 건별 확인 권장 — 품질 판단).
