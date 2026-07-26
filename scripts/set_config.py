#!/usr/bin/env python3
"""config.json 값을 대화로 채우기 위한 헬퍼.

사용자가 JSON을 직접 편집하지 않도록, 클로드가 채팅에서 받은 값을 이 스크립트로 저장한다.
점(.)으로 중첩 경로를 지정한다. config.json이 없으면 config.example.json에서 복제한다.

예:
  python3 scripts/set_config.py me.name="홍길동" company.name="우리회사"
  python3 scripts/set_config.py delivery.private.slack_webhook="https://hooks.slack.com/..."
  python3 scripts/set_config.py me.approval_mode=per_item me.personal_contacts_policy=selected
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG_PATH, EXAMPLE_CONFIG, HOME

EXAMPLE = EXAMPLE_CONFIG


def parse_value(s):
    low = s.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return ""
    body = s[1:] if s.startswith("-") else s
    if body.isdigit():
        return int(s)
    return s


def set_path(cfg, path, value):
    keys = path.split(".")
    node = cfg
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


def _redact(path, value):
    if any(w in path for w in ("password", "secret", "token", "webhook", "api_key")):
        return "••••••(저장됨)"
    return repr(value)


def main():
    args = sys.argv[1:]
    if not args:
        print("사용: set_config.py path=value [path=value ...]")
        return

    # 인자 전부 검증 후에만 파일을 만들고 쓴다 — 잘못된 인자면 아무것도 저장하지 않는다.
    pairs = []
    for arg in args:
        if "=" not in arg:
            raise SystemExit(f"[에러] '{arg}' — path=value 형식이 필요합니다. 아무것도 저장하지 않았습니다.")
        path, _, raw = arg.partition("=")
        pairs.append((path.strip(), parse_value(raw)))

    if not os.path.exists(CONFIG_PATH):
        os.makedirs(HOME, exist_ok=True)
        shutil.copy(EXAMPLE, CONFIG_PATH)
        print(f"[config.json 생성됨] {CONFIG_PATH}")

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    for path, value in pairs:
        set_path(cfg, path, value)
        print(f"  ✓ {path} = {_redact(path, value)}")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("[저장 완료]")


if __name__ == "__main__":
    main()
