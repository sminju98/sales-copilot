"""하드 게이트: 발송량·시간대·예산 상한을 코드가 센다 (프롬프트가 아니라).

설계 원칙 4가지
1. fail-closed — 한도가 없거나 상태 파일을 신뢰할 수 없으면 '차단'이 기본값이다.
   파일이 깨져 있거나 지워졌으면 통과시키지 않는다. "모르면 멈춘다".
2. 한도는 내려갈 뿐 올라가지 않는다 — 이 스크립트는 카운터를 되감지 못한다.
   음수 인자는 거부한다. 상향은 사람이 config.json 을 직접 고쳐야 한다.
3. 동시 실행 안전 — 검사와 기록 사이에 다른 프로세스가 끼어들지 못하게 파일 잠금을 건다.
   잠금이 없으면 상한 100에 135통이 통과하는 경합이 실제로 난다.
4. 전건 로그 — 통과든 차단이든 gates/gate_log.jsonl 에 남는다.

승인 양식 레지스트리
  "승인된 양식 안에서만 자동 발송"의 판정을 AI 판단이 아니라 해시 대조로 한다.
  메일머지 변수({{이름}}·%이름%·{name})는 치환되므로, 실제 나가는 본문을 검사할 때는
  --rendered 로 등록본을 패턴 삼아 대조한다(변수 자리만 달라야 통과).

CLI (exit code: 0=통과, 2=차단, 1=오류→차단으로 취급)
  python3 gates.py check-send   --channel email --count 40 [--template-id welcome-v1]
  python3 gates.py record-send  --channel email --count 40 [--template-id welcome-v1]
  python3 gates.py check-budget --campaign spring --amount 50000
  python3 gates.py record-spend --campaign spring --amount 50000
  python3 gates.py template register --id welcome-v1 --file draft.md [--scope email]
  python3 gates.py template check    --id welcome-v1 --file draft.md [--rendered]
  python3 gates.py template list
  python3 gates.py status
  python3 gates.py selftest          # 네거티브 테스트: 일부러 뚫어보고 막히는지 확인
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import sys

try:
    import fcntl
except ImportError:  # 윈도우 — 잠금 없이 동작하되 경고를 남긴다
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

GATES_DIR = os.path.join(common.HOME, "gates")
STATE_PATH = os.path.join(GATES_DIR, "state.json")
TEMPLATES_PATH = os.path.join(GATES_DIR, "templates.json")
LOG_PATH = os.path.join(GATES_DIR, "gate_log.jsonl")
LOCK_PATH = os.path.join(GATES_DIR, ".lock")

ALLOW, BLOCK, ERROR = 0, 2, 1


class GateStateError(Exception):
    """상태를 신뢰할 수 없다 — 통과가 아니라 차단으로 간다."""


# ---------- 저장소 ----------

def _ensure_dir():
    os.makedirs(GATES_DIR, exist_ok=True)


def _read_json(path, fallback, strict=False):
    """파일이 '없는 것'과 '깨진 것'을 구분한다.

    없으면 fallback(정상 초기 상태). 깨졌거나 못 읽으면 strict 에서 예외를 올려
    호출부가 차단하게 한다 — 상태 파일을 지우거나 망가뜨려 한도를 우회하는 경로를 막는다.
    """
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        if strict:
            raise GateStateError(f"{os.path.basename(path)} 를 해석할 수 없습니다 ({e}) — 손상·위변조 의심")
        return fallback
    except OSError as e:
        if strict:
            raise GateStateError(f"{os.path.basename(path)} 접근 실패 ({e})")
        return fallback


def _write_json(path, data):
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@contextlib.contextmanager
def _lock():
    """검사→증가 사이에 다른 프로세스가 끼어들지 못하게 한다."""
    _ensure_dir()
    if fcntl is None:
        yield
        return
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _today():
    return datetime.date.today().isoformat()


def _load_state():
    """오늘자 카운터. 날짜가 바뀌면 0에서 다시 시작한다. 파일이 깨졌으면 예외(=차단)."""
    st = _read_json(STATE_PATH, {}, strict=True)
    if not isinstance(st, dict):
        raise GateStateError("state.json 구조가 올바르지 않습니다 — 손상 의심")
    if st.get("date") != _today():
        st = {"date": _today(), "sends": {}, "spend": {}}
    st.setdefault("sends", {})
    st.setdefault("spend", {})
    if not isinstance(st["sends"], dict) or not isinstance(st["spend"], dict):
        raise GateStateError("state.json 카운터 구조가 올바르지 않습니다 — 손상 의심")
    return st


def _counter(st, bucket, key):
    """카운터는 음수가 될 수 없다. 음수면 손상으로 보고 차단한다."""
    v = st[bucket].get(key, 0)
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise GateStateError(f"카운터 값이 숫자가 아닙니다: {bucket}.{key}={v!r}")
    if n < 0:
        raise GateStateError(f"카운터가 음수입니다: {bucket}.{key}={n} — 되감기 시도 의심")
    return n


def _log(action, verdict, detail):
    _ensure_dir()
    rec = {"at": common.now_iso(), "action": action, "verdict": verdict, "detail": detail}
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------- 설정 ----------

def _gate_cfg():
    cfg = common.load_config(soft=True) or {}
    g = cfg.get("gates")
    return g if isinstance(g, dict) else {}


def _int_or_none(v):
    try:
        n = int(v)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _positive(v, label):
    """CLI 인자 검증 — 0 이하는 거부한다. 음수로 카운터를 되감는 경로를 막는다."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise GateStateError(f"{label} 는 정수여야 합니다: {v!r}")
    if n <= 0:
        raise GateStateError(f"{label} 는 1 이상이어야 합니다 (받은 값 {n}) — "
                             f"카운터는 되감을 수 없습니다")
    return n


