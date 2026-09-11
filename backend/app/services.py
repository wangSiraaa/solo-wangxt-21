"""赛事用例层：报名、配对预览/发布、成绩录入、更正、终局排名。"""
from __future__ import annotations

from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .pairing import solve_pairings
from .rules import RULES, RULES_VERSION
from .standings import compute_standings, state_for_pairing

VALID_RESULTS = {"W", "B", "D", "BYE"}
RESULT_TEXT = {"W": "白胜", "B": "黑胜", "D": "和棋", "BYE": "轮空"}


# ---------------------------------------------------------------- 报名

def create_tournament(db: Session, data) -> models.Tournament:
    t = models.Tournament(name=data.name, total_rounds=data.total_rounds)
    db.add(t)
    db.flush()
    used_nos = {p.registration_no for p in data.players if p.registration_no is not None}
    auto = 1
    for p in data.players:
        no = p.registration_no
        if no is None:
            while auto in used_nos:
                auto += 1
            no = auto
        used_nos.add(no)
        auto = max(auto, no + 1)
        db.add(models.Player(
            tournament_id=t.id, registration_no=no, name=p.name, rating=p.rating,
        ))
    db.commit()
    db.refresh(t)
    return t


def add_player(db: Session, tournament_id: int, name: str, rating: int,
               registration_no: int | None) -> models.Player:
    t = db.get(models.Tournament, tournament_id)
    if t is None:
        raise HTTPException(404, "赛事不存在")
    if _rounds(db, t):
        raise HTTPException(
            409, "赛事已发布过轮次，名单已冻结；新报名只能进入下一赛事（保护已发布快照）"
        )
    q = select(models.Player).where(models.Player.tournament_id == tournament_id)
    existing = list(db.scalars(q))
    if registration_no is None:
        registration_no = (max((p.registration_no for p in existing), default=0) + 1)
    if any(p.registration_no == registration_no for p in existing):
        raise HTTPException(409, f"报名序号 {registration_no} 已占用")
    p = models.Player(tournament_id=tournament_id, registration_no=registration_no,
                      name=name, rating=rating)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ---------------------------------------------------------------- 数据准备

def _load(db: Session, tournament_id: int) -> models.Tournament:
    t = db.get(models.Tournament, tournament_id)
    if t is None:
        raise HTTPException(404, "赛事不存在")
    return t


def _rounds(db: Session, t: models.Tournament) -> list[models.Round]:
    """显式查询已发布轮次——expire_on_commit=False 时关系集合可能滞后。"""
    return list(db.scalars(
        select(models.Round)
        .where(models.Round.tournament_id == t.id)
        .order_by(models.Round.round_no)
    ))


def _games_by_round(db: Session, rounds):
    out: dict[int, list[models.Game]] = {}
    for rd in rounds:
        out[rd.id] = list(db.scalars(
            select(models.Game).where(models.Game.round_id == rd.id)
        ))
    return out


def _player_states(db: Session, t: models.Tournament, rounds=None):
    rounds = rounds if rounds is not None else _rounds(db, t)
    return state_for_pairing(t, rounds, _games_by_round(db, rounds))


# ---------------------------------------------------------------- 配对预览/发布

def preview_pairing(db: Session, tournament_id: int, override_no_repeat: bool,
                    override_reason: str) -> dict:
    t = _load(db, tournament_id)
    rounds = _rounds(db, t)
    next_no = len(rounds) + 1
    if next_no > t.total_rounds:
        raise HTTPException(409, "赛事轮次已全部发布")
    players = _player_states(db, t, rounds)
    # 与发布保持一致：此前已退赛者不进入配对池
    already_withdrawn = {
        p.id for p in t.players
        if p.withdrawn_round_no is not None and p.withdrawn_round_no <= len(rounds)
    }
    players = [p for p in players if p.pid not in already_withdrawn]
    report = solve_pairings(
        players,
        allow_repeat=override_no_repeat,
        override_reason=override_reason,
    )
    return {
        "round_no": next_no,
        "rule_version": RULES_VERSION,
        "feasible": report.feasible,
        "report": _report_json(report),
        "note": "预览未落库；点击发布才会冻结名单与规则快照" if report.feasible
                else "无合法配对：硬规则未被放松，发布被阻止",
    }


