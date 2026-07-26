#!/usr/bin/env python3
"""오늘의 행동 큐 빌더 (ACT-01~15) — '가만히 있지 못하게 만드는' 하루의 시작점.

로컬 CRM(crm.py)에서 ①오늘 도래 후속 ②신규 접촉 ③방치 리드 ④다음 행동 없는
리드·기회 ⑤전날 미완료 이월을 모아 data/queue/<날짜>.md 로 만든다.
형식은 templates/daily_queue.md 를 따른다. 값이 없으면 지어내지 않고 "확인 필요".

사용:
  python3 scripts/queue_today.py            # 오늘 큐 출력(없으면 빌드해서 출력)
  python3 scripts/queue_today.py --build    # 생성/갱신(완료 체크 [x]는 보존)
  python3 scripts/queue_today.py --build --date 2026-07-27
"""
import argparse
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crm  # noqa: E402
from common import QUEUE_DIR, load_config  # noqa: E402

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
ID_PAT = re.compile(r"\b(?:ac|ct|rl|ld|av|op)-\d{4,}\b")
_PRIORITY_WORDS = {"high": 3, "높음": 3, "상": 3, "medium": 2, "중": 2, "보통": 2,
                   "low": 1, "낮음": 1, "하": 1}


def _priority(rec):
    v = rec.get("priority")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(_PRIORITY_WORDS.get(str(v or "").strip().lower(), 0))


def _days_between(date_str, base):
    try:
        return (base - datetime.date.fromisoformat(str(date_str)[:10])).days
    except (ValueError, TypeError):
        return None


def _item(rec, todo, why, skill, done=False):
    mark = "x" if done else " "
    rid = rec.get("id", "?")
    return f"- [{mark}] {crm._label(rec)} ({rid}) · {todo} — 근거: {why} → 실행: [[{skill}]]"


def _prev_queue_path(date):
    """직전 큐 파일(어제, 없으면 최근 7일 내 가장 가까운 과거)을 찾는다 — 주말 건너뛰기 대응."""
    for back in range(1, 8):
        p = os.path.join(QUEUE_DIR, f"{(date - datetime.timedelta(days=back)).isoformat()}.md")
        if os.path.exists(p):
            return p
    return None


def _checked_ids(path):
    """기존 큐에서 이미 완료([x])된 항목의 레코드 id — 갱신 시 체크 상태를 보존한다."""
    ids = set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.lstrip().startswith("- [x]"):
                    ids.update(ID_PAT.findall(line))
    return ids