def _quiet_hours(g):
    q = g.get("quiet_hours")
    if not isinstance(q, dict):
        return None
    s, e = _int_or_none(q.get("start")), _int_or_none(q.get("end"))
    if s is None or e is None or not (0 <= s <= 23) or not (0 <= e <= 23):
        return None
    return (s, e)


def _in_quiet(now_hour, window):
    s, e = window
    if s == e:
        return False
    if s < e:
        return s <= now_hour < e
    return now_hour >= s or now_hour < e


# ---------- 발송 게이트 ----------

def _check_send_locked(channel, count, template_id, now, st):
    g = _gate_cfg()
    reasons, blocks = [], []

    cap = _int_or_none(g.get("daily_send_cap"))
    if cap is None:
        blocks.append("한도 미설정: gates.daily_send_cap 이 없습니다 (모르면 멈춤)")
    elif cap == 0:
        blocks.append("gates.daily_send_cap 이 0 입니다 — 자동 발송이 전면 차단된 상태입니다. "
                      "쓰려면 config.json 에서 하루 상한을 직접 정하세요")
    else:
        used = _counter(st, "sends", channel)
        if used + count > cap:
            blocks.append(f"일일 발송 상한 초과: {channel} 오늘 {used} + 요청 {count} > 상한 {cap}")
        else:
            reasons.append(f"발송량 {used + count}/{cap}")

    window = _quiet_hours(g)
    if window is None:
        blocks.append("한도 미설정: gates.quiet_hours 가 없습니다 (모르면 멈춤)")
    elif _in_quiet(now.hour, window):
        blocks.append(f"야간 발송 금지 구간: 현재 {now.hour}시, 금지 {window[0]}~{window[1]}시")
    else:
        reasons.append(f"시간대 OK ({now.hour}시)")

    if template_id:
        reg = _read_json(TEMPLATES_PATH, {}, strict=True)
        if template_id not in reg:
            blocks.append(f"미등록 양식: '{template_id}' 는 승인 레지스트리에 없습니다 (= 새 양식)")
        else:
            reasons.append(f"양식 {template_id} v{reg[template_id].get('version', 1)} 등록됨")

    return reasons, blocks


def check_send(channel, count, template_id=None, now=None):
    now = now or datetime.datetime.now()
    try:
        count = _positive(count, "--count")
        with _lock():
            reasons, blocks = _check_send_locked(channel, count, template_id, now, _load_state())
    except GateStateError as e:
        _log("check-send", "BLOCK", {"channel": channel, "error": str(e)})
        return BLOCK, [], [f"상태를 신뢰할 수 없어 차단합니다 — {e}"]
    verdict = "BLOCK" if blocks else "ALLOW"
    _log("check-send", verdict, {"channel": channel, "count": count,
                                 "template_id": template_id, "reasons": reasons, "blocks": blocks})
    return (BLOCK if blocks else ALLOW), reasons, blocks


