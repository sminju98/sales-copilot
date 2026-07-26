#!/usr/bin/env python3
"""주기적 안부 팔로업 — 로컬 CRM 연락처 대상 일괄 발송.

(원본: myeongham-manager plugins/scripts/follow_up.py — sales-copilot 내장판 followup_send.py.
 연락처는 crm/contacts.jsonl, 템플릿은 templates/messages/, 설정은 config followup 블록
 — 없으면 기본값(30일·최대 3회·email) — 과 me.name / company.name 매핑을 쓴다.
 발송은 send_sms/send_email 모듈을 거치므로 그쪽 수신거부·야간 보류 게이트를 그대로 통과하고,
 이 스크립트도 대상 선별 단계에서 수신거부(이메일·전화)를 한 번 더 걸러낸다)

마지막 연락일(last_contact_at, 없으면 created)로부터 followup.interval_days 가 지났고
누적 발송(followups_sent)이 followup.max_followups 미만인 연락처를 골라 안부를 보낸다.

사용:
  python3 followup_send.py --dry-run   # 대상·문안 미리보기 (항상 이걸 먼저 — 실제 발송 없음)
  python3 followup_send.py             # 실제 발송 (스킬의 approval_mode 게이트 통과 후에만 실행)

채널(followup.channel: email|sms|both)이 미설정 상태면 그 채널은 건너뛰고 안내만 한다
(dry-run 은 미설정이어도 문안 미리보기를 출력). 실발송 성공 건만 followups_sent·
last_contact_at 을 갱신한다(보류·수신거부·실패는 갱신 안 함).
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_config, now_iso  # noqa: E402
import crm  # noqa: E402

TPL_DIR = os.path.join(ROOT, "templates", "messages")


class _Safe(dict):
    def __missing__(self, key):
        return ""


def render(template_name, ctx):
    with open(os.path.join(TPL_DIR, template_name), encoding="utf-8") as f:
        return f.read().format_map(_Safe(ctx))


def _days_since(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso_str))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return (datetime.datetime.now().astimezone() - dt).days


def find_due(contacts, interval_days, max_followups):
    due = []
    for c in contacts:
        if int(c.get("followups_sent", 0) or 0) >= max_followups:
            continue
        ref = c.get("last_contact_at") or c.get("created") or c.get("created_at")
        days = _days_since(ref)
        if days is None or days >= interval_days:
            due.append(c)
    return due


def me_context(cfg):
    """템플릿의 me_* 자리표시자 — sales-copilot 설정 구조(me/company/cards/sms/email_send)에서 매핑."""
    me = cfg.get("me", {})
    return {
        "me_name": me.get("name", ""),
        "me_company": cfg.get("company", {}).get("name", "") or me.get("company", ""),
        "me_title": me.get("title", ""),
        "me_phone": me.get("phone", "") or cfg.get("sms", {}).get("sender", ""),
        "me_email": me.get("sender_email", "") or cfg.get("email_send", {}).get("username", ""),
        "me_sales_material_url": cfg.get("cards", {}).get("sales_material_url", ""),
    }


def main():
    ap = argparse.ArgumentParser(description="주기적 안부 팔로업 발송 (로컬 CRM 연락처)")
    ap.add_argument("--dry-run", action="store_true", help="대상·문안 미리보기 (실제 발송 없음)")
    args = ap.parse_args()

    cfg = load_config()
    fu = cfg.get("followup", {})
    interval = int(fu.get("interval_days", 30))
    max_fu = int(fu.get("max_followups", 3))
    channel = fu.get("channel", "email")
    me = me_context(cfg)

    # 채널 준비 상태 — 미설정 채널은 실발송에서 제외(배치 전체를 죽이지 않는다)
    em = cfg.get("email_send", {})
    sm = cfg.get("sms", {})
    email_ready = bool(em.get("smtp_host") and em.get("username") and em.get("app_password"))
    sms_ready = bool(sm.get("api_key") and sm.get("api_secret") and sm.get("sender"))
    want_email = channel in ("email", "both")
    want_sms = channel in ("sms", "both")
    if want_email and not email_ready:
        print("[안내] 이메일 채널 미설정(email_send.username/app_password) — "
              + ("문안 미리보기만 출력합니다." if args.dry_run else "이메일 실발송은 건너뜁니다."))
    if want_sms and not sms_ready:
        print("[안내] 문자 채널 미설정(sms.api_key/api_secret/sender) — "
              + ("문안 미리보기만 출력합니다." if args.dry_run else "문자 실발송은 건너뜁니다."))

    contacts = crm.read_all("contact")
    due = find_due(contacts, interval, max_fu)
    print(f"[팔로업 대상] {len(due)}명 (기준: {interval}일 경과, 최대 {max_fu}회, 채널: {channel})")

    if not due:
        return

    # 지연 임포트: 모듈 임포트는 안전(설정 검사는 send_* 호출 시점) — 수신거부 검사도 재사용
    import send_email
    import send_sms

    sent = 0
    for c in due:
        ctx = {**me, **c}
        n = int(c.get("followups_sent", 0) or 0) + 1
        print(f"\n- {c.get('name', '')} ({c.get('company') or c.get('account') or ''}) · {n}회차")

        ok = False
        if want_email and c.get("email"):
            if crm.suppress_check(c["email"]):
                print(f"  [제외] {c['email']} — 수신거부 등록 주소 (발송 금지)")
            else:
                subject = f"{c.get('name', '')}님, 잘 지내시죠? — {me['me_company']} {me['me_name']}"
                body = render("followup_email.txt", ctx)
                if email_ready:
                    res = send_email.send_email(c["email"], subject, body, dry_run=args.dry_run)
                    ok = ok or bool(res.get("ok"))
                elif args.dry_run:
                    print(f"[DRY-RUN 이메일·채널 미설정] to={c['email']}\n제목: {subject}\n---\n{body}\n---")

        if want_sms and c.get("phone"):
            if send_sms._phone_suppressed(c["phone"]):
                print(f"  [제외] {c['phone']} — 수신거부 등록 번호 (발송 금지)")
            else:
                text = render("followup_sms.txt", ctx)
                if sms_ready:
                    res = send_sms.send_sms(c["phone"], text, dry_run=args.dry_run)
                    ok = ok or bool(res.get("ok"))
                elif args.dry_run:
                    print(f"[DRY-RUN 문자·채널 미설정] to={c['phone']}\n---\n{text}\n---")

        if not (c.get("email") or c.get("phone")):
            print("  [건너뜀] 이메일·전화 모두 없음 — 연락처 보강 필요")

        # 실발송 성공 건만 카운터 갱신 (dry-run·보류·수신거부·실패는 그대로 둔다)
        if ok and not args.dry_run and c.get("id"):
            crm.update("contact", c["id"], {"followups_sent": n, "last_contact_at": now_iso()})
            sent += 1

    if not args.dry_run:
        print(f"\n[완료] 실발송·상태 갱신 {sent}건 / 대상 {len(due)}건")
    else:
        print("\n[DRY-RUN 종료] 실제 발송 없음 — 문안 확인 후 --dry-run 없이 다시 실행")


if __name__ == "__main__":
    main()
