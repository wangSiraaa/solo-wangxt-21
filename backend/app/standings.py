"""积分、小分与并列排名。

事实来源
--------
- 只统计已发布轮次中 status='active' 的棋桌：轮空（is_bye）计 1 分，
  普通棋桌按 game.current_result 计分；cancelled 棋桌（受控重排撤销）不统计。
- 这样成绩更正（改 current_result）与对阵重排（旧桌 cancel、新桌 active）
  对积分的影响天然分开：result_corrections 与 pairing_revisions 各自独立审计。
- 未录入结果的 active 棋桌不计分、不计颜色。
- 退赛者仍在排名中：其历史 active 棋桌全部保留，积分与对手小分照常计算。
"""
from __future__ import annotations

from dataclasses import dataclass

# current_result -> (白方半子, 黑方半子)；BYE 记在白方
RESULT_SCORE2 = {"W": (2, 0), "B": (0, 2), "D": (1, 1), "BYE": (2, 0)}


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
    buchholz: float
    buchholz_cut1: float
    color_white: int
    color_black: int
    rank: int
    tied_with: list[int]
    correction_count: int
    withdrawn: bool
    withdrawn_round_no: int | None


def aggregate(tournament, rounds, games_by_round):
    """从已发布轮次的 active 棋桌重建每人当前状态。退赛者同样参与聚合。"""
    roster = {p.id: p for p in tournament.players}
    st: dict[int, dict] = {}
    for pid, p in roster.items():
        st[pid] = {
            "pid": pid, "name": p.name, "rating": p.rating,
            "registration_no": p.registration_no,
            "score2": 0, "whites": 0, "blacks": 0, "byes": 0,
            "last_color": "", "opponents": set(),
            "wins": 0, "draws": 0, "losses": 0, "played": 0,
            "meetings": [],   # 每轮出场条目：对手 pid 或 None(轮空)，按轮有序
        }

    for rd in rounds:
        # 每个 active 选手在本轮至多一张 active 棋桌（服务层保证）
        met_this_round: dict[int, object] = {}
        for g in games_by_round.get(rd.id, []):
            if getattr(g, "status", "active") != "active":
                continue
            if g.is_bye:
                if g.white_id in st:
                    met_this_round[g.white_id] = None
                continue
            w, b = g.white_id, g.black_id
            if w not in st or b not in st or g.current_result not in RESULT_SCORE2:
                # 未录入结果的棋桌：本轮留空（不占颜色、不记交手）
                if w in st:
                    met_this_round.setdefault(w, None)
                if b in st:
                    met_this_round.setdefault(b, None)
                continue
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
            if g.current_result == "W":
                st[w]["wins"] += 1; st[b]["losses"] += 1
            elif g.current_result == "B":
                st[b]["wins"] += 1; st[w]["losses"] += 1
            else:
                st[w]["draws"] += 1; st[b]["draws"] += 1
            met_this_round[w] = b
            met_this_round[b] = w

        for pid, v in st.items():
            if pid in met_this_round:
                item = met_this_round[pid]
                if item is None:
                    # 可能是轮空，也可能是未录入棋桌；只有轮空计分
                    bye_game = any(
                        g.is_bye and g.white_id == pid
                        and getattr(g, "status", "active") == "active"
                        for g in games_by_round.get(rd.id, [])
                    )
                    if bye_game:
                        v["score2"] += 2
                        v["byes"] += 1
                        v["wins"] += 1
                        v["last_color"] = ""
                        v["meetings"].append(None)
                    # 未录入：不追加任何小分条目（该轮尚不可计）
                else:
                    v["meetings"].append(item)
    return st


def compute_standings(tournament, rounds, games_by_round, correction_counts=None) -> list[Standing]:
    st = aggregate(tournament, rounds, games_by_round)
    points = {pid: v["score2"] / 2 for pid, v in st.items()}

    rows = []
    for pid, v in st.items():
        opp_pts = [0.0 if item is None else points[item] for item in v["meetings"]]
        buch = round(sum(opp_pts), 2)
        buch_cut = round(sum(opp_pts) - (max(opp_pts) if opp_pts else 0.0), 2)
        rows.append({
            "player_id": pid, "registration_no": v["registration_no"],
            "name": v["name"], "rating": v["rating"], "played": v["played"],
            "byes": v["byes"], "wins": v["wins"], "draws": v["draws"],
            "losses": v["losses"], "points": points[pid], "buchholz": buch,
            "buchholz_cut1": buch_cut, "color_white": v["whites"],
            "color_black": v["blacks"],
            "key": (-points[pid], -buch, -buch_cut, -v["wins"], -v["rating"]),
            "correction_count": (correction_counts or {}).get(pid, 0),
        })

    rows.sort(key=lambda r: (r["key"], r["registration_no"]))

    p_by_id = {p.id: p for p in tournament.players}
    standings: list[Standing] = []
    i = 0
    while i < len(rows):
        j = i + 1
        while j < len(rows) and rows[j]["key"] == rows[i]["key"]:
            j += 1
        tied = [rows[k]["player_id"] for k in range(i, j)]
        rank = i + 1
        for k in range(i, j):
            r = rows[k]
            po = p_by_id[r["player_id"]]
            standings.append(Standing(
                player_id=r["player_id"], registration_no=r["registration_no"],
                name=r["name"], rating=r["rating"], played=r["played"],
                byes=r["byes"], wins=r["wins"], draws=r["draws"], losses=r["losses"],
                points=r["points"], buchholz=r["buchholz"],
                buchholz_cut1=r["buchholz_cut1"], color_white=r["color_white"],
                color_black=r["color_black"], rank=rank, tied_with=tied,
                correction_count=r["correction_count"],
                withdrawn=po.withdrawn_round_no is not None,
                withdrawn_round_no=po.withdrawn_round_no,
            ))
        i = j
    return standings


def state_for_pairing(tournament, rounds, games_by_round, excluded_ids=None):
    """聚合成 pairing.PlayerState（全部选手；excluded_ids 供重排求解器移出）。"""
    from .pairing import PlayerState
    st = aggregate(tournament, rounds, games_by_round)
    out = []
    for p in sorted(tournament.players, key=lambda x: x.registration_no):
        v = st[p.id]
        out.append(PlayerState(
            pid=p.id, name=v["name"], rating=v["rating"],
            registration_no=v["registration_no"], score2=v["score2"],
            whites=v["whites"], blacks=v["blacks"], byes=v["byes"],
            last_color=v["last_color"], opponents=v["opponents"],
        ))
    return out
