"""受控重配对组合场景测试。

6 人赛事 / 2 轮已发布。真实时间线：
  ① R1 胜负翻转改判（跨轮影响：积分与对手小分变化，R2 对阵表不变）
  ② R2 第一桌开赛（锁定），选手甲退赛（自动取消其未开赛桌）
  ③ 生成重排方案 A（空闲 3 人 → 1 桌 + 1 轮空）
  ④ 确认期间，另一张未开赛桌开赛 → 确认 A 必须 409：方案过期
  ⑤ 选手乙也退赛；重新生成方案 B（2 张锁定桌 + 2 名空闲选手）→ 确认成功
  ⑥ 重复确认 / 重复提交同一裁定：只形成一次有效变更
  ⑦ 历史轮次按发布时状态回看（快照不变）；退赛者成绩保留、后续不再配对
另测：退赛导致硬约束无解时，方案明确不可行，不会悄悄放松。
"""
import os
import sys
import tempfile

from fastapi.testclient import TestClient

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.db import Base  # noqa: E402

Base.metadata.create_all(bind=__import__("app.db", fromlist=["engine"]).engine)
client = TestClient(app)

NAMES = ["甲", "乙", "丙", "丁", "戊", "己"]


def _mk():
    r = client.post("/tournaments", json={
        "name": "受控重排组合测试", "total_rounds": 4,
        "players": [{"registration_no": i, "name": n,
                     "rating": 1800 - i * 50}
                    for i, n in enumerate(NAMES, 1)],
    })
    assert r.status_code == 201
    return r.json()["id"]


def _publish_and_fill(tid, result):
    client.post(f"/tournaments/{tid}/rounds/publish", json={})
    st = client.get(f"/tournaments/{tid}/status").json()
    rd = st["rounds"][-1]
    for g in rd["games"]:
        if not g["is_bye"]:
            client.post(f"/games/{g['id']}/result", json={"result": result})
    return client.get(f"/tournaments/{tid}/status").json()["rounds"][-1]


def _status(tid):
    return client.get(f"/tournaments/{tid}/status").json()


def test_controlled_repair_combo():
    tid = _mk()
    pid = {p["name"]: p["id"] for p in _status(tid)["tournament"]["players"]}

    r1 = _publish_and_fill(tid, "W")
    client.post(f"/tournaments/{tid}/rounds/publish", json={})
    r2 = _status(tid)["rounds"][1]
    boards = [g for g in r2["games"] if not g["is_bye"]]
    assert len(boards) == 3

    # ① R1 翻转：甲参与的棋 W -> B
    target = next(g for g in r1["games"]
                  if not g["is_bye"] and
                  pid["甲"] in (g["white_id"], g["black_id"]))
    corr = client.post(f"/games/{target['id']}/correction",
                       json={"new_result": "B", "reason": "申诉成立数子翻转"}).json()
    assert corr["verdict"] == "W" and corr["current_result"] == "B"
    assert len(corr["ranking_impact"]) >= 2          # 跨轮影响
    # R2 对阵表不受成绩改判影响
    assert len([g for g in _status(tid)["rounds"][1]["games"]
                if g["status"] == "active" and not g["is_bye"]]) == 3
    # 重复提交同一裁定：幂等，一次有效变更
    again = client.post(f"/games/{target['id']}/correction",
                        json={"new_result": "B", "reason": "申诉成立数子翻转"})
    assert again.json()["idempotent"] is True
    gdetail = next(g for g in _status(tid)["rounds"][0]["games"]
                   if g["id"] == target["id"])
    assert len(gdetail["corrections"]) == 1

    # ② 第一桌开赛锁定
    board0, board1, board2 = boards
    client.post(f"/games/{board0['id']}/start")
    client.post(f"/games/{board0['id']}/start")      # 幂等
    locked_ids = {board0["white_id"], board0["black_id"]}

    # 甲退赛（若甲在锁定桌则选一个不在锁定桌的选手）
    wd1 = "甲" if pid["甲"] not in locked_ids else \
        next(n for n in NAMES if pid[n] not in locked_ids)
    r = client.post(f"/tournaments/{tid}/players/{pid[wd1]}/withdraw",
                    json={"reason": f"{wd1} 因伤退赛"})
    assert r.status_code == 200, r.text
    assert r.json()["auto_cancelled_game_ids"]       # 未开赛桌被自动取消
    # 锁定桌选手退赛被拒
    locked_name = next(n for n in NAMES if pid[n] in locked_ids)
    assert client.post(f"/tournaments/{tid}/players/{pid[locked_name]}/withdraw",
                       json={"reason": "开赛桌退赛必须拒绝"}).status_code == 409

    # ③ 方案 A
    planA = client.post(f"/tournaments/{tid}/repairs/plan", json={}).json()
    assert planA["feasible"] is True
    assert planA["diff"]["locked_boards"] == 1
    assert planA["diff"]["byes_after"]               # 在场 5 人(奇) → 1 轮空
    # 锁定桌保留、退赛者不在新桌
    pairs = {tuple(sorted((p["white_id"], p["black_id"])))
             for p in planA["report"]["pairs"]}
    assert tuple(sorted((board0["white_id"], board0["black_id"]))) in pairs
    for pair in pairs:
        assert pid[wd1] not in pair

    # ④ 确认期间，另一张仍 active 的未开赛桌开赛
    st_mid = _status(tid)["rounds"][1]
    extra = next(g for g in st_mid["games"]
                 if g["status"] == "active" and not g["is_bye"] and not g["started"]
                 and g["id"] != board0["id"])
    client.post(f"/games/{extra['id']}/start")
    expired = client.post(f"/repairs/{planA['revision_id']}/confirm")
    assert expired.status_code == 409
    assert "过期" in expired.json()["detail"]["message"]
    revs = _status(tid)["rounds"][1]["revisions"]
    assert revs[-1]["status"] == "expired"

    # ⑤ 再退一人（选不在两张锁定桌的）
    locked2 = locked_ids | {extra["white_id"], extra["black_id"]}
    wd2 = next(n for n in NAMES
               if pid[n] not in locked2 and n != wd1)
    client.post(f"/tournaments/{tid}/players/{pid[wd2]}/withdraw",
                json={"reason": f"{wd2} 因故退赛"})

    planB = client.post(f"/tournaments/{tid}/repairs/plan", json={}).json()
    assert planB["feasible"] is True, planB.get("report", {}).get("infeasibility")
    assert planB["diff"]["locked_boards"] == 2
    # 改动桌数与代价同时给出
    assert isinstance(planB["diff"]["changed_boards"], int)
    assert {"A_score_gap_half_points", "B_bye", "C_color",
            "unpaired_count"} <= set(planB["diff"]["proposed_cost"])
    assert planB["diff"]["proposed_cost"]["unpaired_count"] == 0
    ok = client.post(f"/repairs/{planB['revision_id']}/confirm")
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "applied"

    r2_now = _status(tid)["rounds"][1]
    active = [g for g in r2_now["games"] if g["status"] == "active" and not g["is_bye"]]
    cancelled = [g for g in r2_now["games"] if g["status"] == "cancelled"]
    assert {g["id"] for g in active} == {board0["id"], extra["id"]}  # 两张锁定桌
    assert cancelled and all(g["cancelled_reason"] for g in cancelled)

    # 退赛者成绩保留、仍在榜单
    st = _status(tid)
    standings = {s["player_id"]: s for s in st["standings"]}
    for n in (wd1, wd2):
        s = standings[pid[n]]
        assert s["withdrawn"] and s["played"] + s["byes"] >= 1

    # ⑥ 重复确认幂等
    again_confirm = client.post(f"/repairs/{planB['revision_id']}/confirm").json()
    assert again_confirm["status"] == "already_applied"

    # ⑦ 历史快照不变；applied 修订可查
    r1_view = _status(tid)["rounds"][0]
    assert r1_view["pairing_snapshot"]["pairs"] == r1["pairing_snapshot"]["pairs"]
    applied = [rv for rv in r2_now["revisions"] if rv["status"] == "applied"]
    assert len(applied) == 1 and applied[0]["diff"]["kept"]

    # R3 发布：两名退赛者移出，4 名在场选手 2 桌无轮空
    pub3 = client.post(f"/tournaments/{tid}/rounds/publish", json={})
    assert pub3.status_code == 201
    snap = pub3.json()["pairing_snapshot"]
    assert len(snap["pairs"]) == 2 and snap["byes"] == []
    for p in snap["pairs"]:
        assert p["white_id"] not in {pid[wd1], pid[wd2]}
        assert p["black_id"] not in {pid[wd1], pid[wd2]}


