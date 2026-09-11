"""瑞士制配对引擎（OR-Tools CP-SAT）。

设计原则
--------
硬约束（无解也绝不悄悄放松）：
  ONE_GAME_PER_ROUND  每名在场选手每轮恰好出场一次（参赛或轮空）
  NO_REPEAT           任何两人不重复交手（含已开赛锁定的棋桌）
  BYE_COUNT           在场奇数人数恰好 1 个轮空，偶数 0 个
  LOCKED_BOARDS       受控重排时，已开赛棋桌保持不动（选手、颜色、结果）

软约束按字典序（lexicographic）分层最小化，先后顺序本身就是规则的一部分：
  A. SCORE_PROXIMITY  对阵双方积分差
  B. BYE_FAIRNESS     轮空给积分最低、历史轮空最少者
  C. COLOR_BALANCE    先后手总差，其次避免连续同色（锁定棋桌同样计入评估）

每一层在上一层取得最优值后才优化，因此报告可以明确指出"被放宽的是哪一层、
放宽到什么程度、影响哪些选手"，而不是给出一张看不出依据的对阵表。

solve_repair 支持受控重配对：locked_pairs 中的棋桌原样固定，excluded_ids
（退赛者）不进入求解池，只对其余空闲选手重新求解。
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
    pairs: list[tuple[int, int, str]]          # (白方pid, 黑方pid, 颜色依据)
    byes: list[int]
    player_reasons: dict[int, dict]
    tiers: list[dict]
    hard_constraints: list[dict]
    locked_pairs: list[tuple[int, int]] = field(default_factory=list)
    excluded: list[int] = field(default_factory=list)
    infeasibility: dict | None = None
    objective_values: dict[str, int] = field(default_factory=dict)
    overrides: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- 求解

_SOLVER_SECONDS = 5.0


def _constant_color_cost(locked_pairs, all_present):
    """锁定棋桌选手对 C 层目标的固定贡献（先后手差 + 连续同色）。"""
    locked_color = {}
    for w, b in locked_pairs:
        locked_color[w] = "W"
        locked_color[b] = "B"
    cost = 0
    for p in all_present:
        w_new = p.whites + (1 if locked_color.get(p.pid) == "W" else 0)
        b_new = p.blacks + (1 if locked_color.get(p.pid) == "B" else 0)
        cost += 4 * abs(w_new - b_new)
        if p.last_color and locked_color.get(p.pid) == p.last_color:
            cost += 1
    return cost, locked_color


def _build_and_solve(
    players: list[PlayerState],
    required_byes: int,
    allow_repeat: bool,
    locked_pairs: list[tuple[int, int]] | None = None,
    excluded: set[int] | None = None,
    bound_a: int | None = None,
    bound_b: int | None = None,
    *,
    with_objectives: bool = True,
):
    """构造并求解 CP 模型。返回 (solver_or_None, vars)。"""
    locked_pairs = locked_pairs or []
    excluded = excluded or set()

    locked_white = {w for w, _ in locked_pairs}
    locked_black = {b for _, b in locked_pairs}
    locked_all = locked_white | locked_black

    # 求解池：在场且未锁定的空闲选手（退赛者不进入）
    free = [p for p in players if p.pid not in locked_all and p.pid not in excluded]
    n = len(free)
    m = cp_model.CpModel()

    edge: dict[tuple[int, int], object] = {}
    for a in range(n):
        for b in range(a + 1, n):
            pa, pb = free[a], free[b]
            # NO_REPEAT：历史对手（含锁定棋桌这一轮的交手）不连边
            if allow_repeat or pb.pid not in pa.opponents:
                edge[(a, b)] = m.NewBoolVar(f"e_{a}_{b}")

    bye = [m.NewBoolVar(f"bye_{k}") for k in range(n)]
    white = [m.NewBoolVar(f"white_{k}") for k in range(n)]

    incident: list[list] = [[] for _ in range(n)]
    for (a, b), e in edge.items():
        incident[a].append(e)
        incident[b].append(e)

    # 硬：每名空闲选手恰好出场一次（一盘比赛 或 轮空）
    for k in range(n):
        m.Add(sum(incident[k]) + bye[k] == 1)
    m.Add(sum(bye) == required_byes)

    # 每盘恰好一名称白方（未选中的边不施加方向约束，避免交叉约束）
    for (a, b), e in edge.items():
        m.Add(white[a] + white[b] == 1).OnlyEnforceIf(e)
    for k in range(n):
        m.Add(white[k] + bye[k] <= 1)

    # ---- A：积分差（仅自由新盘） ----
    obj_a = 0
    for (a, b), e in edge.items():
        obj_a += abs(free[a].score2 - free[b].score2) * e

    # ---- B：轮空公平 ----
    obj_b = 0
    for k, p in enumerate(free):
        obj_b += (10 * p.score2 + 3 * p.byes) * bye[k]

    # ---- C：颜色（自由新盘的变量部分 + 锁定棋桌的固定部分） ----
    obj_c_terms = []
    for k, p in enumerate(free):
        base = p.whites - p.blacks
        diff = m.NewIntVar(-n, n, f"diff_{k}")
        m.Add(diff == base + 2 * white[k] - 1 + bye[k])
        aux = m.NewIntVar(0, n, f"absdiff_{k}")
        m.AddAbsEquality(aux, diff)
        obj_c_terms.append(4 * aux)
        if p.last_color == "W":
            obj_c_terms.append(white[k])
        elif p.last_color == "B":
            obj_c_terms.append(sum(incident[k]) - white[k])
    locked_cost, _ = _constant_color_cost(locked_pairs, players)
    obj_c = sum(obj_c_terms) + locked_cost

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
    v = {"edge": edge, "bye": bye, "white": white, "free": free,
         "obj_a": obj_a, "obj_b": obj_b, "obj_c": obj_c}
    return (solver if ok else None), v


def solve_pairings(players: Iterable[PlayerState], *, required_byes: int | None = None,
                   allow_repeat: bool = False, override_reason: str = "") -> PairingReport:
    """普通发布：无锁定棋桌、无退赛者。"""
    return solve_repair(list(players), locked_pairs=[], excluded_ids=set(),
                        required_byes=required_byes, allow_repeat=allow_repeat,
                        override_reason=override_reason)


def solve_repair(
    players: Iterable[PlayerState],
    *,
    locked_pairs: list[tuple[int, int]],
    excluded_ids: set[int],
    required_byes: int | None = None,
    allow_repeat: bool = False,
    override_reason: str = "",
) -> PairingReport:
    """受控重配对：锁定棋桌不动，退赛者移出，空闲选手重新求解。"""
    players = list(players)
    locked_pairs = list(locked_pairs)
    excluded = set(excluded_ids)

    by_id = {p.pid: p for p in players}
    # 数据完整性校验：锁定/排除的选手必须存在，退赛者不能出现在锁定桌
    for w, b in locked_pairs:
        if w not in by_id or b not in by_id:
            raise ValueError("锁定棋桌包含不存在的选手")
        if w in excluded or b in excluded:
            raise ValueError("退赛选手不能出现在已开赛锁定棋桌中")

    present = [p for p in players if p.pid not in excluded]
    locked_all = {x for pair in locked_pairs for x in pair}
    free = [p for p in present if p.pid not in locked_all]
    if required_byes is None:
        required_byes = len(present) % 2

    if not free:
        # 全员锁定/退赛：没有需要求解的部分
        return _extract(None, {"edge": {}, "bye": [], "white": [], "free": [],
                               "obj_a": 0, "obj_b": 0, "obj_c":
                                   _constant_color_cost(locked_pairs, present)[0]},
                        present, locked_pairs, excluded, required_byes,
                        allow_repeat, override_reason,
                        {"A": 0, "B": 0, "C": _constant_color_cost(locked_pairs, present)[0]},
                        all_locked=True, all_players=players)

    s_a, v = _build_and_solve(present, required_byes, allow_repeat,
                              locked_pairs, excluded)
    if s_a is None:
        return _infeasible_report(present, locked_pairs, excluded,
                                  required_byes, allow_repeat, override_reason)

    val_a = s_a.Value(v["obj_a"])
    s_b, v = _build_and_solve(present, required_byes, allow_repeat,
                              locked_pairs, excluded, bound_a=val_a)
    assert s_b is not None
    val_b = s_b.Value(v["obj_b"])
    s_c, v = _build_and_solve(present, required_byes, allow_repeat,
                              locked_pairs, excluded, bound_a=val_a, bound_b=val_b)
    assert s_c is not None
    val_c = s_c.Value(v["obj_c"])

    return _extract(s_c, v, present, locked_pairs, excluded, required_byes,
                    allow_repeat, override_reason,
                    {"A": val_a, "B": val_b, "C": val_c},
                    all_players=players)


# ---------------------------------------------------------------- 结果组装

def _extract(solver, v, present, locked_pairs, excluded, required_byes,
             allow_repeat, override_reason, vals, *, all_locked=False,
             all_players=None):
    free: list[PlayerState] = v["free"]
    pid = [p.pid for p in free]
    pstate = {p.pid: p for p in present}
    name = {p.pid: p.name for p in present}
    score_of = {p.pid: p.score2 / 2 for p in present}

    locked_white = {w for w, _ in locked_pairs}
    locked_black = {b for _, b in locked_pairs}
    locked_all = locked_white | locked_black

    chosen_edges, byes = [], []
    if not all_locked:
        edge, bye_v, white_v = v["edge"], v["bye"], v["white"]
        chosen_edges = [(a, b) for (a, b), e in edge.items() if solver.Value(e) == 1]
        byes = [free[k].pid for k in range(len(free)) if solver.Value(bye_v[k]) == 1]
    else:
        edge, bye_v, white_v = {}, [], []

    pairs: list[tuple[int, int, str]] = []
    plays_as: dict[int, tuple[int, str, str]] = {}

    # 新求解出的棋桌
    for a, b in chosen_edges:
        w, bk = (a, b) if solver.Value(white_v[a]) == 1 else (b, a)
        note = _color_note(free[w], free[bk])
        pairs.append((pid[w], pid[bk], note))
        plays_as[pid[w]] = (pid[bk], "W", note)
        plays_as[pid[bk]] = (pid[w], "B", note)

    # 锁定棋桌（颜色固定）
    for wpid, bpid in locked_pairs:
        pw, pb = pstate[wpid], pstate[bpid]
        note = f"已开赛棋桌锁定：维持原台次安排（{pw.name} 执白 / {pb.name} 执黑），不参与重排"
        pairs.append((wpid, bpid, note))
        plays_as[wpid] = (bpid, "W", note)
        plays_as[bpid] = (wpid, "B", note)

    pair_gaps = {}
    for (wpid, bpid, _) in pairs:
        pair_gaps[wpid] = pair_gaps[bpid] = abs(score_of[wpid] - score_of[bpid])

    # ---- 每选手依据 ----
    reasons: dict[int, dict] = {}
    for p in present:
        if p.pid in byes:
            reasons[p.pid] = {
                "name": p.name, "role": "bye",
                "headline": "轮空（直接记 1 分）",
                "factors": [
                    f"当前积分 {p.score2 / 2:g}",
                    f"历史轮空 {p.byes} 次",
                    "在可重排选手中按 B 层轮空公平规则选中",
                ],
            }
            continue
        opp_pid, color, note = plays_as[p.pid]
        opp = pstate[opp_pid]
        gap = pair_gaps[p.pid]
        locked = p.pid in locked_all
        factors = [
            f"对手 {opp.name}，积分 {opp.score2 / 2:g}，积分差 {gap:g}",
            f"本轮执{'白' if color == 'W' else '黑'}：{note}",
            f"本人赛后累计 白{p.whites + (1 if color == 'W' else 0)} / "
            f"黑{p.blacks + (1 if color == 'B' else 0)}",
        ]
        if locked:
            factors.insert(0, "🔒 棋桌已开赛，受控重排中保持不动")
        if opp_pid in p.opponents:
            factors.append("⚠ 与对手曾交手——本次对阵来自 NO_REPEAT 授权放宽")
        reasons[p.pid] = {
            "name": p.name,
            "role": "locked" if locked else "paired",
            "color": color, "opponent_id": opp_pid, "opponent_name": opp.name,
            "score_gap": gap,
            "headline": ("🔒 棋桌锁定：" if locked else "对阵 ")
                        + f"{opp.name}（积分差 {gap:g}），执{'白' if color == 'W' else '黑'}",
            "factors": factors,
        }
    # 退赛者：不在对阵中，但依据里显式说明
    all_by_id = {p.pid: p for p in (all_players or present)}
    for xid in sorted(excluded):
        xp = all_by_id.get(xid)
        if xp is None:
            continue
        reasons[xp.pid] = {
            "name": xp.name, "role": "withdrawn",
            "headline": "已退赛：本轮移出配对池，不安排棋桌",
            "factors": [
                f"当前积分 {xp.score2 / 2:g}（既有成绩保留）",
                "后续轮次不再配对；已取得的积分与对手小分按锁定规则继续计入排名",
            ],
        }

    return _finalize(report=None, present=present, pairs=pairs, byes=byes,
                     reasons=reasons, locked_pairs=locked_pairs, excluded=excluded,
                     vals=vals, allow_repeat=allow_repeat,
                     override_reason=override_reason, plays_as=plays_as,
                     required_byes=required_byes)


def _finalize(*, report, present, pairs, byes, reasons, locked_pairs, excluded,
              vals, allow_repeat, override_reason, plays_as, required_byes):
    pstate = {p.pid: p for p in present}
    name = {p.pid: p.name for p in present}

    # 退赛者依据（在 reasons 里单独记录，但他们不在对阵中）
    # excluded 选手不在 present 中——需要调用方补充，见下方 solve_repair 的包装处理
    # ---- A 层报告（只统计新求解的棋桌：锁定桌不是配对选择） ----
    free_pairs = [(w, b) for (w, b, _) in pairs if w not in {x for pa in locked_pairs for x in pa}]
    gap_pairs = [
        {"white": name[w], "black": name[b],
         "gap_half_points": int(abs(pstate[w].score2 - pstate[b].score2))}
        for (w, b) in free_pairs if pstate[w].score2 != pstate[b].score2
    ]
    tiers = [{
        "tier": "A", "code": "SCORE_PROXIMITY",
        "status": "SATISFIED" if vals["A"] == 0 else "RELAXED",
        "optimal_value_half_points": vals["A"],
        "detail": ("全部对阵积分相同" if vals["A"] == 0
                   else f"{len(gap_pairs)} 张{'重排' if locked_pairs else ''}棋桌跨积分组，"
                        f"最小可达积分差合计 {vals['A']} 半子"),
        "affected_pairs": gap_pairs,
    }]

    if required_byes > 0 and byes:
        bye_pid = byes[0]
        bp = pstate[bye_pid]
        eligible = [p for p in present if p.pid not in
                    {x for pa in locked_pairs for x in pa}]
        min_score = min(p.score2 for p in eligible)
        min_byes_among_low = min(p.byes for p in eligible if p.score2 == min_score)
        ideal = bp.score2 == min_score and bp.byes == min_byes_among_low
        tiers.append({
            "tier": "B", "code": "BYE_FAIRNESS",
            "status": "SATISFIED" if ideal else "RELAXED",
            "optimal_value": vals["B"],
            "detail": (
                f"轮空给 {bp.name}（{bp.score2 / 2:g} 分，历史轮空 {bp.byes} 次），"
                + ("位于最低积分且轮空最少的理想集合" if ideal
                   else "理想集合内选手或已被锁定棋桌占用或已退赛")
            ),
            "bye_player": bp.name,
        })

    post_imb, repeats = [], []
    for p in present:
        w = p.whites + (1 if p.pid in plays_as and plays_as[p.pid][1] == "W" else 0)
        b = p.blacks + (1 if p.pid in plays_as and plays_as[p.pid][1] == "B" else 0)
        post_imb.append(abs(w - b))
        if p.pid in plays_as and p.last_color == plays_as[p.pid][1] and p.pid not in {x for pa in locked_pairs for x in pa}:
            repeats.append(p.name)
    c_ok = max(post_imb, default=0) <= 1 and not repeats
    tiers.append({
        "tier": "C", "code": "COLOR_BALANCE",
        "status": "SATISFIED" if c_ok else "RELAXED",
        "optimal_value": vals["C"],
        "detail": (f"在场选手赛后最大先后手差 {max(post_imb, default=0)}，"
                   f"连续同色 {len(repeats)} 人次"
                   + (f"（{', '.join(repeats)}）" if repeats else "")),
        "max_imbalance": max(post_imb, default=0),
        "repeat_color_players": repeats,
    })

    overrides = []
    if allow_repeat:
        repeated = [{"players": [name[w], name[b]]}
                    for (w, b, _) in pairs if b in pstate[w].opponents]
        overrides.append({"code": "NO_REPEAT", "reason": override_reason,
                          "repeated_matchups": repeated})

    return PairingReport(
        feasible=True, rule_version=RULES_VERSION,
        pairs=sorted(pairs, key=lambda t: (min(t[0], t[1]),)),
        byes=byes, player_reasons=reasons, tiers=tiers,
        hard_constraints=_hard_status(len(present), len(excluded), pairs,
                                      locked_pairs, overrides, allow_repeat),
        locked_pairs=locked_pairs, excluded=sorted(excluded),
        objective_values={"A_score_gap_half_points": vals["A"], "B_bye": vals["B"],
                          "C_color": vals["C"]},
        overrides=overrides,
    )


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

def _hard_status(n_present, n_excluded, pairs, locked_pairs, overrides, allow_repeat=False):
    no_repeat_status = "OVERRIDDEN" if allow_repeat else "SATISFIED"
    return [
        {"code": "ONE_GAME_PER_ROUND", "status": "SATISFIED",
         "detail": f"{n_present} 名在场选手各出场一次（退赛 {n_excluded} 人移出）"},
        {"code": "NO_REPEAT", "status": no_repeat_status,
         "detail": "已交手选手之间不生成对阵边" if not allow_repeat
                   else f"已授权放宽：{overrides[0]['reason'] if overrides else ''}"},
        {"code": "BYE_COUNT", "status": "SATISFIED",
         "detail": f"在场 {n_present} 人为{'奇' if n_present % 2 else '偶'}，轮空 {n_present % 2} 人"},
        {"code": "LOCKED_BOARDS",
         "status": "SATISFIED" if locked_pairs else "NOT_APPLICABLE",
         "detail": (f"{len(locked_pairs)} 张已开赛棋桌维持原选手与颜色"
                    if locked_pairs else "本轮无锁定棋桌（全新发布）")},
    ]


# ---------------------------------------------------------------- 无解诊断

def _infeasible_report(present, locked_pairs, excluded, required_byes,
                       allow_repeat, override_reason):
    """硬约束无解时给出结构化原因，绝不自动放松。诊断对象为空闲选手子图。"""
    locked_all = {x for pair in locked_pairs for x in pair}
    free = [p for p in present if p.pid not in locked_all]
    n = len(free)

    s_rep, _ = _build_and_solve(present, required_byes, True,
                                locked_pairs, excluded, with_objectives=False)
    root = "NO_REPEAT" if s_rep is not None else "STRUCTURAL"

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
            if free[b].pid not in free[a].opponents:
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
    byes_needed = max(0, o - (n % 2)) + required_byes

    bottlenecks = [free[k].name for k, opp_idxs in blocked_detail.items()
                   if len(opp_idxs) == n - 1]

    infeas = {
        "root_cause": root,
        "message": (
            f"硬约束下不存在合法重排：{len(locked_pairs)} 张已开赛棋桌锁定、"
            f"{len(excluded)} 人退赛后，空闲选手允许对阵图无法在只安排 "
            f"{required_byes} 个轮空的前提下完成配对。"
            if root == "NO_REPEAT"
            else "即使允许重复交手仍无可行解，请检查锁定棋桌、退赛名单与轮空数量。"
        ),
        "free_players": n,
        "locked_boards": len(locked_pairs),
        "withdrawn": len(excluded),
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
            "由裁判长通过授权 override 并填写理由放宽 NO_REPEAT（全程显式标注）",
        ],
    }

    return PairingReport(
        feasible=False, rule_version=RULES_VERSION, pairs=[], byes=[],
        player_reasons={}, tiers=[],
        hard_constraints=_hard_status(len(present), len(excluded), [],
                                      locked_pairs, [], allow_repeat),
        locked_pairs=locked_pairs, excluded=sorted(excluded),
        infeasibility=infeas,
        overrides=[{"code": "NO_REPEAT", "reason": override_reason}] if allow_repeat else [],
    )