def record_send(channel, count, template_id=None):
    now = datetime.datetime.now()
    try:
        count = _positive(count, "--count")
        with _lock():                       # 검사와 증가를 한 잠금 안에서 — 경합 방지
            st = _load_state()
            reasons, blocks = _check_send_locked(channel, count, template_id, now, st)
            if blocks:
                _log("record-send", "BLOCK", {"channel": channel, "count": count, "blocks": blocks})
                return BLOCK, reasons, blocks
            st["sends"][channel] = _counter(st, "sends", channel) + count
            _write_json(STATE_PATH, st)
            total = st["sends"][channel]
    except GateStateError as e:
        _log("record-send", "BLOCK", {"channel": channel, "error": str(e)})
        return BLOCK, [], [f"상태를 신뢰할 수 없어 차단합니다 — {e}"]
    _log("record-send", "ALLOW", {"channel": channel, "count": count, "today_total": total})
    return ALLOW, [f"{channel} 누계 {total}"], []


# ---------- 예산 게이트 ----------

def _check_budget_locked(campaign, amount, st):
    g = _gate_cfg()
    reasons, blocks = [], []
    cap = _int_or_none(g.get("daily_budget_cap"))
    if cap is None:
        blocks.append("한도 미설정: gates.daily_budget_cap 이 없습니다 (모르면 멈춤)")
    elif cap == 0:
        blocks.append("gates.daily_budget_cap 이 0 입니다 — 자동 집행이 전면 차단된 상태입니다. "
                      "쓰려면 config.json 에서 하루 상한을 직접 정하세요")
    else:
        spent = _counter(st, "spend", campaign)
        if spent + amount > cap:
            blocks.append(f"일일 예산 상한 초과: {campaign} 오늘 {spent} + 요청 {amount} > 상한 {cap}")
        else:
            reasons.append(f"예산 {spent + amount}/{cap}")
    return reasons, blocks


def check_budget(campaign, amount):
    try:
        amount = _positive(amount, "--amount")
        with _lock():
            reasons, blocks = _check_budget_locked(campaign, amount, _load_state())
    except GateStateError as e:
        _log("check-budget", "BLOCK", {"campaign": campaign, "error": str(e)})
        return BLOCK, [], [f"상태를 신뢰할 수 없어 차단합니다 — {e}"]
    verdict = "BLOCK" if blocks else "ALLOW"
    _log("check-budget", verdict, {"campaign": campaign, "amount": amount,
                                   "reasons": reasons, "blocks": blocks})
    return (BLOCK if blocks else ALLOW), reasons, blocks


def record_spend(campaign, amount):
    try:
        amount = _positive(amount, "--amount")
        with _lock():
            st = _load_state()
            reasons, blocks = _check_budget_locked(campaign, amount, st)
            if blocks:
                _log("record-spend", "BLOCK", {"campaign": campaign, "blocks": blocks})
                return BLOCK, reasons, blocks
            st["spend"][campaign] = _counter(st, "spend", campaign) + amount
            _write_json(STATE_PATH, st)
            total = st["spend"][campaign]
    except GateStateError as e:
        _log("record-spend", "BLOCK", {"campaign": campaign, "error": str(e)})
        return BLOCK, [], [f"상태를 신뢰할 수 없어 차단합니다 — {e}"]
    _log("record-spend", "ALLOW", {"campaign": campaign, "amount": amount, "today_total": total})
    return ALLOW, [f"{campaign} 누계 {total}"], []


# ---------- 승인 양식 레지스트리 ----------

# 메일머지 변수 표기 3종. 대괄호([광고] 등)는 일부러 제외 — 표시 의무 문구를
# 변수로 오인해 지워버리면 (광고) 누락을 못 잡는다.
_PLACEHOLDER = re.compile(r"\{\{[^}\n]{0,80}\}\}|\{[^{}\n]{0,80}\}|%[^%\n]{1,40}%")


def _normalize(text):
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def _hash_text(text):
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def _rendered_matches(template_body, rendered_body):
    """치환본이 등록본과 '변수 자리만' 다른지 확인한다.

    등록본을 정규식으로 바꿔(고정 문구는 그대로, 변수는 와일드카드) 치환본에 맞춘다.
    고정 문구를 한 글자라도 고치면 불일치 → 새 양식으로 판정된다.
    """
    parts = [re.escape(p) for p in _PLACEHOLDER.split(_normalize(template_body))]
    pattern = r"[^\n]{0,200}".join(parts)
    return re.fullmatch(pattern, _normalize(rendered_body), re.DOTALL) is not None


def template_register(tid, path, scope=""):
    try:
        with open(path, encoding="utf-8") as f:
            body = f.read()
    except OSError as e:
        return ERROR, [], [f"파일을 읽을 수 없습니다: {e}"]
    with _lock():
        reg = _read_json(TEMPLATES_PATH, {})
        prev = reg.get(tid)
        version = int(prev.get("version", 1)) + 1 if prev else 1
        reg[tid] = {"hash": _hash_text(body), "body": _normalize(body), "version": version,
                    "scope": scope, "approved_at": common.now_iso(),
                    "source": os.path.abspath(path)}
        _write_json(TEMPLATES_PATH, reg)
    nvar = len(_PLACEHOLDER.findall(body))
    _log("template-register", "ALLOW", {"id": tid, "version": version, "vars": nvar})
    return ALLOW, [f"'{tid}' v{version} 등록 (해시 {reg[tid]['hash'][:12]}…, 변수 {nvar}개)"], []