def publish_round(db: Session, tournament_id: int, override_no_repeat: bool,
                  override_reason: str) -> models.Round:
    t = _load(db, tournament_id)
    rounds = _rounds(db, t)
    next_no = len(rounds) + 1
    if next_no > t.total_rounds:
        raise HTTPException(409, "赛事轮次已全部发布")
    if override_no_repeat and len(override_reason.strip()) < 4:
        raise HTTPException(422, "放宽 NO_REPEAT 必须填写不少于 4 个字符的书面理由")

    players = _player_states(db, t, rounds)
    # 此前已退赛者（withdrawn_round_no < 新轮次）不进入新一轮配对池
    already_withdrawn = {
        p.id for p in t.players
        if p.withdrawn_round_no is not None and p.withdrawn_round_no <= len(rounds)
    }
    active_players = [p for p in players if p.pid not in already_withdrawn]
    report = solve_pairings(
        active_players, allow_repeat=override_no_repeat,
        override_reason=override_reason,
    )
    if not report.feasible:
        raise HTTPException(
            422,
            {"message": "硬约束下无合法配对，已阻止发布（不会悄悄突破硬规则）",
             "infeasibility": report.infeasibility},
        )

    roster_snapshot = [
        {"player_id": p.pid, "registration_no": p.registration_no,
         "name": p.name, "rating": p.rating,
         "score_before_round": p.score2 / 2,
         "whites": p.whites, "blacks": p.blacks, "byes": p.byes,
         "withdrawn": p.pid in already_withdrawn}
        for p in players
    ]
    pairing_snapshot = _report_json(report)

    rd = models.Round(
        tournament_id=t.id, round_no=next_no, rule_version=RULES_VERSION,
        roster_snapshot=roster_snapshot, rule_snapshot=RULES,
        pairing_snapshot=pairing_snapshot,
        override_used=override_no_repeat,
        override_reason=override_reason if override_no_repeat else None,
    )
    db.add(rd)
    db.flush()

    for board_no, (wpid, bpid, _note) in enumerate(report.pairs, start=1):
        db.add(models.Game(
            round_id=rd.id, white_id=wpid, black_id=bpid, is_bye=False,
            board_no=board_no, verdict="", current_result="",
        ))
    for bpid in report.byes:
        db.add(models.Game(
            round_id=rd.id, white_id=bpid, black_id=None, is_bye=True,
            board_no=900, verdict="BYE", current_result="BYE",
        ))
    db.commit()
    db.refresh(rd)
    return rd


def _report_json(report) -> dict:
    return {
        "feasible": report.feasible,
        "rule_version": report.rule_version,
        "pairs": [
            {"white_id": w, "black_id": b, "color_reason": why}
            for (w, b, why) in report.pairs
        ],
        "byes": report.byes,
        "player_reasons": {str(k): v for k, v in report.player_reasons.items()},
        "tiers": report.tiers,
        "hard_constraints": report.hard_constraints,
        "objective_values": report.objective_values,
        "overrides": report.overrides,
        "infeasibility": report.infeasibility,
    }


# ---------------------------------------------------------------- 成绩录入

def enter_result(db: Session, game_id: int, result: str, entered_by: str) -> models.Game:
    g = db.get(models.Game, game_id)
    if g is None:
        raise HTTPException(404, "对局不存在")
    result = result.upper()
    if g.is_bye:
        result = "BYE"
    elif result not in VALID_RESULTS or result == "BYE":
        raise HTTPException(422, f"非法结果 {result}；可选 W/B/D")
    if g.verdict:
        raise HTTPException(409, "裁判裁定已存在且不可覆盖；如需更改请走"
                                 "“成绩更正”流程并写明理由")
    g.verdict = result
    g.current_result = result
    g.entered_by = entered_by
    db.commit()
    db.refresh(g)
    return g


def correct_result(db: Session, game_id: int, new_result: str, reason: str,
                   created_by: str) -> dict:
    """成绩更正（幂等）。

    - 原裁定 verdict 永不动；变更追加到 result_corrections。
    - 多次提交"同一裁定"（new_result 等于当前生效结果）只返回已存在变更，
      不新增审计行——只形成一次有效变更。
    - 返回排名影响：哪些人的积分/小分/名次因此变化；对阵表本身不受影响。
    """
    g = db.get(models.Game, game_id)
    if g is None:
        raise HTTPException(404, "对局不存在")
    new_result = new_result.upper()
    if g.is_bye:
        raise HTTPException(422, "轮空结果不接受更正")
    if new_result not in {"W", "B", "D"}:
        raise HTTPException(422, f"非法更正结果 {new_result}")
    if not g.verdict:
        raise HTTPException(409, "原裁定尚不存在，应使用正常录入")

    t = db.get(models.Tournament, g.round.tournament_id)
    rounds = _rounds(db, t)
    gbr = _games_by_round(db, rounds)

    def _rank_map():
        rows = compute_standings(t, rounds, gbr)
        return {s.player_id: s for s in rows}

    before = {pid: {"points": s.points, "buchholz": s.buchholz,
                    "buchholz_cut1": s.buchholz_cut1, "rank": s.rank}
              for pid, s in _rank_map().items()}

    idempotent = False
    if new_result == g.current_result:
        existing = next((c for c in g.corrections if c.new_result == new_result), None)
        if existing:
            idempotent = True
        else:
            raise HTTPException(422, "新结果与当前生效结果相同")

    corr = None
    if not idempotent:
        corr = models.ResultCorrection(
            game_id=g.id, old_result=g.current_result, new_result=new_result,
            reason=reason, created_by=created_by,
        )
        db.add(corr)
        g.current_result = new_result
        db.commit()
        db.refresh(g)

    after_map = _rank_map()
    impacts = []
    pname = {p.id: p.name for p in t.players}
    for pid, b in before.items():
        a = after_map[pid]
        a_d = {"points": a.points, "buchholz": a.buchholz,
               "buchholz_cut1": a.buchholz_cut1, "rank": a.rank}
        if a_d != b:
            impacts.append({"player_id": pid, "name": pname[pid],
                            "before": b, "after": a_d})

    return {
        "game_id": g.id, "verdict": g.verdict, "current_result": g.current_result,
        "idempotent": idempotent,
        "correction": None if idempotent else {
            "old_result": corr.old_result, "new_result": corr.new_result,
            "reason": corr.reason,
        },
        "ranking_impact": impacts,
        "note": ("重复提交同一裁定：未产生新变更（幂等）" if idempotent
                 else "原裁定保留；积分与小分已重算，对阵表不变"),
    }


