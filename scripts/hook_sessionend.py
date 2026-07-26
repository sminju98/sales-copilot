#!/usr/bin/env python3
"""SessionEnd 훅 — ① 오늘 활동 원장을 영업 활동 로그 초안으로 정리(로컬, 사람이 검토·보완)
② '다음 행동 없는 리드·기회'를 파일로 남겨 다음 세션의 proactive가 첫마디로 짚게 한다
('다음 행동 없이 업무를 종료할 수 없음' 원칙 — proactive.py가 읽은 뒤 삭제).
주입 없음(로컬 파일만 씀)."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def _label(item):
    """crm.next_missing() 항목 하나를 한 줄 요약으로. 형태를 단정하지 않는다(방어적)."""
    if isinstance(item, dict):
        label = item.get("label") or ""
        if not label:
            rec = item.get("record") if isinstance(item.get("record"), dict) else item
            name = rec.get("name") or rec.get("contact") or rec.get("title") or ""
            comp = rec.get("company") or rec.get("account") or ""
            label = " · ".join(s for s in (name, comp) if s) or rec.get("id") or "(이름 없음)"
        ent = item.get("entity") or item.get("_entity") or ""
        return f"{label}[{ent}]" if ent else str(label)
    return str(item)


def _save_pending_next_actions():
    """crm.py 는 병렬 제작 중 — 없거나 실패하면 조용히 넘어간다."""
    try:
        import crm  # noqa: PLC0415
        missing = list(crm.next_missing() or [])
    except Exception:
        return
    if not missing:
        return
    try:
        os.makedirs(common.DATA_DIR, exist_ok=True)
        with open(common.PENDING_NEXT_ACTIONS, "w", encoding="utf-8") as f:
            json.dump({
                "count": len(missing),
                "top": [_label(x) for x in missing[:5]],
                "saved": common.now_iso(),
            }, f, ensure_ascii=False)
    except Exception:
        pass


def _write_worklog():
    today = datetime.date.today().isoformat()
    src = os.path.join(common.ACTIVITY_DIR, today + ".md")
    if not os.path.exists(src):
        return
    try:
        acts = open(src, encoding="utf-8").read().strip()
    except Exception:
        return
    if not acts:
        return
    outdir = os.path.join(common.DATA_DIR, "worklog")
    try:
        os.makedirs(outdir, exist_ok=True)
        out = os.path.join(outdir, f"{today}-draft.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(
                f"<!-- {common.PRIVATE_SENTINEL} -->\n"
                f"# 영업 활동 로그 · {today}  (자동 수집 — 사람이 검토·보완)\n\n"
                f"## 오늘의 접촉·작업\n{acts}\n\n"
                f"> [claude ai] 자동 초안입니다. **누구에게 무엇을 보냈고 어떤 회신이 왔는지**, "
                f"수주·실주와 각 리드의 다음 행동은 직접 채워 완성하세요.\n"
            )
    except Exception:
        pass


def main():
    p = common.proactive_cfg()
    if p is None or p.get("end_check", True) is False:
        return
    _write_worklog()
    # 다음 행동 없는 리드는 그냥 넘기지 않는다 — 다음 세션 첫마디로 인계
    _save_pending_next_actions()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 훅은 어떤 경우에도 세션을 방해하지 않는다
    sys.exit(0)
