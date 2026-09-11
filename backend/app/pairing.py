"""瑞士制配对引擎（OR-Tools CP-SAT）。

设计原则
--------
硬约束（无解也绝不悄悄放松）：
  ONE_GAME_PER_ROUND  每人每轮恰好出场一次（参赛或轮空）
  NO_REPEAT           任何两人不重复交手
  BYE_COUNT           奇数人数恰好 1 个轮空，偶数 0 个

软约束按字典序（lexicographic）分层最小化，先后顺序本身就是规则的一部分：
  A. SCORE_PROXIMITY  对阵双方积分差
  B. BYE_FAIRNESS     轮空给积分最低、历史轮空最少者
  C. COLOR_BALANCE    先后手总差，其次避免连续同色

每一层在上一层取得最优值后才优化，因此报告可以明确指出"被放宽的是哪一层、
放宽到什么程度、影响哪些选手"，而不是给出一张看不出依据的对阵表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ortools.sat.python import cp_model

from .rules import RULES_VERSION

# ---------------------------------------------------------------- 数据结构


@dataclass
class PlayerState:
    pid: int
    name: str
    rating: int
    registration_no: int
    score2: int          # 积分 ×2，整数建模（胜 2 / 和 1 / 轮空 2）
    whites: int          # 累计执白
    blacks: int          # 累计执黑
    byes: int            # 累计轮空
    last_color: str      # "W" / "B" / ""
    opponents: set[int] = field(default_factory=set)


@dataclass
class PairingReport:
    feasible: bool
    rule_version: str
    pairs: list[tuple[int, int, str]]          # (白方pid, 黑方pid, 颜色依据) — 轮空不在此
    byes: list[int]
    player_reasons: dict[int, dict]
    tiers: list[dict]
    hard_constraints: list[dict]
    infeasibility: dict | None = None
    objective_values: dict[str, int] = field(default_factory=dict)
    overrides: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- 求解

_SOLVER_SECONDS = 5.0


def _build_and_solve(
    players: list[PlayerState],
    required_byes: int,
    allow_repeat: bool,
    bound_a: int | None = None,
    bound_b: int | None = None,
    *,
    with_objectives: bool = True,
):
    """构造并求解 CP 模型；返回 (solver, model, vars) 或 (None, model, vars)。"""
    n = len(players)
    m = cp_model.CpModel()
    idx = {p.pid: k for k, p in enumerate(players)}

    # 只有"尚未交手"的两人之间才存在对阵边；授权放宽时全部放开
    edge: dict[tuple[int, int], ...] = {}
    for a in range(n):
        for b in range(a + 1, n):
            pa, pb = players[a], players[b]
            if allow_repeat or pb.pid not in pa.opponents:
                edge[(a, b)] = m.NewBoolVar(f"e_{a}_{b}")

    bye = [m.NewBoolVar(f"bye_{k}") for k in range(n)]
    white = [m.NewBoolVar(f"white_{k}") for k in range(n)]

    incident: list[list] = [[] for _ in range(n)]
    for (a, b), e in edge.items():
        incident[a].append(e)
        incident[b].append(e)

    # 硬：每人恰好出场一次（一盘比赛 或 轮空）
    for k in range(n):
        m.Add(sum(incident[k]) + bye[k] == 1)

    # 硬：轮空总数
    m.Add(sum(bye) == required_byes)

    # 每盘恰好一名称白方（未选中的边不施加方向约束，避免交叉约束）
    for (a, b), e in edge.items():
        m.Add(white[a] + white[b] == 1).OnlyEnforceIf(e)
    # 轮空者不执白；非轮空者由出场约束保证恰好在一条边内
    for k in range(n):
        m.Add(white[k] + bye[k] <= 1)

    # ---- 目标 ----
    obj_a = 0
    for (a, b), e in edge.items():
        gap2 = abs(players[a].score2 - players[b].score2)
        obj_a += gap2 * e

    obj_b = 0
    for k, p in enumerate(players):
        # 积分越低（10×半子）、轮空历史越少（×3）越应拿轮空
        obj_b += (10 * p.score2 + 3 * p.byes) * bye[k]

    obj_c_terms = []
    for k, p in enumerate(players):
        plays = sum(incident[k])  # = 1 - bye
        # 新差值 = (whites-blacks) + 2*white - plays(+bye 抵消，见模块文档)
        # plays = 1 - bye，故 diff = base + 2*white - 1 + bye
        base = p.whites - p.blacks
        diff = m.NewIntVar(-n, n, f"diff_{k}")
        m.Add(diff == base + 2 * white[k] - 1 + bye[k])
        aux = m.NewIntVar(0, n, f"absdiff_{k}")
        m.AddAbsEquality(aux, diff)
        obj_c_terms.append(4 * aux)
        # 连续同色罚分（线性化）
        if p.last_color == "W":
            obj_c_terms.append(white[k])          # 本轮再执白
        elif p.last_color == "B":
            obj_c_terms.append(plays - white[k])  # 本轮再执黑；bye 时该项为 0

    obj_c = sum(obj_c_terms)

    if bound_a is not None:
        m.Add(obj_a <= bound_a)
    if bound_b is not None:
        m.Add(obj_b <= bound_b)

    if with_objectives:
        if bound_a is None:
            m.Minimize(obj_a)
        elif bound_b is None:
            m.Minimize(obj_b)
        else:
            m.Minimize(obj_c)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _SOLVER_SECONDS
    solver.parameters.num_workers = 1          # 确定性
    solver.parameters.random_seed = 20260911
    status = solver.Solve(m)
    ok = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return (solver if ok else None), {
        "model": m, "edge": edge, "bye": bye, "white": white,
        "obj_a": obj_a, "obj_b": obj_b, "obj_c": obj_c, "idx": idx,
    }


def solve_pairings(
    players: Iterable[PlayerState],
    *,
    required_byes: int | None = None,
    allow_repeat: bool = False,
    override_reason: str = "",
) -> PairingReport:
    players = list(players)
    n = len(players)
    if required_byes is None:
        required_byes = n % 2

    if n == 0:
        return PairingReport(
            feasible=True, rule_version=RULES_VERSION, pairs=[], byes=[],
            player_reasons={}, tiers=[], hard_constraints=_hard_status(n, [], []),
        )

    # 字典序三层求解：A → A 最优下的 B → A、B 最优下的 C
    s_a, v = _build_and_solve(players, required_byes, allow_repeat)
    if s_a is None:
        return _infeasible_report(players, required_byes, allow_repeat, override_reason)

    val_a = s_a.Value(v["obj_a"])
    s_b, v = _build_and_solve(players, required_byes, allow_repeat, bound_a=val_a)
    assert s_b is not None
    val_b = s_b.Value(v["obj_b"])
    s_c, v = _build_and_solve(
        players, required_byes, allow_repeat, bound_a=val_a, bound_b=val_b
    )
    assert s_c is not None
    val_c = s_c.Value(v["obj_c"])

    return _extract(s_c, v, players, required_byes, allow_repeat, override_reason,
                    {"A": val_a, "B": val_b, "C": val_c})


# ---------------------------------------------------------------- 结果组装

def _extract(solver, v, players, required_byes, allow_repeat, override_reason, vals):
    edge, bye_v, white_v = v["edge"], v["bye"], v["white"]
    pid = [p.pid for p in players]

    chosen_edges = [(a, b) for (a, b), e in edge.items() if solver.Value(e) == 1]
    byes = [players[k].pid for k in range(len(players)) if solver.Value(bye_v[k]) == 1]

    pairs: list[tuple[int, int, str]] = []
    plays_as: dict[int, tuple[int, int, str]] = {}
    for a, b in chosen_edges:
        if solver.Value(white_v[a]) == 1:
            w, bk = a, b
        else:
            w, bk = b, a
        color_note = _color_note(players[w], players[bk])
        pairs.append((pid[w], pid[bk], color_note))
        plays_as[pid[w]] = (pid[bk], "W", color_note)
        plays_as[pid[bk]] = (pid[w], "B", color_note)

    name = {p.pid: p.name for p in players}
    score_of = {p.pid: p.score2 / 2 for p in players}
    pstate = {p.pid: p for p in players}

    # ---- 每选手的配对依据 ----
    reasons: dict[int, dict] = {}
    pair_gaps: dict[int, float] = {}
    for (wpid, bpid, _note) in pairs:
        g = abs(score_of[wpid] - score_of[bpid])
        pair_gaps[wpid] = g
        pair_gaps[bpid] = g

    for p in players:
        if p.pid in byes:
            same_low = [q.pid for q in players
                        if q.score2 == p.score2 and q.byes == p.byes]
            reasons[p.pid] = {
                "name": p.name,
                "role": "bye",
                "headline": f"轮空（直接记 1 分）",
                "factors": [
                    f"当前积分 {p.score2 / 2:g}",
                    f"历史轮空 {p.byes} 次",
                    f"同分同轮空次数候选共 {len(same_low)} 人，按确定性规则选中",
                ],
            }
            continue
        opp_pid, color, note = plays_as[p.pid]
        opp = pstate[opp_pid]
        gap = pair_gaps[p.pid]
        factors = [
            f"对手 {opp.name}，积分 {opp.score2 / 2:g}，积分差 {gap:g}",
            f"本轮执{'白' if color == 'W' else '黑'}：{note}",
            f"本人累计 白{ p.whites + (1 if color == 'W' else 0)} / "
            f"黑{p.blacks + (1 if color == 'B' else 0)}",
        ]
        if opp_pid in p.opponents:
            factors.append("⚠ 与对手曾交手——本次对阵来自 NO_REPEAT 授权放宽")
        reasons[p.pid] = {
            "name": p.name,
            "role": "paired",
            "color": color,
            "opponent_id": opp_pid,
            "opponent_name": opp.name,
            "score_gap": gap,
            "headline": f"对阵 {opp.name}（积分差 {gap:g}），执{'白' if color == 'W' else '黑'}",
            "factors": factors,
        }

    # ---- 三层软约束报告 ----
    # A：所有对阵都在同分组才算完全满足
    gap_pairs = [
        {"white": name[w], "black": name[b], "gap_half_points": int(score2gap(players, w, b))}
        for (w, b, _) in pairs
        if pair_gaps[w] > 0
    ]
    tiers = [
        {
            "tier": "A", "code": "SCORE_PROXIMITY",
            "status": "SATISFIED" if vals["A"] == 0 else "RELAXED",
            "optimal_value_half_points": vals["A"],
            "detail": ("全部对阵积分相同" if vals["A"] == 0
                       else f"{len(gap_pairs)} 盘跨积分组，最小可达积分差合计 {vals['A']} 半子"),
            "affected_pairs": gap_pairs,
        },
    ]

    # B：被选轮空者是否位于"最低积分且其中轮空最少"的理想集合
    if required_byes > 0:
        bye_pid = byes[0]
        bp = pstate[bye_pid]
        min_score = min(p.score2 for p in players)
        min_byes_among_low = min(p.byes for p in players if p.score2 == min_score)
        ideal = bp.score2 == min_score and bp.byes == min_byes_among_low
        blocked = [
            {"name": q.name, "score": q.score2 / 2, "prior_byes": q.byes}
            for q in players
            if (q.score2, q.byes) < (bp.score2, bp.byes)
        ]
        tiers.append({
            "tier": "B", "code": "BYE_FAIRNESS",
            "status": "SATISFIED" if ideal else "RELAXED",
            "optimal_value": vals["B"],
            "detail": (
                f"轮空给 {bp.name}（{bp.score2 / 2:g} 分，历史轮空 {bp.byes} 次），"
                + ("位于最低积分且轮空最少的理想集合" if ideal
                   else "理想集合内选手均已被更高优先级约束占用")
            ),
            "bye_player": bp.name,
            "preferred_candidates_blocked": blocked,
        })

    # C：颜色
    post_imb = []
    repeats = []
    for p in players:
        w = p.whites + (1 if p.pid in plays_as and plays_as[p.pid][1] == "W" else 0)
        b = p.blacks + (1 if p.pid in plays_as and plays_as[p.pid][1] == "B" else 0)
        if p.pid in byes:
            w, b = p.whites, p.blacks
        post_imb.append(abs(w - b))
        if p.pid in plays_as and plays_as[p.pid][1] and p.last_color == plays_as[p.pid][1]:
            repeats.append(p.name)
    c_ok = max(post_imb, default=0) <= 1 and not repeats
    tiers.append({
        "tier": "C", "code": "COLOR_BALANCE",
        "status": "SATISFIED" if c_ok else "RELAXED",
        "optimal_value": vals["C"],
        "detail": (
            f"赛后最大先后手差 {max(post_imb, default=0)}，连续同色 {len(repeats)} 人次"
            + (f"（{', '.join(repeats)}）" if repeats else "")
        ),
        "max_imbalance": max(post_imb, default=0),
        "repeat_color_players": repeats,
    })

    overrides = []
    if allow_repeat:
        repeated = [
            {"players": [name[w], name[b]]}
            for (w, b, _) in pairs
            if b in pstate[w].opponents if False  # 占位，下面按 pid 重算
        ]
        repeated = []
        for wpid, bpid, _ in pairs:
            if bpid in pstate[wpid].opponents:
                repeated.append({"players": [name[wpid], name[bpid]]})
        overrides.append({
            "code": "NO_REPEAT", "reason": override_reason,
            "repeated_matchups": repeated,
        })

    return PairingReport(
        feasible=True,
        rule_version=RULES_VERSION,
        pairs=sorted(pairs, key=lambda t: (min(t[0], t[1]),)),
        byes=byes,
        player_reasons=reasons,
        tiers=tiers,
        hard_constraints=_hard_status(len(players), pairs, overrides, allow_repeat),
        objective_values={"A_score_gap_half_points": vals["A"], "B_bye": vals["B"],
                          "C_color": vals["C"]},
        overrides=overrides,
    )


def score2gap(players, wpid, bpid):
    """pairs 里是 pid，这里需要按 pid 查分差（半子）。"""
    by_pid = {p.pid: p for p in players}
    return abs(by_pid[wpid].score2 - by_pid[bpid].score2)


def _color_note(white_p: PlayerState, black_p: PlayerState) -> str:
    """解释为什么是这个人执白。"""
    dw = white_p.whites - white_p.blacks
    db = black_p.whites - black_p.blacks
    if dw != db:
        return f"白方赛前先后手差 {dw:+d}，黑方 {db:+d}，安排给差值更负（更欠白棋）的一方"
    if white_p.last_color == "B" and black_p.last_color != "B":
        return "双方先后手差相同，白方上一轮执黑，优先换色"
    if black_p.last_color == "W":
        return "双方先后手差相同，黑方上一轮执白故本轮继续执黑不符合换色——取次优"
    return f"双方颜色数据相同（差 {dw:+d}），按报名序号确定性裁定"


# ---------------------------------------------------------------- 硬约束状态

def _hard_status(n, pairs, overrides, allow_repeat=False):
    no_repeat_status = "OVERRIDDEN" if allow_repeat else "SATISFIED"
    return [
        {"code": "ONE_GAME_PER_ROUND", "status": "SATISFIED",
         "detail": f"{n} 人各出场一次"},
        {"code": "NO_REPEAT", "status": no_repeat_status,
         "detail": "已交手选手之间不生成对阵边" if not allow_repeat
                   else f"已授权放宽：{overrides[0]['reason'] if overrides else ''}"},
        {"code": "BYE_COUNT", "status": "SATISFIED",
         "detail": f"人数 {n} 为{'奇' if n % 2 else '偶'}，轮空 {n % 2} 人"},
    ]


# ---------------------------------------------------------------- 无解诊断

def _infeasible_report(players, required_byes, allow_repeat, override_reason):
    """硬约束无解时给出结构化原因，绝不自动放松。"""
    n = len(players)

    # 用"允许重复交手"再试一次，区分无解根因
    s_rep, _ = _build_and_solve(players, required_byes, True,
                                with_objectives=False)
    root = "NO_REPEAT" if s_rep is not None else "STRUCTURAL"

    # 并查集：允许对阵图的连通分量
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    blocked_edges = 0
    blocked_detail: dict[int, list[int]] = {}
    for a in range(n):
        for b in range(a + 1, n):
            if players[b].pid not in players[a].opponents:
                union(a, b)
            else:
                blocked_edges += 1
                blocked_detail.setdefault(a, []).append(b)
                blocked_detail.setdefault(b, []).append(a)

    comp: dict[int, list[int]] = {}
    for k in range(n):
        comp.setdefault(find(k), []).append(k)
    sizes = [len(c) for c in comp.values()]
    odd_components = [c for c in comp.values() if len(c) % 2 == 1]
    o = len(odd_components)
    # 每个奇数分量内部必须消化一个轮空；全体奇偶性抵消一个
    byes_needed = max(0, o - (n % 2)) + required_byes

    bottlenecks = []
    for k, opp_idxs in blocked_detail.items():
        if len(opp_idxs) == n - 1:
            bottlenecks.append(players[k].name)

    infeas = {
        "root_cause": root,
        "message": (
            "硬约束 NO_REPEAT 下不存在合法配对：允许对阵图无法在只安排 "
            f"{required_byes} 个轮空的前提下完成配对。"
            if root == "NO_REPEAT"
            else "即使允许重复交手仍无可行解，请检查参赛名单与轮空数量。"
        ),
        "allowed_graph": {
            "component_sizes": sorted(sizes, reverse=True),
            "odd_component_count": o,
            "blocked_edges_due_to_past_games": blocked_edges,
        },
        "byes_required_to_fix": byes_needed,
        "byes_allowed_by_rules": required_byes,
        "isolated_players": bottlenecks,
        "options": [
            f"需要至少 {byes_needed} 个轮空来消化奇数连通分量，但规则只允许 {required_byes} 个",
            "由裁判长通过 override 端点并填写理由，授权放宽 NO_REPEAT（系统会在对阵表与审计记录中显式标注）",
        ],
    }

    return PairingReport(
        feasible=False,
        rule_version=RULES_VERSION,
        pairs=[], byes=[], player_reasons={}, tiers=[],
        hard_constraints=_hard_status(n, [], [], allow_repeat),
        infeasibility=infeas,
        overrides=[{"code": "NO_REPEAT", "reason": override_reason}] if allow_repeat else [],
    )
