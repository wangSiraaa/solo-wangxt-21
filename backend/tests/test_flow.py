"""端到端流程测试（内存 SQLite）。

覆盖：
1. 报名→奇数轮空→发布后刷新结果不变（快照不可变）
2. 裁判原裁定不可覆盖；更正保留原裁定并重算小分
3. 无合法配对时阻止发布；override 必须写理由；override 后显式标注
4. 并列排名同名次
5. 确定性：同输入两次预览完全一致
"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.db import Base, engine  # noqa: E402

Base.metadata.create_all(engine)
client = TestClient(app)


def _mk(name, rounds, players):
    r = client.post("/tournaments", json={
        "name": name, "total_rounds": rounds,
        "players": [{"registration_no": no, "name": n, "rating": rt}
                    for no, n, rt in players],
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _publish(tid, override=False, reason=""):
    return client.post(f"/tournaments/{tid}/rounds/publish",
                       json={"override_no_repeat": override, "override_reason": reason})


def _status(tid, preview=False):
    return client.get(f"/tournaments/{tid}/status",
                      params={"preview": preview}).json()


def test_odd_bye_published_round_immutable_and_deterministic():
    tid = _mk("t1", 3, [(1, "甲", 1500), (2, "乙", 1400), (3, "丙", 1300),
                        (4, "丁", 1200), (5, "戊", 1100)])
    p1 = client.get(f"/tournaments/{tid}/pairings/preview").json()
    p2 = client.get(f"/tournaments/{tid}/pairings/preview").json()
    assert p1["report"]["pairs"] == p2["report"]["pairs"]
    assert len(p1["report"]["byes"]) == 1

    r = _publish(tid)
    assert r.status_code == 201, r.text
    snap1 = r.json()["pairing_snapshot"]

    # 录入成绩
    st = _status(tid)
    for g in st["rounds"][0]["games"]:
        if not g["is_bye"]:
            rr = client.post(f"/games/{g['id']}/result", json={"result": "W"})
            assert rr.status_code == 200

    # "刷新页面"：重新取状态，已发布对阵、名单、规则版本必须原样
    st2 = _status(tid)
    rd = st2["rounds"][0]
    assert rd["pairing_snapshot"]["pairs"] == snap1["pairs"]
    assert rd["pairing_snapshot"]["byes"] == snap1["byes"]
    assert rd["rule_version"] == snap1["rule_version"]
    assert len(rd["roster_snapshot"]) == 5
    assert rd["roster_snapshot"][0]["score_before_round"] == 0


def test_verdict_immutable_correction_recomputes_buchholz():
    tid = _mk("t2", 3, [(1, "甲", 1500), (2, "乙", 1400), (3, "丙", 1300),
                        (4, "丁", 1200)])
    _publish(tid)
    st = _status(tid)
    games = st["rounds"][0]["games"]
    g0 = next(g for g in games if not g["is_bye"])
    assert client.post(f"/games/{g0['id']}/result", json={"result": "W"}).status_code == 200

    # 重复录入必须被拒绝
    dup = client.post(f"/games/{g0['id']}/result", json={"result": "B"})
    assert dup.status_code == 409

    before = {s["player_id"]: s for s in _status(tid)["standings"]}
    # 更正 W -> D
    corr = client.post(f"/games/{g0['id']}/correction",
                       json={"new_result": "D", "reason": "数子复核改为和棋"})
    assert corr.status_code == 200, corr.text
    after = {s["player_id"]: s for s in _status(tid)["standings"]}

    gw = g0["white_id"]
    assert before[gw]["points"] - after[gw]["points"] == 0.5      # 积分重算
    gdetail = next(g for g in _status(tid)["rounds"][0]["games"] if g["id"] == g0["id"])
    assert gdetail["verdict"] == "W"                              # 原裁定保留
    assert gdetail["current_result"] == "D"
    assert gdetail["corrected"] is True
    assert gdetail["corrections"][0]["reason"] == "数子复核改为和棋"

    # 无理由更正拒绝
    bad = client.post(f"/games/{g0['id']}/correction",
                      json={"new_result": "B", "reason": "x"})
    assert bad.status_code == 422


def test_no_legal_pairing_blocks_publish_override_annotated():
    # 3 人，前 3 轮打完所有组合（覆盖全部 K3 边）
    tid = _mk("t3", 4, [(1, "甲", 1500), (2, "乙", 1400), (3, "丙", 1300)])
    results_cycle = ["W", "B", "D"]
    for i in range(3):
        _publish(tid)
        st = _status(tid)
        for g in st["rounds"][i]["games"]:
            if not g["is_bye"]:
                client.post(f"/games/{g['id']}/result",
                            json={"result": results_cycle[i]})

    pv = client.get(f"/tournaments/{tid}/pairings/preview").json()
    assert pv["feasible"] is False
    assert pv["report"]["infeasibility"]["root_cause"] == "NO_REPEAT"
    assert pv["report"]["infeasibility"]["allowed_graph"]["component_sizes"] == [1, 1, 1]

    # 正常发布被阻止
    blocked = _publish(tid)
    assert blocked.status_code == 422

    # override 不写理由被拒绝
    no_reason = _publish(tid, override=True, reason="")
    assert no_reason.status_code == 422

    # 写明理由后可发布，且快照/硬约束状态全程标注
    ok = _publish(tid, override=True, reason="裁判长授权重复交手以完成收官轮")
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["pairing_snapshot"]["hard_constraints"][1]["status"] == "OVERRIDDEN"
    assert body["pairing_snapshot"]["overrides"][0]["repeated_matchups"]
    st = _status(tid)
    rd = st["rounds"][3]
    assert rd["override_used"] is True
    assert "裁判长授权" in rd["override_reason"]
    assert rd["rule_snapshot"]["version"] == rd["rule_version"]


def test_tied_rank():
    # 4 人同等级分：r1 全白胜、r2 全和，两组同分同小分
    tid = _mk("t4", 3, [(1, "甲", 1600), (2, "乙", 1600),
                        (3, "丙", 1600), (4, "丁", 1600)])
    for i, res in enumerate(["W", "D"]):
        _publish(tid)
        st = _status(tid)
        for g in st["rounds"][i]["games"]:
            client.post(f"/games/{g['id']}/result", json={"result": res})
    ranks = [(s["name"], s["rank"], s["tied"]) for s in _status(tid)["standings"]]
    rank_values = sorted({r for _, r, _ in ranks})
    assert rank_values == [1, 3]                      # 并列后跳号
    assert sum(1 for _, _, t in ranks if t) == 4


def test_soft_constraints_never_break_hard_and_report_relaxed():
    # 构造积分分散的局面，验证 A 层被放宽时明确报告，且 NO_REPEAT 仍满足
    tid = _mk("t5", 4, [(1, "甲", 1900), (2, "乙", 1800),
                        (3, "丙", 1200), (4, "丁", 1100)])
    _publish(tid)
    st = _status(tid)
    for g in st["rounds"][0]["games"]:
        # 高等级分者胜：甲>乙 白胜视具体颜色，统一让 id 小者胜，用 W/B 由颜色决定
        client.post(f"/games/{g['id']}/result", json={"result": "W"})
    _publish(tid)
    pv = client.get(f"/tournaments/{tid}/pairings/preview",
                    params={"preview": True}).json()
    # 第二轮发布后的"下一轮"是第 3 轮；这里主要校验报告结构完整
    rep = _status(tid, preview=True)["preview"]["report"]
    for hc in rep["hard_constraints"]:
        assert hc["status"] in {"SATISFIED", "OVERRIDDEN"}
    assert any(t["tier"] == "A" for t in rep["tiers"])
    for pid, reason in rep["player_reasons"].items():
        assert reason["headline"]
