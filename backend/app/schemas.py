from __future__ import annotations

from pydantic import BaseModel, Field


# ---- 赛事 / 报名 ----

class PlayerIn(BaseModel):
    name: str
    rating: int = 1000
    registration_no: int | None = None


class TournamentIn(BaseModel):
    name: str
    total_rounds: int = Field(default=5, ge=1, le=20)
    players: list[PlayerIn] = []


class PlayerOut(BaseModel):
    id: int
    registration_no: int
    name: str
    rating: int
    active: bool
    withdrawn: bool = False
    withdrawn_round_no: int | None = None
    withdrawn_reason: str | None = None


# ---- 受控重配对 ----

class WithdrawIn(BaseModel):
    reason: str = Field(min_length=4)


class RepairPlanIn(BaseModel):
    override_no_repeat: bool = False
    override_reason: str = ""


class TournamentOut(BaseModel):
    id: int
    name: str
    total_rounds: int
    players: list[PlayerOut]


# ---- 配对 ----

class PairingPreviewIn(BaseModel):
    override_no_repeat: bool = False
    override_reason: str = ""


# ---- 成绩 ----

class ResultIn(BaseModel):
    result: str = Field(description="W 白胜 / B 黑胜 / D 和 / BYE 轮空")
    entered_by: str = "裁判组"


class CorrectionIn(BaseModel):
    new_result: str
    reason: str = Field(min_length=4)
    created_by: str = "裁判长"


class GameOut(BaseModel):
    id: int
    white_id: int | None
    white_name: str | None
    black_id: int | None
    black_name: str | None
    is_bye: bool
    board_no: int = 0
    status: str = "active"
    started: bool = False
    started_at: str | None = None
    cancelled_reason: str | None = None
    verdict: str
    current_result: str
    corrected: bool
    corrections: list["CorrectionOut"] = []


class CorrectionOut(BaseModel):
    id: int
    old_result: str
    new_result: str
    reason: str
    created_by: str
    created_at: str


class RoundOut(BaseModel):
    id: int
    round_no: int
    rule_version: str
    published_at: str
    override_used: bool
    override_reason: str | None
    roster_snapshot: list
    rule_snapshot: dict
    pairing_snapshot: dict
    games: list[GameOut] = []
    revisions: list[dict] = []


class StandingOut(BaseModel):
    rank: int
    player_id: int
    registration_no: int
    name: str
    rating: int
    points: float
    buchholz: float
    buchholz_cut1: float
    wins: int
    draws: int
    losses: int
    byes: int
    played: int
    color_white: int
    color_black: int
    tied: bool
    tied_with: list[int]
    correction_count: int
    withdrawn: bool = False
    withdrawn_round_no: int | None = None


class FullStatus(BaseModel):
    tournament: TournamentOut
    rules: dict
    standings: list[StandingOut]
    rounds: list[RoundOut]
    next_round_no: int | None
    preview: dict | None = None


GameOut.model_rebuild()
