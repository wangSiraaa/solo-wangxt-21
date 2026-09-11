"""赛事规则的唯一事实来源。

每次发布轮次时，把当前 RULES_VERSION 与整套规则快照写入 rounds.rule_snapshot。
旧轮次永远按发布时的版本展示与审计，刷新页面或升级代码都不会改变已发布轮次。
"""

RULES_VERSION = "swiss-cp-v1.0.0"

RULES = {
    "version": RULES_VERSION,
    "name": "积分瑞士制（CP-SAT 配对）",
    "scoring": {
        "win": 1.0,
        "draw": 0.5,
        "loss": 0.0,
        "bye": 1.0,  # 轮空按胜局计分
    },
    # 硬规则：求解器必须全部满足；无解时绝不悄悄放松，只给出明确诊断
    "hard_constraints": [
        {"code": "ONE_GAME_PER_ROUND", "description": "每人每轮至多一次出场（参赛或轮空）"},
        {"code": "NO_REPEAT", "description": "任何两名选手不得在同一赛事中重复交手"},
        {"code": "BYE_COUNT", "description": "奇数人数时恰好 1 人轮空，偶数人数时 0 人轮空"},
    ],
    # 软约束：按字典序分层优化，被放宽的层会在报告中逐条点名
    "soft_constraints": [
        {
            "tier": "A",
            "code": "SCORE_PROXIMITY",
            "weight_per_half_point": 1,
            "description": "积分差最小化（按半子为单位计罚）",
        },
        {
            "tier": "B",
            "code": "BYE_FAIRNESS",
            "description": "轮空优先给当前积分最低、历史轮空次数最少的选手",
            "sub_weights": {"bye_lowest_score_group_per_half_point": 10, "bye_count": 3},
        },
        {
            "tier": "C",
            "code": "COLOR_BALANCE",
            "description": "先后手总次数差最小，其次避免连续同色",
            "sub_weights": {"imbalance": 4, "repeat_color": 1},
        },
    ],
    "tiebreaks": [
        {"order": 1, "code": "points", "description": "总积分（轮空计 1 分）"},
        {"order": 2, "code": "buchholz", "description": "对手分 Buchholz（轮空轮按 0 分对手处理，无对手可计）"},
        {"order": 3, "code": "buchholz_cut1", "description": "裁对手分：去掉最高对手分"},
        {"order": 4, "code": "wins", "description": "胜局数（轮空胜计入）"},
        {"order": 5, "code": "rating", "description": "等级分高者列前"},
        {"order": 6, "code": "registration_no", "description": "报名序号（仅用于并列者之间的展示排序，不打破并列）"},
    ],
    "ranking": "竞技比较键（积分、对手分、裁对手分、胜局、等级分）完全相同的选手并列同名次；报名序号只决定并列者的展示顺序",
    "determinism": {
        "workers": 1,
        "random_seed": 20260911,
        "note": "同一输入必然得到同一对阵，刷新、重算结果一致",
    },
}
