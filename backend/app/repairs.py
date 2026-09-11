"""受控重配对服务：退赛、开赛标记、方案对比、确认与复检。

与成绩链路严格分开：
  - 成绩改判 → result_corrections（game.verdict 永不动，current_result 驱动小分）
  - 对阵变更 → pairing_revisions（rounds.pairing_snapshot 永不覆盖）
    原棋桌置 cancelled（行保留），新棋桌插入并关联 revision。
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .pairing import solve_repair
from .rules import RULES, RULES_VERSION


# ---------------------------------------------------------------- 退赛

def withdraw_player(db: Session, tournament_id: int, player_id: int,
                    reason: str) -> models.Player:
    t = db.get(models.Tournament, tournament_id)
    if t is None:
        raise HTTPException(404, "赛事不存在")
    p = db.get(models.Player, player_id)
    if p is None or p.tournament_id != tournament_id:
        raise HTTPException(404, "选手不存在")
    if len(reason.strip()) < 4:
        raise HTTPException(422, "退赛理由不少于 4 个字")

    rounds = _rounds(db, t)
    if not rounds:
        raise HTTPException(409, "尚未发布任何轮次，直接移除报名即可（无成绩需保留）")
    if p.withdrawn_round_no is not None:
        raise HTTPException(409, f"{p.name} 已在第 {p.withdrawn_round_no} 轮退赛（幂等，不重复处理）")

    latest = rounds[-1]
    # 已在最新轮开赛的棋桌上 → 不能从本轮移出
    started = list(db.scalars(select(models.Game).where(
        models.Game.round_id == latest.id,
        models.Game.status == "active",
        models.Game.started_at.is_not(None),
    )))
    for g in started:
        if g.white_id == player_id or g.black_id == player_id:
            raise HTTPException(
                409, f"{p.name} 在第 {latest.round_no} 轮的棋桌已开赛，"
                     "不能退赛移出该轮；待该棋桌录入结果后退赛于下一轮生效"
            )

    from datetime import datetime, timezone as tz
    p.withdrawn_round_no = latest.round_no
    p.withdrawn_reason = reason.strip()
    p.withdrawn_at = datetime.now(tz.utc)
    # 退赛者在最新轮若有未开赛 active 棋桌，立即取消
    pending = list(db.scalars(select(models.Game).where(
        models.Game.round_id == latest.id,
        models.Game.status == "active",
        models.Game.started_at.is_(None),
    )))
    cancelled = []
    for g in pending:
        if g.white_id == player_id or g.black_id == player_id:
            g.status = "cancelled"
            g.cancelled_reason = f"选手 {p.name} 退赛（{reason.strip()}）"
            cancelled.append(g.id)
    db.commit()
    db.refresh(p)
    return p, cancelled


# ---------------------------------------------------------------- 开赛标记

def mark_started(db: Session, game_id: int) -> models.Game:
    g = db.get(models.Game, game_id)
    if g is None:
        raise HTTPException(404, "对局不存在")
    if g.status != "active":
        raise HTTPException(409, "该棋桌已取消，不能开赛")
    if g.started_at is None:
        from datetime import datetime, timezone as tz
        g.started_at = datetime.now(tz.utc)
        db.commit()
        db.refresh(g)
    # 已开赛再标记：幂等，不产生新状态
    return g


# ---------------------------------------------------------------- 局面快照与指纹

def _rounds(db: Session, t: models.Tournament):
    return list(db.scalars(
        select(models.Round).where(models.Round.tournament_id == t.id)
        .order_by(models.Round.round_no)
    ))


def _active_games(db: Session, round_id: int):
    return list(db.scalars(
        select(models.Game)
        .where(models.Game.round_id == round_id, models.Game.status == "active")
        .order_by(models.Game.board_no, models.Game.id)
    ))


def _fingerprint(active_games, withdrawn_ids):
    """局面指纹：棋桌 id + 开赛状态 + 退赛集合。确认时用它检测方案过期。"""
    payload = {
        "games": sorted(
            (g.id, g.is_bye, g.white_id, g.black_id,
             g.started_at.astimezone(timezone.utc).isoformat() if g.started_at else None)
            for g in active_games
        ),
        "withdrawn": sorted(withdrawn_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _withdrawn_before(db: Session, t: models.Tournament, round_no: int) -> set[int]:
    return {p.id for p in t.players
            if p.withdrawn_round_no is not None and p.withdrawn_round_no <= round_no}


def _locked_and_free(db: Session, rd: models.Round):
    games = _active_games(db, rd.id)
    locked_pairs, free_games, bye_games = [], [], []
    for g in games:
        if g.is_bye:
            bye_games.append(g)
        elif g.started_at is not None or g.verdict:
            # 已开赛或已录入裁定的棋桌锁定
            locked_pairs.append((g.white_id, g.black_id))
        else:
            free_games.append(g)
    return locked_pairs, free_games, bye_games, games


# ---------------------------------------------------------------- 方案生成

def _arrangement_cost(states, pairs, byes, locked_pairs):
    """按发布规则的同一套权重评估任意安排的全轮约束代价（用于公平比较）。

    states：全部在场选手；没有出现在任何棋桌/轮空中的人计"无桌罚分"，
    否则退赛后"维持原表"会因为漏算无桌选手而显得代价更低。
    """
    by_id = {s.pid: s for s in states}
    unpaired_penalty = 1000
    a = b = c = 0
    for wpid, bpid in pairs:
        a += abs(by_id[wpid].score2 - by_id[bpid].score2)
    for pid in byes:
        s = by_id[pid]
        b += 10 * s.score2 + 3 * s.byes

    seated = {x for pair in pairs for x in pair} | set(byes)
    color_of = {}
    for wpid, bpid in pairs:
        color_of[wpid], color_of[bpid] = "W", "B"
    unpaired_ids = []
    for s in states:
        if s.pid not in seated:
            c += unpaired_penalty
            unpaired_ids.append(s.pid)
        elif s.pid in color_of:
            w = s.whites + (color_of[s.pid] == "W")
            bl = s.blacks + (color_of[s.pid] == "B")
            c += 4 * abs(w - bl)
            if s.last_color and color_of[s.pid] == s.last_color:
                c += 1
    return {"A_score_gap_half_points": a, "B_bye": b, "C_color": c,
            "unpaired_players": unpaired_ids,
            "unpaired_count": len(unpaired_ids)}


def _report_json(report):
    return {
        "feasible": report.feasible,
        "rule_version": report.rule_version,
        "pairs": [{"white_id": w, "black_id": b, "color_reason": why,
                   "locked": w in {x for pa in report.locked_pairs for x in pa}}
                  for (w, b, why) in report.pairs],
        "byes": report.byes,
        "locked_pairs": [list(x) for x in report.locked_pairs],
        "excluded": report.excluded,
        "player_reasons": {str(k): v for k, v in report.player_reasons.items()},
        "tiers": report.tiers,
        "hard_constraints": report.hard_constraints,
        "objective_values": report.objective_values,
        "overrides": report.overrides,
        "infeasibility": report.infeasibility,
    }


def create_repair_plan(db: Session, tournament_id: int, *,
                       allow_repeat: bool = False, reason: str = "") -> dict:
    t = db.get(models.Tournament, tournament_id)
    if t is None:
        raise HTTPException(404, "赛事不存在")
    rounds = _rounds(db, t)
    if not rounds:
        raise HTTPException(409, "尚未发布轮次，无重排对象")
    rd = rounds[-1]

    locked_pairs, free_games, bye_games, active_games = _locked_and_free(db, rd)
    withdrawn_ids = _withdrawn_before(db, t, rd.round_no)

    # 求解所需的选手状态（含历史），退赛者传入 excluded
    from .standings import state_for_pairing
    gbr = {r.id: _active_games(db, r.id) for r in rounds}
    states = state_for_pairing(t, rounds, gbr)

    # 原始（当前 active）安排 —— 维持原表基线
    baseline_pairs = [(g.white_id, g.black_id) for g in active_games if not g.is_bye]
    baseline_byes = [g.white_id for g in bye_games]
    present_states = [s for s in states if s.pid not in withdrawn_ids]
    baseline_cost = _arrangement_cost(present_states, baseline_pairs,
                                      baseline_byes, locked_pairs)
    baseline_viable = (
        all(w not in withdrawn_ids and b not in withdrawn_ids
            for w, b in baseline_pairs)
        and baseline_cost["unpaired_count"] == 0
    )

    # 局部重排求解
    report = solve_repair(
        states, locked_pairs=locked_pairs, excluded_ids=withdrawn_ids,
        allow_repeat=allow_repeat, override_reason=reason,
    )

    new_pairs = [(w, b) for (w, b, _) in report.pairs] if report.feasible else []
    new_cost = None
    if report.feasible:
        new_cost = dict(report.objective_values)
        new_cost["unpaired_players"] = []
        new_cost["unpaired_count"] = 0

    # 桌次差异（按无序对局集合比对；锁定桌必然不变）
    def pairset(pairs):
        return {tuple(sorted(x)) for x in pairs}

    base_set, new_set = pairset(baseline_pairs), pairset(new_pairs)
    dissolved = sorted(base_set - new_set)
    created = sorted(new_set - base_set)
    kept = sorted(base_set & new_set)
    base_byes = set(baseline_byes)
    new_byes = set(report.byes if report.feasible else [])
    byes_removed = sorted(base_byes - new_byes)
    byes_added = sorted(new_byes - base_byes)
    # 改动桌数：被撤销/新开的对局桌（去重计数）+ 轮空归属变化
    pair_boards_changed = len(dissolved)  # dissolved 与 created 在重排中一一对应
    bye_changes = len(byes_removed) + len(byes_added)

    diff = {
        "changed_boards": (pair_boards_changed + bye_changes) if report.feasible else None,
        "pair_boards_changed": pair_boards_changed,
        "bye_changes": bye_changes,
        "byes_removed": byes_removed,
        "byes_added": byes_added,
        "dissolved": dissolved,
        "created": created,
        "kept": kept,
        "locked_boards": len(locked_pairs),
        "withdrawn_ids": sorted(withdrawn_ids),
        "baseline_cost": baseline_cost,
        "proposed_cost": new_cost,
        "baseline_viable": baseline_viable,
        "byes_before": baseline_byes,
        "byes_after": report.byes if report.feasible else [],
    }

    fingerprint = _fingerprint(active_games, withdrawn_ids)

    # 过期此前 draft，仅保留最新一个
    for old in db.scalars(select(models.PairingRevision).where(
        models.PairingRevision.round_id == rd.id,
        models.PairingRevision.status == "draft",
    )):
        old.status = "expired"
        old.expiry_note = "生成了更新的重排方案"

    revision = models.PairingRevision(
        round_id=rd.id, status="draft",
        reason=reason or "受控重排（申诉改判 / 退赛）",
        situation_fingerprint=fingerprint, diff_summary=diff,
        baseline_snapshot={
            "pairs": [{"white_id": w, "black_id": b} for w, b in baseline_pairs],
            "byes": baseline_byes, "cost": baseline_cost,
            "viable": baseline_viable,
        },
        proposed_snapshot=_report_json(report),
    )
    db.add(revision)
    db.commit()
    db.refresh(revision)

    return {
        "revision_id": revision.id, "round_no": rd.round_no,
        "rule_version": RULES_VERSION, "fingerprint": fingerprint,
        "feasible": report.feasible,
        "report": _report_json(report),
        "diff": diff,
        "note": ("方案未落子；确认前会再次复检棋桌开赛状态"
                 if report.feasible else "无合法重排：硬规则未放松，确认将被阻止"),
    }


# ---------------------------------------------------------------- 确认

def confirm_repair(db: Session, revision_id: int) -> dict:
    rev = db.get(models.PairingRevision, revision_id)
    if rev is None:
        raise HTTPException(404, "重排方案不存在")
    rd = db.get(models.Round, rev.round_id)
    t = db.get(models.Tournament, rd.tournament_id)
    rounds = _rounds(db, t)

    if rev.status == "applied":
        # 幂等：重复提交同一裁定只形成一次有效变更
        return {"id": rev.id, "status": "already_applied",
                "message": "该方案已生效，重复确认不再产生变更"}
    if rev.status != "draft":
        raise HTTPException(409, f"方案已 {rev.status}，不能确认")

    active_games = _active_games(db, rd.id)
    withdrawn_ids = _withdrawn_before(db, t, rd.round_no)
    current_fp = _fingerprint(active_games, withdrawn_ids)
    if current_fp != rev.situation_fingerprint:
        rev.status = "expired"
        rev.expiry_note = "确认时局面已变化（有棋桌新开赛或名单变动），需重新生成方案"
        db.commit()
        raise HTTPException(
            409,
            {"message": "重排方案已过期：方案生成后又有棋桌开赛（或名单变化），"
                        "已阻止确认。请重新生成方案，新开赛棋桌将自动锁定。",
             "expected_fingerprint": rev.situation_fingerprint,
             "current_fingerprint": current_fp},
        )

    report = rev.proposed_snapshot
    if not report["feasible"]:
        raise HTTPException(422, "无合法重排，不能确认")

    locked_pairs, free_games, bye_games, _ = _locked_and_free(db, rd)
    # 双保险：即将取消的棋桌必须仍未开赛
    for g in free_games + bye_games:
        if g.started_at is not None or g.verdict:
            db.rollback()
            raise HTTPException(409, "棋桌在复检时已开赛/录入，方案过期，请重新生成")

    from datetime import datetime, timezone as tz
    # 1) 取消所有未开赛棋桌与轮空（行保留，关联 revision 便于回看）
    for g in free_games + bye_games:
        g.status = "cancelled"
        g.revision_id = rev.id
        g.cancelled_reason = f"受控重排方案 #{rev.id} 撤销（{rev.reason}）"

    # 2) 按方案插入新棋桌（锁定桌原样保留，不在此插入）
    locked_set = {x for pair in locked_pairs for x in pair}
    board_no = 100
    for item in report["pairs"]:
        if item["white_id"] in locked_set:
            continue
        db.add(models.Game(
            round_id=rd.id, white_id=item["white_id"], black_id=item["black_id"],
            is_bye=False, status="active", board_no=board_no,
            verdict="", current_result="", revision_id=rev.id,
        ))
        board_no += 1
    for pid in report["byes"]:
        db.add(models.Game(
            round_id=rd.id, white_id=pid, black_id=None, is_bye=True,
            status="active", board_no=board_no, verdict="BYE",
            current_result="BYE", revision_id=rev.id,
        ))
        board_no += 1

    rev.status = "applied"
    rev.applied_at = datetime.now(tz.utc)
    db.commit()
    db.refresh(rev)
    return {"id": rev.id, "status": "applied", "diff": rev.diff_summary,
            "applied_at": rev.applied_at.astimezone(timezone.utc).isoformat()}


def round_revisions(db: Session, round_id: int):
    return list(db.scalars(
        select(models.PairingRevision)
        .where(models.PairingRevision.round_id == round_id)
        .order_by(models.PairingRevision.created_at)
    ))
