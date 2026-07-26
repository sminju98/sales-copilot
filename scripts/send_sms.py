#!/usr/bin/env python3
"""Solapi(솔라피) 문자 발송 — 발송 게이트 내장판.

(원본: myeongham-manager plugins/scripts/send_sms.py — sales-copilot 내장판.
 설정은 config.json 의 sms 블록: provider(solapi), api_key, api_secret, sender)

사용:
  python3 send_sms.py --to 01012345678 --text "안녕하세요..." [--dry-run]

안전장치:
  - 야간 보류(quiet hours): config policy.quiet_hours("21:00-08:00") 기준으로 야간 실발송을 보류.
    문자는 기본 켜짐(야간 전송 제한 취지) — 끄려면 options.respect_quiet_hours 를 false 로 명시.
  - 수신거부 백스톱: crm/suppressions.jsonl 에 같은 번호(phone 필드)가 있으면 발송 거부(종료코드 1).
    이메일 기반 수신거부 검사는 발송 전에 crm.py suppress-check 로 별도 수행할 것.
  - 수신거부 문구: options.append_opt_out + options.opt_out_text 를 켜면 본문 끝에 덧붙임(기본 꺼짐).
"""
import argparse
import datetime
import hashlib
import hmac
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CRM_DIR, http_request, load_config  # noqa: E402
import crm  # noqa: E402

SOLAPI_URL = "https://api.solapi.com/messages/v4/send"


def _norm_phone(p):
    return re.sub(r"\D", "", str(p or ""))


def _auth_header(api_key, api_secret):
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    salt = secrets.token_hex(16)
    signature = hmac.new(api_secret.encode(), (date + salt).encode(), hashlib.sha256).hexdigest()
    return f"HMAC-SHA256 apiKey={api_key}, date={date}, salt={salt}, signature={signature}"


def _quiet_range(cfg):
    """policy.quiet_hours '21:00-08:00' → (21, 8). 없거나 파싱 실패면 options → 기본 (21, 8)."""
    raw = str(cfg.get("policy", {}).get("quiet_hours", "") or "")
    m = re.match(r"^\s*(\d{1,2})(?::\d{2})?\s*-\s*(\d{1,2})(?::\d{2})?\s*$", raw)
    if m:
        return int(m.group(1)) % 24, int(m.group(2)) % 24
    opts = cfg.get("options", {})
    return int(opts.get("quiet_start_hour", 21)), int(opts.get("quiet_end_hour", 8))


def _in_quiet_hours(cfg):
    start, end = _quiet_range(cfg)
    h = datetime.datetime.now().astimezone().hour
    if start == end:
        return False
    if start < end:
        return start <= h < end
    return (h >= start) or (h < end)


def _phone_suppressed(to):
    """수신거부 원장(suppressions.jsonl)에 같은 번호가 있으면 True."""
    digits = _norm_phone(to)
    if not digits:
        return False
    for r in crm.read_all(path=os.path.join(CRM_DIR, "suppressions.jsonl")):
        rp = _norm_phone(r.get("phone"))
        if rp and (rp == digits or (len(digits) >= 8 and digits[-8:] == rp[-8:])):
            return True
    return False


def apply_options(text, opts):
    if opts.get("append_opt_out") and opts.get("opt_out_text"):
        if opts["opt_out_text"] not in text:
            text = f"{text}\n{opts['opt_out_text']}"
    return text


def send_sms(to, text, dry_run=False):
    cfg = load_config()
    sms = cfg.get("sms", {})
    if not (sms.get("api_key") and sms.get("api_secret") and sms.get("sender")):
        raise SystemExit(
            "[미설정] 문자 발송 설정이 없습니다 — config sms.api_key / sms.api_secret / sms.sender (솔라피).\n"
            "  예: python3 scripts/set_config.py sms.api_key=... sms.api_secret=... sms.sender=01000000000"
        )
    opts = cfg.get("options", {})
    text = apply_options(text, opts)

    if _phone_suppressed(to):
        print(f"[발송 거부] {to} — 수신거부 등록 번호입니다. 발송 금지, 다른 경로([[send-policy]])로.")
        return {"ok": False, "skipped": "suppressed"}

    in_quiet = _in_quiet_hours(cfg)
    if dry_run:
        note = " (지금은 야간 — 실발송은 보류됨)" if in_quiet else ""
        print(f"[DRY-RUN 문자] to={to}{note}\n---\n{text}\n---")
        return {"ok": True, "dry_run": True}

    if opts.get("respect_quiet_hours", True) and in_quiet:
        print("[보류] 야간(quiet hours) — 지금은 보내지 않습니다. 낮 시간에 다시 실행하세요."
              " (끄려면 options.respect_quiet_hours=false)")
        return {"ok": False, "skipped": "quiet_hours"}

    payload = {"message": {"to": _norm_phone(to), "from": _norm_phone(sms["sender"]), "text": text}}
    headers = {"Authorization": _auth_header(sms["api_key"], sms["api_secret"])}
    res = http_request(SOLAPI_URL, payload, headers)
    if res.get("error"):
        print(f"[문자 발송 실패] to={to} -> {res['error']} {res.get('body') or ''}")
        return {"ok": False, "error": res["error"]}
    print(f"[문자 발송] to={to} -> {res.get('body')}")
    return {"ok": True, "body": res.get("body")}


def main():
    ap = argparse.ArgumentParser(description="Solapi 문자 발송 (수신거부·야간 보류 게이트 내장)")
    ap.add_argument("--to", required=True, help="수신 번호 (01012345678)")
    ap.add_argument("--text", required=True, help="본문")
    ap.add_argument("--dry-run", action="store_true", help="실제 발송 없이 미리보기")
    args = ap.parse_args()
    res = send_sms(args.to, args.text, dry_run=args.dry_run)
    if res.get("skipped") == "suppressed" or res.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