def test_repair_infeasible_blocks_and_override_annotated():
    """退赛把空闲选手切成两个已交手的孤立点时：重排无解，确认被阻止。"""
    tid = _mk()
    pid = {p["name"]: p["id"] for p in _status(tid)["tournament"]["players"]}
    _publish_and_fill(tid, "W")
    r2 = client.post(f"/tournaments/{tid}/rounds/publish", json={}).json()
    boards = [p for p in r2["pairing_snapshot"]["pairs"]]

    # 锁定一张 R2 桌
    full = _status(tid)
    st = full["rounds"][1]
    g0 = next(g for g in st["games"] if not g["is_bye"])
    client.post(f"/games/{g0['id']}/start")

    # 找到两个退赛者，使剩余 2 名空闲选手在 R1 已交手（无解）
    locked = {g0["white_id"], g0["black_id"]}
    free_players = [p["id"] for p in full["tournament"]["players"]
                    if p["id"] not in locked]
    r1_pairs = [tuple(sorted((g["white_id"], g["black_id"])))
                for g in full["rounds"][0]["games"] if not g["is_bye"]]

    wd = None
    for a in range(len(free_players)):
        for b in range(a + 1, len(free_players)):
            rem = [x for x in free_players if x not in {free_players[a], free_players[b]}]
            if len(rem) == 2 and tuple(sorted(rem)) in r1_pairs:
                wd = [free_players[a], free_players[b]]
                break
        if wd:
            break
    assert wd, "测试数据应存在导致无解的退赛组合"
    for x in wd:
        rr = client.post(f"/tournaments/{tid}/players/{x}/withdraw",
                         json={"reason": "构造无解重排退赛"})
        assert rr.status_code == 200

    plan = client.post(f"/tournaments/{tid}/repairs/plan", json={}).json()
    assert plan["feasible"] is False
    assert plan["report"]["infeasibility"]["root_cause"] == "NO_REPEAT"
    # 确认无解方案必须被阻止
    blocked = client.post(f"/repairs/{plan['revision_id']}/confirm")
    assert blocked.status_code == 422
    # 授权放宽（带理由）后可行且全程标注
    plan_ov = client.post(f"/tournaments/{tid}/repairs/plan",
                          json={"override_no_repeat": True,
                                "override_reason": "裁判长授权重复交手收官"}).json()
    assert plan_ov["feasible"] is True
    ok = client.post(f"/repairs/{plan_ov['revision_id']}/confirm")
    assert ok.status_code == 200
    hc = {h["code"]: h["status"] for h in plan_ov["report"]["hard_constraints"]}
    assert hc["NO_REPEAT"] == "OVERRIDDEN"
    assert hc["LOCKED_BOARDS"] == "SATISFIED"
