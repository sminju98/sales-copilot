#!/usr/bin/env python3
"""SMTP 이메일 발송 (Gmail 앱 비밀번호 등) — 발송 게이트 내장판.

(원본: myeongham-manager plugins/scripts/send_email.py — sales-copilot 내장판.
 설정은 config.json 의 email_send 블록: smtp_host, smtp_port, username, app_password, from_name)

사용:
  python3 send_email.py --to a@b.com --subject "제목" --body "본문" [--dry-run]
  python3 send_email.py --to a@b.com --subject "제목" --body-file draft.txt

Gmail을 쓰려면 2단계 인증 후 '앱 비밀번호'를 발급해 email_send.app_password 에 넣는다.
안전장치:
  - 수신거부 게이트: crm.py suppress 에 등록된 주소면 발송 거부(종료코드 1).
  - 수신거부 문구: options.append_opt_out + options.opt_out_text 켜면 본문 끝에 덧붙임(기본 꺼짐).
  - 야간 보류: options.respect_quiet_hours 를 켰을 때만 적용(이메일은 야간 전송 제한의 예외라 기본 꺼짐).
"""
import argparse
import datetime
import os
import smtplib
import ssl
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config  # noqa: E402
import crm  # noqa: E402


def _in_quiet_hours(opts):
    start = int(opts.get("quiet_start_hour", 21))
    end = int(opts.get("quiet_end_hour", 8))
    h = datetime.datetime.now().astimezone().hour
    return (h >= start) or (h < end)


def send_email(to, subject, body, dry_run=False):
    cfg = load_config()
    em = cfg.get("email_send", {})
    if not (em.get("smtp_host") and em.get("username") and em.get("app_password")):
        raise SystemExit(
            "[미설정] 이메일 발송 설정이 없습니다 — config email_send.username / app_password (SMTP).\n"
            "  예: python3 scripts/set_config.py email_send.username=me@gmail.com email_send.app_password=..."
        )
    opts = cfg.get("options", {})

    if crm.suppress_check(to):
        print(f"[발송 거부] {to} — 수신거부 등록 주소입니다. 발송 금지, 다른 경로([[send-policy]])로.")
        return {"ok": False, "skipped": "suppressed"}

    if opts.get("append_opt_out") and opts.get("opt_out_text"):
        body = f"{body}\n\n---\n{opts['opt_out_text']}"

    if dry_run:
        print(f"[DRY-RUN 이메일] to={to}\n제목: {subject}\n---\n{body}\n---")
        return {"ok": True, "dry_run": True}

    if opts.get("respect_quiet_hours") and _in_quiet_hours(opts):
        print("[보류] 야간 발송 자제 옵션이 켜져 있어 지금은 보내지 않습니다.")
        return {"ok": False, "skipped": "quiet_hours"}

    from_name = em.get("from_name") or "{} ({})".format(
        cfg.get("me", {}).get("name", ""), cfg.get("company", {}).get("name", "")
    ).strip(" ()")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), em["username"]))
    msg["To"] = to

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP(em["smtp_host"], int(em.get("smtp_port", 587))) as s:
            s.starttls(context=ctx)
            s.login(em["username"], em["app_password"])
            s.sendmail(em["username"], [to], msg.as_string())
    except (smtplib.SMTPException, OSError) as e:
        print(f"[이메일 발송 실패] {to} -> {e}")
        return {"ok": False, "error": str(e)}
    print(f"[이메일 발송] {to}")
    return {"ok": True}


def main():
    ap = argparse.ArgumentParser(description="SMTP 이메일 발송 (수신거부 게이트 내장)")
    ap.add_argument("--to", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", help="본문 문자열")
    ap.add_argument("--body-file", help="본문을 파일에서 읽기")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    body = args.body
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            body = f.read()
    if body is None:
        ap.error("--body 또는 --body-file 중 하나는 필요합니다.")
    res = send_email(args.to, args.subject, body, dry_run=args.dry_run)
    if res.get("skipped") == "suppressed" or res.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