# ---------------------------------------------------------------- 排名 / 全量状态

def standings(db: Session, tournament_id: int):
    t = _load(db, tournament_id)
    rounds = _rounds(db, t)
    gbr = _games_by_round(db, rounds)
    correction_counts: dict[int, int] = {}
    for games in gbr.values():
        for g in games:
            if g.corrections:
                for pid in (g.white_id, g.black_id):
                    if pid is not None:
                        correction_counts[pid] = correction_counts.get(pid, 0) + len(g.corrections)
    return compute_standings(t, rounds, gbr, correction_counts)


def full_status(db: Session, tournament_id: int, include_preview: bool,
                override_no_repeat: bool, override_reason: str) -> dict:
    t = _load(db, tournament_id)
    rounds = _rounds(db, t)
    gbr = _games_by_round(db, rounds)
    standings_rows = standings(db, tournament_id)
    pname = {p.id: p.name for p in t.players}

    rounds_out = []
    from .repairs import round_revisions
    for rd in rounds:
        games = []
        for g in gbr[rd.id]:
            games.append({
                "id": g.id,
                "board_no": g.board_no,
                "white_id": g.white_id, "white_name": pname.get(g.white_id) if g.white_id else None,
                "black_id": g.black_id, "black_name": pname.get(g.black_id) if g.black_id else None,
                "is_bye": g.is_bye,
                "status": g.status,
                "started": g.started_at is not None,
                "started_at": g.started_at.astimezone(timezone.utc).isoformat() if g.started_at else None,
                "cancelled_reason": g.cancelled_reason,
                "verdict": g.verdict, "current_result": g.current_result,
                "corrected": bool(g.verdict) and g.current_result != g.verdict,
                "corrections": [{
                    "id": c.id, "old_result": c.old_result, "new_result": c.new_result,
                    "reason": c.reason, "created_by": c.created_by,
                    "created_at": c.created_at.astimezone(timezone.utc).isoformat(),
                } for c in g.corrections],
            })
        games.sort(key=lambda x: (x["status"] != "active", x["is_bye"],
                                  x["board_no"], x["white_name"] or ""))
        revisions = [{
            "id": rv.id, "status": rv.status, "kind": rv.kind, "reason": rv.reason,
            "created_at": rv.created_at.astimezone(timezone.utc).isoformat(),
            "applied_at": rv.applied_at.astimezone(timezone.utc).isoformat() if rv.applied_at else None,
            "expiry_note": rv.expiry_note,
            "diff": rv.diff_summary,
        } for rv in round_revisions(db, rd.id)]
        rounds_out.append({
            "id": rd.id, "round_no": rd.round_no, "rule_version": rd.rule_version,
            "published_at": rd.published_at.astimezone(timezone.utc).isoformat(),
            "override_used": rd.override_used, "override_reason": rd.override_reason,
            "roster_snapshot": rd.roster_snapshot,
            "rule_snapshot": rd.rule_snapshot,
            "pairing_snapshot": rd.pairing_snapshot,
            "games": games,
            "revisions": revisions,
        })

    next_no = len(rounds) + 1
    data = {
        "tournament": {
            "id": t.id, "name": t.name, "total_rounds": t.total_rounds,
            "players": [{
                "id": p.id, "registration_no": p.registration_no,
                "name": p.name, "rating": p.rating, "active": p.active,
                "withdrawn": p.withdrawn_round_no is not None,
                "withdrawn_round_no": p.withdrawn_round_no,
                "withdrawn_reason": p.withdrawn_reason,
            } for p in sorted(t.players, key=lambda x: x.registration_no)],
        },
        "rules": RULES,
        "standings": [{
            "rank": s.rank, "player_id": s.player_id,
            "registration_no": s.registration_no, "name": s.name, "rating": s.rating,
            "points": s.points, "buchholz": s.buchholz,
            "buchholz_cut1": s.buchholz_cut1,
            "wins": s.wins, "draws": s.draws, "losses": s.losses,
            "byes": s.byes, "played": s.played,
            "color_white": s.color_white, "color_black": s.color_black,
            "tied": len(s.tied_with) > 1, "tied_with": s.tied_with,
            "correction_count": s.correction_count,
            "withdrawn": s.withdrawn,
            "withdrawn_round_no": s.withdrawn_round_no,
        } for s in standings_rows],
        "rounds": rounds_out,
        "next_round_no": next_no if next_no <= t.total_rounds else None,
    }
    if include_preview and data["next_round_no"] is not None:
        data["preview"] = preview_pairing(db, tournament_id,
                                          override_no_repeat, override_reason)
    return data
