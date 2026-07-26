#!/usr/bin/env python3
"""로컬 최소 CRM — CRM 커넥터가 없어도 영업 루프가 돌게 하는 JSONL 저장소.

엔티티: account(회사) contact(사람) relationship(관계) lead(리드)
        activity(활동) opportunity(영업기회) + suppressions(수신거부).
저장: $SALES_COPILOT_HOME/crm/<복수형>.jsonl — 한 줄 = 한 레코드(JSON).

사용:
  python3 scripts/crm.py add lead --json '{"company":"A사","name":"김OO","status":"new"}'
  python3 scripts/crm.py update lead ld-0001 --json '{"next_action":"3일차 후속"}'
  python3 scripts/crm.py list lead --where status=new --limit 10
  python3 scripts/crm.py get lead ld-0001
  python3 scripts/crm.py find contact 김철수
  python3 scripts/crm.py dedupe contact --json '{"email":"a@b.com"}'
  python3 scripts/crm.py next-missing          # 다음 행동 없는 활성 리드·기회 (핵심 KPI)
  python3 scripts/crm.py suppress add --email a@b.com --reason "수신거부 회신"
  python3 scripts/crm.py suppress-check --email a@b.com   # 등록돼 있으면 종료코드 1
  python3 scripts/crm.py stats

모듈로도 쓴다: next_missing() / stats() / suppress_check(email) — 훅(proactive 등)이 import.
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CRM_DIR, load_config, now_iso  # noqa: E402

# 엔티티 → (id 접두사, 저장 파일)
ENTITIES = {
    "account": ("ac", "accounts.jsonl"),
    "contact": ("ct", "contacts.jsonl"),
    "relationship": ("rl", "relationships.jsonl"),
    "lead": ("ld", "leads.jsonl"),
    "activity": ("av", "activities.jsonl"),
    "opportunity": ("op", "opportunities.jsonl"),
}
SUPPRESSIONS = "suppressions.jsonl"
# 활성 판정: 여기 들어간 상태/단계면 '끝난' 레코드로 보고 다음 행동을 요구하지 않는다.
INACTIVE_LEAD_STATUS = {"won", "lost", "dormant", "disqualified", "converted"}
INACTIVE_OPP_STAGE = {"won", "lost"}
# find 가 뒤지는 필드(이름·회사·이메일 계열)
FIND_KEYS = ("name", "name_ko", "name_en", "company", "company_name", "account",
             "email", "contact", "person", "title", "domain")


# ---------- 저장소 ----------
def _path(entity):
    return os.path.join(CRM_DIR, ENTITIES[entity][1])


def read_all(entity=None, path=None):
    """JSONL 전체를 list[dict]로. 깨진 줄은 조용히 건너뛴다(수기 편집 보호)."""
    p = path or _path(entity)
    rows = []
    if not os.path.exists(p):
        return rows
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append(path, rec):
    os.makedirs(CRM_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _rewrite(path, rows):
    os.makedirs(CRM_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _next_id(entity, rows):
    prefix = ENTITIES[entity][0]
    top = 0
    pat = re.compile(rf"^{prefix}-(\d+)$")
    for r in rows:
        m = pat.match(str(r.get("id", "")))
        if m:
            top = max(top, int(m.group(1)))
    return f"{prefix}-{top + 1:04d}"


# ---------- 정규화 ----------
def _norm(s):
    return str(s or "").strip().lower()


def _norm_stage(s):
    return _norm(s).replace("closed_", "").replace("closed-", "").replace("closed ", "")


def _norm_phone(s):
    return re.sub(r"\D", "", str(s or ""))


def _norm_company(s):
    t = _norm(s)
    for junk in ("주식회사", "(주)", "㈜", "(유)", "inc.", "inc", "co., ltd", "co.,ltd",
                 "co.", "ltd.", "ltd", "corp.", "corp", ",", ".", " "):
        t = t.replace(junk, "")
    return t


def _label(rec):
    """사람이 읽을 한 줄 식별자: 회사 · 이름/제목."""
    company = rec.get("company") or rec.get("company_name") or rec.get("account") or "회사?"
    who = rec.get("name") or rec.get("contact") or rec.get("person") or rec.get("title") or ""
    return f"{company} {who}".strip()


def _days_since(iso):
    try:
        dt = datetime.datetime.fromisoformat(str(iso))
        return (datetime.datetime.now(tz=dt.tzinfo) - dt).days
    except (ValueError, TypeError):
        return None


# ---------- 핵심 동작 ----------
def add(entity, data):
    rows = read_all(entity)
    rec = dict(data)
    rec["id"] = _next_id(entity, rows)
    rec["created"] = rec["updated"] = now_iso()
    _append(_path(entity), rec)
    return rec


def update(entity, rec_id, patch):
    rows = read_all(entity)
    hit = None
    for r in rows:
        if r.get("id") == rec_id:
            r.update(patch)
            r["updated"] = now_iso()
            hit = r
            break
    if hit is None:
        return None
    _rewrite(_path(entity), rows)
    return hit


def find(entity, query):
    q = _norm(query)
    out = []
    for r in read_all(entity):
        for k in FIND_KEYS:
            if q and q in _norm(r.get(k)):
                out.append(r)
                break
    return out


def dedupe(entity, data):
    """이메일 / 전화 / 이름+회사 일치 후보를 (사유, 레코드) 목록으로."""
    email = _norm(data.get("email"))
    phone = _norm_phone(data.get("phone"))
    name = _norm(data.get("name"))
    company = _norm_company(data.get("company") or data.get("company_name") or data.get("account"))
    hits = []
    for r in read_all(entity):
        if email and email == _norm(r.get("email")):
            hits.append(("이메일 일치", r))
            continue
        rp = _norm_phone(r.get("phone"))
        if phone and rp and (phone == rp or (len(phone) >= 8 and phone[-8:] == rp[-8:])):
            hits.append(("전화 일치", r))
            continue
        rc = _norm_company(r.get("company") or r.get("company_name") or r.get("account"))
        if name and company and name == _norm(r.get("name")) and company == rc:
            hits.append(("이름+회사 일치", r))
    return hits


def _actives():
    """(entity, record) — 다음 행동을 요구하는 활성 리드·기회만."""
    out = []
    for r in read_all("lead"):
        if _norm(r.get("status")) not in INACTIVE_LEAD_STATUS:
            out.append(("lead", r))
    for r in read_all("opportunity"):
        if _norm_stage(r.get("stage")) not in INACTIVE_OPP_STAGE:
            out.append(("opportunity", r))
    return out


def next_missing():
    """다음 행동(next_action/next_action_date) 빠진 활성 리드·기회. 훅·queue_today가 재사용.
    반환: [{"entity","id","label","status","missing","updated","record"}]"""
    out = []
    for entity, r in _actives():
        missing = [k for k in ("next_action", "next_action_date") if not str(r.get(k) or "").strip()]
        if not missing:
            continue
        out.append({
            "entity": entity,
            "id": r.get("id", "?"),
            "label": _label(r),
            "status": r.get("status") or r.get("stage") or "?",
            "missing": missing,
            "updated": r.get("updated", ""),
            "record": r,
        })
    return out


def suppress_check(email):
    """수신거부 등록 여부(True=발송 금지). 발송 게이트 1번 검사."""
    e = _norm(email)
    return any(e and e == _norm(r.get("email"))
               for r in read_all(path=os.path.join(CRM_DIR, SUPPRESSIONS)))


def suppress_add(email, reason=""):
    rec = {"email": _norm(email), "reason": reason or "", "created": now_iso()}
    _append(os.path.join(CRM_DIR, SUPPRESSIONS), rec)
    return rec


def stats():
    """엔티티별 건수 · 다음 행동 보유율(핵심 KPI, 목표 100%) · 방치 리드 수."""
    cfg = load_config(soft=True)
    stale_days = int(cfg.get("cadence", {}).get("stale_days", 21) or 21)
    counts = {e: len(read_all(e)) for e in ENTITIES}
    counts["suppression"] = len(read_all(path=os.path.join(CRM_DIR, SUPPRESSIONS)))
    actives = _actives()
    with_next = [r for _, r in actives if str(r.get("next_action") or "").strip()]
    rate = round(100 * len(with_next) / len(actives)) if actives else 100
    stale = 0
    for entity, r in actives:
        if entity != "lead":
            continue
        d = _days_since(r.get("updated"))
        if d is not None and d > stale_days:
            stale += 1
    return {
        "counts": counts,
        "active_total": len(actives),
        "active_with_next_action": len(with_next),
        "next_action_rate": rate,
        "stale_leads": stale,
        "stale_days": stale_days,
    }


# ---------- CLI ----------
def _print_rec(rec):
    print(json.dumps(rec, ensure_ascii=False, indent=2))


def _parse_json(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"[오류] --json 파싱 실패: {e}")
    if not isinstance(data, dict):
        raise SystemExit("[오류] --json 은 JSON 객체({...})여야 합니다.")
    return data


def cmd_next_missing():
    rows = next_missing()
    if rows:
        print("⚠️ 다음 행동 없는 활성 리드·기회 — 오늘 안에 다음 행동·날짜를 채울 것")
        print("| 유형 | ID | 대상 | 상태 | 빠진 항목 | 마지막 갱신 |")
        print("|---|---|---|---|---|---|")
        for m in rows:
            d = _days_since(m["updated"])
            when = f"{m['updated'][:10]} ({d}일 전)" if d is not None else "확인 필요"
            kind = "리드" if m["entity"] == "lead" else "기회"
            print(f"| {kind} | {m['id']} | {m['label']} | {m['status']} "
                  f"| {', '.join(m['missing'])} | {when} |")
    else:
        print("다음 행동 없는 활성 리드·기회 없음 — 다음 행동 보유율 100% ✅")
    print(f"총 {len(rows)}건")


def cmd_stats():
    s = stats()
    names = {"account": "회사", "contact": "연락처", "relationship": "관계", "lead": "리드",
             "activity": "활동", "opportunity": "영업기회", "suppression": "수신거부"}
    print("📞 로컬 CRM 현황")
    for e, n in s["counts"].items():
        print(f"- {names.get(e, e)}: {n}건")
    print(f"- 다음 행동 보유율: {s['next_action_rate']}% "
          f"({s['active_with_next_action']}/{s['active_total']} · 목표 100%)")
    print(f"- 방치 리드(갱신 {s['stale_days']}일 초과): {s['stale_leads']}건")


def main():
    p = argparse.ArgumentParser(description="Sales Copilot 로컬 최소 CRM (JSONL)")
    sub = p.add_subparsers(dest="cmd", required=True)
    ent = dict(choices=sorted(ENTITIES))

    sp = sub.add_parser("add", help="레코드 추가(id·created·updated 자동)")
    sp.add_argument("entity", **ent)
    sp.add_argument("--json", required=True, dest="payload")

    sp = sub.add_parser("update", help="부분 갱신(merge)")
    sp.add_argument("entity", **ent)
    sp.add_argument("id")
    sp.add_argument("--json", required=True, dest="payload")

    sp = sub.add_parser("get", help="id로 조회")
    sp.add_argument("entity", **ent)
    sp.add_argument("id")

    sp = sub.add_parser("list", help="목록 조회")
    sp.add_argument("entity", **ent)
    sp.add_argument("--where", action="append", default=[], metavar="key=value")
    sp.add_argument("--limit", type=int, default=0)

    sp = sub.add_parser("find", help="이름·회사·이메일 부분일치 검색")
    sp.add_argument("entity", **ent)
    sp.add_argument("query", nargs="+")

    sp = sub.add_parser("dedupe", help="중복 후보 탐지(이메일·전화·이름+회사)")
    sp.add_argument("entity", **ent)
    sp.add_argument("--json", required=True, dest="payload")

    sub.add_parser("next-missing", help="다음 행동 없는 활성 리드·기회 (핵심 KPI)")

    sp = sub.add_parser("suppress", help="수신거부 등록: suppress add --email ...")
    sp.add_argument("action", choices=["add"])
    sp.add_argument("--email", required=True)
    sp.add_argument("--reason", default="")

    sp = sub.add_parser("suppress-check", help="수신거부 검사(등록돼 있으면 종료코드 1)")
    sp.add_argument("--email", required=True)

    sub.add_parser("stats", help="건수·다음 행동 보유율·방치 리드 수")

    a = p.parse_args()

    if a.cmd == "add":
        _print_rec(add(a.entity, _parse_json(a.payload)))
    elif a.cmd == "update":
        rec = update(a.entity, a.id, _parse_json(a.payload))
        if rec is None:
            raise SystemExit(f"[오류] {a.entity} {a.id} 를 찾지 못했습니다.")
        _print_rec(rec)
    elif a.cmd == "get":
        for r in read_all(a.entity):
            if r.get("id") == a.id:
                _print_rec(r)
                return
        raise SystemExit(f"[오류] {a.entity} {a.id} 를 찾지 못했습니다.")
    elif a.cmd == "list":
        rows = read_all(a.entity)
        for cond in a.where:
            if "=" not in cond:
                raise SystemExit(f"[오류] --where 형식은 key=value 입니다: {cond}")
            k, v = cond.split("=", 1)
            rows = [r for r in rows if _norm(r.get(k)) == _norm(v)]
        if a.limit > 0:
            rows = rows[:a.limit]
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        print(f"# 총 {len(rows)}건", file=sys.stderr)
    elif a.cmd == "find":
        rows = find(a.entity, " ".join(a.query))
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        if not rows:
            print("검색 결과 없음")
    elif a.cmd == "dedupe":
        hits = dedupe(a.entity, _parse_json(a.payload))
        if not hits:
            print("중복 없음")
        else:
            print(f"⚠️ 중복 후보 {len(hits)}건 — 새로 만들지 말고 update 를 검토할 것")
            for reason, r in hits:
                print(f"[{reason}] {json.dumps(r, ensure_ascii=False)}")
    elif a.cmd == "next-missing":
        cmd_next_missing()
    elif a.cmd == "suppress":
        rec = suppress_add(a.email, a.reason)
        print(f"수신거부 등록: {rec['email']}" + (f" (사유: {rec['reason']})" if rec["reason"] else ""))
    elif a.cmd == "suppress-check":
        if suppress_check(a.email):
            print(f"SUPPRESSED: {a.email} — 수신거부 등록됨. 발송 금지, 다른 경로([[send-policy]] 분기)로.")
            raise SystemExit(1)
        print("OK")
    elif a.cmd == "stats":
        cmd_stats()


if __name__ == "__main__":
    main()
