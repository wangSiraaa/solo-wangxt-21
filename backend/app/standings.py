"""积分、小分与并列排名。

数据全部来自已发布轮次（rounds 快照）与其中 games 的 *当前生效结果*；
未发布轮次不参与任何计算。成绩更正后重新聚合即可重算所有小分，
原始裁定保留在 result_corrections 中，本模块只读取 current_result。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

# current_result -> (白方半子, 黑方半子)；BYE 在白方记录
RESULT_SCORE2 = {
    "W": (2, 0),
    "B": (0, 2),
    "D": (1, 1),
    "BYE": (2, 0),
}


@dataclass
class Standing:
    player_id: int
    registration_no: int
    name: str
    rating: int
    played: int
    byes: int
    wins: int
    draws: int
    losses: int
    points: float
    buchholz: float          # 对手分（轮空轮对手按 0 分）
    buchholz_cut1: float     # 去掉一个最高对手分
    color_white: int
    color_black: int
    rank: int                # 并列同名次
    tied_with: list[int]
    correction_count: int


def aggregate(tournament, rounds, games_by_round) -> list[dict]:
    """从快照重建每人的当前状态。

    rounds: 已发布 Round 列表（含 pairing_snapshot）
    games_by_round: {round_id: [Game...]}
    返回：pid -> 状态 dict，其中 opponents 为 [(对手pid, 对手这局是否轮空对我无影响)]
    """
    roster = {p.id: p for p in tournament.players}
    st: dict[int, dict] = {}
    for pid, p in roster.items():
        st[pid] = {
            "pid": pid, "name": p.name, "rating": p.rating,
            "registration_no": p.registration_no,
            "score2": 0, "whites": 0, "blacks": 0, "byes": 0,
            "last_color": "", "opponents": set(),
            "wins": 0, "draws": 0, "losses": 0, "played": 0,
            "opponent_points_at_meeting": [],  # 用于小分（重算时换成终局对手分）
        }

    for rd in rounds:
        snap = rd.pairing_snapshot
        byes = set(snap.get("byes", []))
        for pid in byes:
            st[pid]["score2"] += 2
            st[pid]["byes"] += 1
            st[pid]["wins"] += 1   # 轮空按胜计分（规则快照中明确）
            st[pid]["last_color"] = ""
            # 轮空轮没有对手；Buchholz 该轮按 0 分
            st[pid]["opponent_points_at_meeting"].append(None)

        for g in games_by_round.get(rd.id, []):
            if g.is_bye:
                # 轮空已由 pairing_snapshot.byes 权威记录，跳过避免双重计入
                continue
            w, b = g.white_id, g.black_id
            if w not in st or b not in st:
                continue
            if g.current_result not in RESULT_SCORE2:
                continue   # 已发布但裁判尚未录入：不计分、不占颜色
            sw, sb = RESULT_SCORE2[g.current_result]
            st[w]["score2"] += sw
            st[b]["score2"] += sb
            st[w]["whites"] += 1
            st[b]["blacks"] += 1
            st[w]["last_color"] = "W"
            st[b]["last_color"] = "B"
            st[w]["opponents"].add(b)
            st[b]["opponents"].add(w)
            st[w]["played"] += 1
            st[b]["played"] += 1
            # 战绩
            if g.current_result == "W":
                st[w]["wins"] += 1; st[b]["losses"] += 1
            elif g.current_result == "B":
                st[b]["wins"] += 1; st[w]["losses"] += 1
            else:
                st[w]["draws"] += 1; st[b]["draws"] += 1
            # 对手分先记账，终局对手分在 standings 中用最终积分填
            st[w]["opponent_points_at_meeting"].append(b)
            st[b]["opponent_points_at_meeting"].append(w)
    return st


def compute_standings(tournament, rounds, games_by_round, correction_counts=None) -> list[Standing]:
    st = aggregate(tournament, rounds, games_by_round)
    points = {pid: v["score2"] / 2 for pid, v in st.items()}

    rows = []
    for pid, v in st.items():
        # Buchholz：每盘计入对手当前总积分；轮空轮按 0 分
        opp_pts = []
        for item in v["opponent_points_at_meeting"]:
            opp_pts.append(0.0 if item is None else points[item])
        buch = round(sum(opp_pts), 2)
        buch_cut = round(sum(opp_pts) - (max(opp_pts) if opp_pts else 0.0), 2)
        rows.append({
            "player_id": pid,
            "registration_no": v["registration_no"],
            "name": v["name"],
            "rating": v["rating"],
            "played": v["played"],
            "byes": v["byes"],
            "wins": v["wins"],
            "draws": v["draws"],
            "losses": v["losses"],
            "points": points[pid],
            "buchholz": buch,
            "buchholz_cut1": buch_cut,
            "color_white": v["whites"],
            "color_black": v["blacks"],
            # 竞技比较键：报名序号刻意不在其中
            "key": (-points[pid], -buch, -buch_cut, -v["wins"], -v["rating"]),
            "correction_count": (correction_counts or {}).get(pid, 0),
        })

    # 展示排序：竞技键之后再用报名序号（不影响并列判定）
    rows.sort(key=lambda r: (r["key"], r["registration_no"]))

    standings: list[Standing] = []
    i = 0
    while i < len(rows):
        j = i + 1
        while j < len(rows) and rows[j]["key"] == rows[i]["key"]:
            j += 1
        tied = [rows[k]["player_id"] for k in range(i, j)]
        rank = i + 1   # 标准竞赛排名：并列后跳号
        for k in range(i, j):
            r = rows[k]
            standings.append(Standing(
                player_id=r["player_id"], registration_no=r["registration_no"],
                name=r["name"], rating=r["rating"], played=r["played"], byes=r["byes"],
                wins=r["wins"], draws=r["draws"], losses=r["losses"],
                points=r["points"], buchholz=r["buchholz"],
                buchholz_cut1=r["buchholz_cut1"],
                color_white=r["color_white"], color_black=r["color_black"],
                rank=rank, tied_with=tied, correction_count=r["correction_count"],
            ))
        i = j
    return standings


def state_for_pairing(tournament, rounds, games_by_round):
    """聚合成 pairing.PlayerState 列表（顺序即报名序号，供求解器确定性使用）。"""
    from .pairing import PlayerState
    st = aggregate(tournament, rounds, games_by_round)
    out = []
    for p in sorted(tournament.players, key=lambda x: x.registration_no):
        if not p.active:
            continue
        v = st[p.id]
        out.append(PlayerState(
            pid=p.id, name=v["name"], rating=v["rating"],
            registration_no=v["registration_no"],
            score2=v["score2"], whites=v["whites"], blacks=v["blacks"],
            byes=v["byes"], last_color=v["last_color"], opponents=v["opponents"],
        ))
    return out
