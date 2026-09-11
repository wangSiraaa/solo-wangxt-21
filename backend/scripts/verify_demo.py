#!/usr/bin/env python3
"""一键验证：建库 → 播种四场演示赛事 → 断言关键不变量 → 打印可读报告。

用法：
  DATABASE_URL=sqlite:////tmp/check.db python scripts/verify_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app import models, repairs, services  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from scripts.seed_demo import seed_all  # noqa: E402

R = "\033[31m"; G = "\033[32m"; Y = "\033[33m"; C = "\033[36m"; X = "\033[0m"
ok = 0


def check(label, cond, detail=""):
    global ok
    mark = f"{G}✓{X}" if cond else f"{R}✗{X}"
    print(f"  {mark} {label}" + (f"  {Y}{detail}{X}" if detail else ""))
    if not cond:
        ok += 1


def main():
    Base.metadata.create_all(engine)
    ids = seed_all()
    db = SessionLocal()
    try:
        # ---- 演示1：奇数、轮空、更正 ----
        st = services.full_status(db, ids["odd"], True, False, "")
        print(f"\n{C}[演示1] 7人奇数场{X}")
        rounds = st["rounds"]
        check("两轮各 1 个轮空",
              all(len(r["pairing_snapshot"]["byes"]) == 1 for r in rounds))
        active = [g for r in rounds for g in r["games"] if g["status"] == "active"]
        def appearances(pid):
            n = 0
            for g in active:
                if g["is_bye"]:
                    n += 1 if g["white_id"] == pid else 0
                elif g["white_id"] == pid or g["black_id"] == pid:
                    n += 1
            return n
        check("每人两轮恰好出场一次（对局或轮空）",
              all(appearances(s["player_id"]) == 2 for s in st["standings"]))
        corr = [g for r in rounds for g in r["games"] if g["corrected"]]
        check("存在成绩更正且原裁定保留",
              corr and corr[0]["verdict"] != corr[0]["current_result"])

        # ---- 演示2：并列 ----
        st2 = services.full_status(db, ids["ties"], False, False, "")
        print(f"\n{C}[演示2] 4人并列场{X}")
        ranks = sorted({s["rank"] for s in st2["standings"]})
        check("名次为 1,3 跳号（两人并列第1）", ranks == [1, 3], str(ranks))
        check("4 人全部处于并列组",
              all(len(s["tied_with"]) == 2 for s in st2["standings"]))

        # ---- 演示3：无合法配对 ----
        st3 = services.full_status(db, ids["infeasible"], True, False, "")
        pv = st3["preview"]
        print(f"\n{C}[演示3] 3人无合法配对场{X}")
        check("下一轮预览无可行解", pv["feasible"] is False)
        check("根因 NO_REPEAT 且三人均孤立",
              pv["report"]["infeasibility"]["root_cause"] == "NO_REPEAT"
              and pv["report"]["infeasibility"]["allowed_graph"]["component_sizes"] == [1, 1, 1])

        blocked_ok = False
        try:
            services.publish_round(db, ids["infeasible"], False, "")
        except HTTPException as e:
            blocked_ok = e.status_code == 422
        check("硬规则无解时发布被阻止（不悄悄突破）", blocked_ok)

        # ---- 演示4：受控重排 ----
        print(f"\n{C}[演示4] 申诉+退赛 受控重排场{X}")
        st4 = services.full_status(db, ids["repair"], False, False, "")
        wd = [p for p in st4["tournament"]["players"] if p["withdrawn"]]
        check("叶让退赛且报名记录保留", len(wd) == 1 and wd[0]["name"] == "叶让")
        r2 = st4["rounds"][1]
        check("R2 有一张锁定（已开赛）桌",
              any(g["started"] for g in r2["games"] if g["status"] == "active"))
        check("退赛者的未开赛桌已取消但行保留",
              any(g["status"] == "cancelled" and g["cancelled_reason"] for g in r2["games"]))

        plan = repairs.create_repair_plan(db, ids["repair"], reason="验证重排")
        d = plan["diff"]
        check("重排方案可行且锁定桌不动",
              plan["feasible"] and d["locked_boards"] == 1)
        check("原表因退赛不可行（有人无桌）",
              d["baseline_viable"] is False and d["baseline_cost"]["unpaired_count"] == 1)
        check("给出改动桌数与两套代价",
              d["changed_boards"] >= 1 and "proposed_cost" in d)
        check("A 层代价不劣于原表",
              d["proposed_cost"]["A_score_gap_half_points"]
              <= d["baseline_cost"]["A_score_gap_half_points"])
        rev_id = plan["revision_id"]

        # 确认期间新开赛 → 过期
        st_mid = services.full_status(db, ids["repair"], False, False, "")
        free_g = next(g for g in st_mid["rounds"][1]["games"]
                      if g["status"] == "active" and not g["started"] and not g["is_bye"])
        repairs.mark_started(db, free_g["id"])
        expired = False
        try:
            repairs.confirm_repair(db, rev_id)
        except HTTPException as e:
            expired = e.status_code == 409
        check("确认期间有棋桌开赛 → 方案过期(409)", expired)

        plan2 = repairs.create_repair_plan(db, ids["repair"], reason="验证重排2")
        applied = repairs.confirm_repair(db, plan2["revision_id"])
        check("重排确认生效", applied["status"] == "applied")
        again = repairs.confirm_repair(db, plan2["revision_id"])
        check("重复确认幂等（只一次有效变更）", again["status"] == "already_applied")

        st5 = services.full_status(db, ids["repair"], False, False, "")
        check("退赛者历史成绩保留仍在榜单",
              any(s["name"] == "叶让" and s["points"] >= 1 for s in st5["standings"]))
        check("修订历史含 1 次生效记录",
              len([rv for rv in st5["rounds"][1]["revisions"] if rv["status"] == "applied"]) == 1)
        check("历史发布快照（名单/规则/依据）仍可回看",
              bool(st5["rounds"][0]["pairing_snapshot"].get("player_reasons"))
              and st5["rounds"][0]["rule_snapshot"]["version"]
              == st5["rounds"][0]["rule_version"])
    finally:
        db.close()

    print(f"\n{G if ok == 0 else R}验证{'全部通过' if ok == 0 else f'{ok} 项失败'}{X}")
    sys.exit(1 if ok else 0)


if __name__ == "__main__":
    main()