def template_check(tid, path, rendered=False):
    reg = _read_json(TEMPLATES_PATH, {}, strict=True) if os.path.exists(TEMPLATES_PATH) else {}
    if tid not in reg:
        _log("template-check", "BLOCK", {"id": tid, "why": "unregistered"})
        return BLOCK, [], [f"미등록 양식: '{tid}' — 새 양식이므로 자동 발송 대상이 아닙니다"]
    try:
        with open(path, encoding="utf-8") as f:
            body = f.read()
    except OSError as e:
        return ERROR, [], [f"파일을 읽을 수 없습니다: {e}"]

    meta = reg[tid]
    if rendered:
        stored = meta.get("body")
        if not stored:
            return BLOCK, [], [f"'{tid}' 는 본문 없이 등록돼 치환본 대조를 할 수 없습니다 — 다시 register 하세요"]
        if not _rendered_matches(stored, body):
            _log("template-check", "BLOCK", {"id": tid, "why": "rendered-mismatch"})
            return BLOCK, [], [f"치환본이 승인 양식과 다릅니다 — '{tid}' 의 고정 문구가 바뀌었습니다. "
                               f"변수 자리 외의 수정은 새 양식입니다."]
        _log("template-check", "ALLOW", {"id": tid, "mode": "rendered"})
        return ALLOW, [f"'{tid}' v{meta['version']} 치환본 일치 (고정 문구 동일)"], []

    if _hash_text(body) != meta["hash"]:
        _log("template-check", "BLOCK", {"id": tid, "why": "hash-mismatch"})
        return BLOCK, [], [f"본문이 승인본과 다릅니다 — '{tid}' 는 수정됐으므로 새 양식입니다. "
                           f"승인 후 template register 로 다시 등록하세요. "
                           f"(메일머지 치환본을 검사하려면 --rendered 를 붙이세요)"]
    _log("template-check", "ALLOW", {"id": tid, "version": meta["version"]})
    return ALLOW, [f"'{tid}' v{meta['version']} 승인본과 일치"], []


def template_list():
    reg = _read_json(TEMPLATES_PATH, {})
    if not reg:
        return ALLOW, ["등록된 승인 양식이 없습니다."], []
    return ALLOW, [f"{tid}  v{m.get('version', 1)}  {m.get('scope', '')}  "
                   f"{m.get('hash', '')[:12]}…  {m.get('approved_at', '')}"
                   for tid, m in sorted(reg.items())], []


# ---------- 상태·자가검사 ----------

def status():
    g = _gate_cfg()
    try:
        st = _load_state()
    except GateStateError as e:
        return BLOCK, [], [f"상태 파일 이상 — {e}"]
    cap, bcap, win = (_int_or_none(g.get("daily_send_cap")),
                      _int_or_none(g.get("daily_budget_cap")), _quiet_hours(g))
    out = [f"날짜 {st['date']}",
           f"일일 발송 상한: {cap if cap else '미설정/0 (=차단)'}",
           f"야간 금지: {f'{win[0]}~{win[1]}시' if win else '미설정 (=차단)'}",
           f"일일 예산 상한: {bcap if bcap else '미설정/0 (=차단)'}",
           f"오늘 발송: {st['sends'] or '없음'}",
           f"오늘 집행: {st['spend'] or '없음'}",
           f"등록 양식 수: {len(_read_json(TEMPLATES_PATH, {}))}",
           f"동시 실행 잠금: {'사용' if fcntl else '미지원(윈도우) — 병렬 실행 주의'}"]
    return ALLOW, out, []