def build(date):
    cfg = load_config(soft=True)
    cadence = cfg.get("cadence", {})
    new_cap = int(cadence.get("daily_new_touches", 10) or 10)
    fu_cap = int(cadence.get("daily_followups", 10) or 10)
    stale_days = int(cadence.get("stale_days", 21) or 21)
    today = date.isoformat()
    out_path = os.path.join(QUEUE_DIR, f"{today}.md")
    done = _checked_ids(out_path)
    queued = set()  # 섹션 간 중복 방지(같은 리드를 두 번 시키지 않는다)

    leads = crm.read_all("lead")
    opps = crm.read_all("opportunity")
    active_leads = [r for r in leads if crm._norm(r.get("status")) not in crm.INACTIVE_LEAD_STATUS]
    active_opps = [r for r in opps if crm._norm_stage(r.get("stage")) not in crm.INACTIVE_OPP_STAGE]

    # ① 오늘 도래 후속: next_action_date <= 오늘 (날짜 오름차순)
    due = [( "lead", r) for r in active_leads] + [("opportunity", r) for r in active_opps]
    due = [(e, r) for e, r in due
           if str(r.get("next_action_date") or "")[:10] and str(r["next_action_date"])[:10] <= today]
    due.sort(key=lambda er: str(er[1]["next_action_date"])[:10])
    sec1 = []
    for e, r in due:
        d = _days_between(r["next_action_date"], date)
        late = f" · {d}일 경과" if d and d > 0 else " · 오늘"
        todo = str(r.get("next_action") or "").strip() or "다음 행동 내용 확인 필요"
        skill = "outreach" if e == "lead" else "pipeline"
        sec1.append(_item(r, todo, f"기한 {str(r['next_action_date'])[:10]}{late}", skill,
                          r.get("id") in done))
        queued.add(r.get("id"))

    # ② 신규 접촉: status=new, priority 내림차순, daily_new_touches 상한
    new_leads = [r for r in active_leads
                 if crm._norm(r.get("status")) == "new" and r.get("id") not in queued]
    new_leads.sort(key=_priority, reverse=True)
    overflow = max(0, len(new_leads) - new_cap)
    sec2 = []
    for r in new_leads[:new_cap]:
        warm = r.get("referrer") or ("소개" in str(r.get("source") or ""))
        why = str(r.get("reason") or r.get("contact_reason") or r.get("signal") or "").strip()
        if not why:
            why = (f"따뜻한 경로: {r['referrer']} 소개 가능" if r.get("referrer")
                   else "연락 이유 확인 필요(저위험 첫 질문부터)")
        skill = "relationships" if warm else "outreach"
        todo = "소개 요청" if warm else "첫 접촉(초안→발송 게이트)"
        sec2.append(_item(r, todo, why, skill, r.get("id") in done))
        queued.add(r.get("id"))

    # ③ 방치 리드: 마지막 갱신이 stale_days 초과, 최대 5건
    stale = []
    for r in active_leads:
        if r.get("id") in queued:
            continue
        d = crm._days_since(r.get("updated"))
        if d is not None and d > stale_days:
            stale.append((d, r))
    stale.sort(key=lambda x: -x[0])
    sec3 = []
    for d, r in stale[:5]:
        sec3.append(_item(r, "새 명분으로 재접촉", f"마지막 갱신 {d}일 전(기준 {stale_days}일)",
                          "revive", r.get("id") in done))
        queued.add(r.get("id"))

    # ④ 다음 행동 없는 리드·기회 (crm.next_missing 재사용 — 목표는 연락이 아니라 '지정')
    sec4 = []
    missing_ids = set()
    for m in crm.next_missing():
        kind = "리드" if m["entity"] == "lead" else "기회"
        missing_ids.add(m["id"])
        sec4.append(_item(m["record"], "다음 행동·날짜 지정",
                          f"{kind} 상태 {m['status']} · {', '.join(m['missing'])} 없음",
                          "pipeline", m["id"] in done))

    # ⑤ 전날 큐 미완료 이월
    sec5 = []
    prev = _prev_queue_path(date)
    if prev:
        prev_date = os.path.basename(prev)[:-3]
        with open(prev, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if not line.lstrip().startswith("- [ ]"):
                    continue
                ids = set(ID_PAT.findall(line))
                if ids & queued or ids & missing_ids:
                    continue  # 오늘 섹션에 이미 잡힌 항목은 이월하지 않는다
                sec5.append(f"{line} (이월: {prev_date})")

    # 오늘 미팅: 로컬 활동 기록에서만 찾는다(캘린더 미연동 시 지어내지 않음)
    meetings = []
    for r in crm.read_all("activity"):
        kind = crm._norm(r.get("type") or r.get("kind"))
        when = str(r.get("date") or r.get("when") or "")
        if kind in ("meeting", "미팅", "call", "콜", "demo", "데모") and when[:10] == today:
            t = when[11:16] if len(when) >= 16 else "시간 확인 필요"
            meetings.append(f"- {t} {crm._label(r)} · {r.get('subject') or kind}"
                            " — 브리핑: [[prepare-meeting]] / 끝나면 [[after-meeting]]")

    st = crm.stats()
    lines = [
        f"# 📞 오늘의 행동 큐 · {today} ({WEEKDAY_KO[date.weekday()]}) — "
        f"신규 {len(sec2)}건 / 후속 {len(sec1)}건 / 미팅 {len(meetings)}건",
        "",
        f"> 오늘 처리용량 기준(ACT-01~04): 신규 {new_cap} · 후속 {fu_cap}. 다 못 하면 위에서부터.",
        "",
        "## ① 오늘 도래 후속 (next_action_date ≤ 오늘) — ACT-02·ACT-07",
        *(sec1 or ["(없음)"]),
        "",
        "## ② 신규 접촉 — ACT-01·ACT-05·ACT-06",
        *(sec2 or ["(없음)"]),
    ]
    if overflow:
        lines.append(f"> 내일 이후 대기 {overflow}건 (하루 상한 {new_cap}건 초과분)")
    lines += [
        "",
        "## ③ 방치 리드 재접촉 (장기 미접촉) — ACT-08",
        *(sec3 or ["(없음)"]),
        "",
        "## ④ ⚠️ 다음 행동 없는 리드 — 오늘 안에 채워야 함 (ACT-09·ACT-10)",
        *(sec4 or ["(없음 — KPI 100% ✅)"]),
        "",
        "## ⑤ 이월 항목 (전날 미완료 재배치) — ACT-15",
        *(sec5 or ["(없음)"]),
        "",
        f"## 오늘 미팅 ({len(meetings)}건)",
        *(meetings or ["(로컬 기록엔 없음 — 캘린더 연동/확인 필요)"]),
        "",
        "---",
        f"**다음 행동 보유율: {st['next_action_rate']}% "
        f"({st['active_with_next_action']}/{st['active_total']})** — 목표 100%. ④가 0건이면 달성.",
        "저녁 마감(17시): 완료 체크 → 미완료는 내일 큐로 이월 · 요약은 [[metrics]] · 자동화는 [[routine]]",
        "",
    ]
    os.makedirs(QUEUE_DIR, exist_ok=True)
    body = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    return out_path, body


def main():
    p = argparse.ArgumentParser(description="오늘의 행동 큐 생성/조회")
    p.add_argument("--build", action="store_true", help="생성/갱신(완료 체크는 보존)")
    p.add_argument("--date", default=None, metavar="YYYY-MM-DD")
    a = p.parse_args()
    try:
        date = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    except ValueError:
        raise SystemExit(f"[오류] --date 형식은 YYYY-MM-DD 입니다: {a.date}")

    path = os.path.join(QUEUE_DIR, f"{date.isoformat()}.md")
    if a.build or not os.path.exists(path):
        path, body = build(date)
        print(body)
        print(f"[저장] {path}", file=sys.stderr)
    else:
        with open(path, encoding="utf-8") as f:
            print(f.read())


if __name__ == "__main__":
    main()