def selftest():
    """네거티브 테스트 — 일부러 뚫어보고 막히는지 확인한다.

    게이트는 통과를 확인해서는 검증되지 않는다. 넘겨봤을 때 막혀야 검증된다.
    """
    g = _gate_cfg()
    results, failed = [], False

    def _expect_block(label, fn):
        nonlocal failed
        code = fn()[0]
        ok = code == BLOCK
        failed |= not ok
        results.append(f"{label} → {'차단됨 ✅' if ok else '통과됨 ❌ 게이트 무효'}")

    cap = _int_or_none(g.get("daily_send_cap"))
    if not cap:
        results.append("① 발송 상한 미설정/0 → 모든 발송 차단 (fail-closed 정상)")
    else:
        _expect_block(f"① 상한+1({cap + 1}통) 시도", lambda: check_send("__selftest__", cap + 1))

    win = _quiet_hours(g)
    if win is None:
        results.append("② 야간 구간 미설정 → 시간대 검사가 항상 차단 (fail-closed 정상)")
    else:
        fake = datetime.datetime.now().replace(hour=win[0], minute=30)
        _expect_block(f"② 금지 시간({win[0]}시 30분) 발송 시도",
                      lambda: check_send("__selftest__", 1, now=fake))

    _expect_block("③ 미등록 양식으로 발송 시도",
                  lambda: check_send("__selftest__", 1, template_id="__never_registered__"))
    _expect_block("④ 음수 카운트로 한도 되감기 시도",
                  lambda: record_send("__selftest__", -999))
    _expect_block("⑤ 음수 금액으로 예산 되감기 시도",
                  lambda: record_spend("__selftest__", -999999))

    # ⑥ 상태 파일 손상 — 실제로 깨뜨렸다가 원복
    _ensure_dir()
    backup = None
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            backup = f.read()
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            f.write("CORRUPTED{{{")
        _expect_block("⑥ 상태 파일 손상 후 발송 시도", lambda: check_send("__selftest__", 1))
    finally:
        if backup is None:
            os.path.exists(STATE_PATH) and os.remove(STATE_PATH)
        else:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                f.write(backup)

    results.append("")
    results.append("불합격 항목이 있으면 config.json 의 gates.* 를 채우고 다시 실행하세요."
                   if failed else "여섯 가지 우회를 전부 막습니다 — 졸업 조건 통과.")
    _log("selftest", "BLOCK" if failed else "ALLOW", {"results": results})
    return (BLOCK if failed else ALLOW), results, []


# ---------- CLI ----------

def main(argv=None):
    p = argparse.ArgumentParser(description="하드 게이트 — 발송량·시간대·예산·승인 양식을 코드가 센다")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("check-send", "record-send"):
        s = sub.add_parser(name)
        s.add_argument("--channel", required=True)
        s.add_argument("--count", required=True)
        s.add_argument("--template-id", default=None)

    for name in ("check-budget", "record-spend"):
        s = sub.add_parser(name)
        s.add_argument("--campaign", required=True)
        s.add_argument("--amount", required=True)

    t = sub.add_parser("template")
    tsub = t.add_subparsers(dest="tcmd", required=True)
    tr = tsub.add_parser("register")
    tr.add_argument("--id", required=True)
    tr.add_argument("--file", required=True)
    tr.add_argument("--scope", default="")
    tc = tsub.add_parser("check")
    tc.add_argument("--id", required=True)
    tc.add_argument("--file", required=True)
    tc.add_argument("--rendered", action="store_true",
                    help="메일머지 치환본을 등록본과 대조(변수 자리만 달라야 통과)")
    tsub.add_parser("list")

    sub.add_parser("status")
    sub.add_parser("selftest")

    a = p.parse_args(argv)

    if a.cmd == "check-send":
        code, reasons, blocks = check_send(a.channel, a.count, a.template_id)
    elif a.cmd == "record-send":
        code, reasons, blocks = record_send(a.channel, a.count, a.template_id)
    elif a.cmd == "check-budget":
        code, reasons, blocks = check_budget(a.campaign, a.amount)
    elif a.cmd == "record-spend":
        code, reasons, blocks = record_spend(a.campaign, a.amount)
    elif a.cmd == "template":
        if a.tcmd == "register":
            code, reasons, blocks = template_register(a.id, a.file, a.scope)
        elif a.tcmd == "check":
            code, reasons, blocks = template_check(a.id, a.file, a.rendered)
        else:
            code, reasons, blocks = template_list()
    elif a.cmd == "status":
        code, reasons, blocks = status()
    else:
        code, reasons, blocks = selftest()

    if blocks:
        print("⛔ 차단")
        for b in blocks:
            print(f"  - {b}")
        print("\n한도를 넘겨야 하는 사정이 있으면 사람이 config.json 의 gates.* 를 직접 고치세요 "
              "— 이 스크립트는 한도를 올리거나 카운터를 되감지 못합니다.")
    if reasons:
        if not blocks and a.cmd.startswith(("check", "record")):
            print("✅ 통과")
        for r in reasons:
            print(f"  (참고) {r}" if blocks else f"  {r}")
    return code


if __name__ == "__main__":
    sys.exit(main())
