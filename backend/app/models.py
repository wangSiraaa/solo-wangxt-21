"""SQLAlchemy 模型。

不可变性（"已发布轮次刷新不变"的落地方式）：
- rounds 发布后写入 roster_snapshot（当时的参赛名单）、rule_snapshot（规则版本全文）、
  pairing_snapshot（求解器产出的对阵 + 依据）；此后这些列永不更新。
- games 的裁判裁定（verdict）一经创建永不覆盖；成绩更正写入 result_corrections，
  并在 games 上更新"当前生效结果"以驱动积分重算——原始裁定完整保留在审计表。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now():
    return datetime.now(timezone.utc)


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    total_rounds: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    players: Mapped[list["Player"]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )
    rounds: Mapped[list["Round"]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan",
        order_by="Round.round_no",
    )


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("tournament_id", "registration_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    registration_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    rating: Mapped[int] = mapped_column(Integer, default=1000)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 退赛（受控）：不删除记录。withdrawn_round_no = 退赛生效轮次
    # （该轮及以后不再配对）；此前成绩全部保留，继续计入对手分。
    withdrawn_round_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    withdrawn_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tournament: Mapped["Tournament"] = relationship(back_populates="players")


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("tournament_id", "round_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    round_no: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    rule_version: Mapped[str] = mapped_column(String(32))

    # 三份发布快照：名单 / 规则全文 / 求解器输出（含逐人依据、软约束状态）
    roster_snapshot: Mapped[list] = mapped_column(JSON)
    rule_snapshot: Mapped[dict] = mapped_column(JSON)
    pairing_snapshot: Mapped[dict] = mapped_column(JSON)

    # 发布时是否携带硬规则放宽授权
    override_used: Mapped[bool] = mapped_column(Boolean, default=False)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    tournament: Mapped["Tournament"] = relationship(back_populates="rounds")
    games: Mapped[list["Game"]] = relationship(
        back_populates="round", cascade="all, delete-orphan",
        foreign_keys="Game.round_id",
    )
    revisions: Mapped[list["PairingRevision"]] = relationship(
        back_populates="round", cascade="all, delete-orphan",
        order_by="PairingRevision.created_at",
    )


class Game(Base):
    """一盘棋或一个轮空。

    verdict：裁判首次录入的原始裁定（'W' 白胜 / 'B' 黑胜 / 'D' 和 / 'BYE' 轮空）。
    current_result：当前生效结果（更正后可能与 verdict 不同；积分按它算）。
    更正绝不改 verdict，保证"不覆盖原裁定"。
    """
    __tablename__ = "games"
    __table_args__ = (
        # 软唯一性在服务层保证：同一轮每人至多一张 *active* 棋桌。
        # 不加数据库 UNIQUE，因为重排会保留 cancelled 棋桌的历史行，
        # 且两人理论上可能在同一轮先取消再（经授权）重新对上。
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"))
    white_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    black_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    is_bye: Mapped[bool] = mapped_column(Boolean, default=False)
    board_no: Mapped[int] = mapped_column(Integer, default=0)

    # active：当前生效棋桌；cancelled：受控重排中撤销，保留行用于回看与审计
    status: Mapped[str] = mapped_column(String(12), default="active")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("pairing_revisions.id"), nullable=True
    )

    verdict: Mapped[str] = mapped_column(String(8))
    current_result: Mapped[str] = mapped_column(String(8))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    entered_by: Mapped[str] = mapped_column(String(64), default="裁判组")

    round: Mapped["Round"] = relationship(
        back_populates="games", foreign_keys=[round_id]
    )
    revision: Mapped["PairingRevision | None"] = relationship(
        back_populates="games", foreign_keys=[revision_id]
    )
    corrections: Mapped[list["ResultCorrection"]] = relationship(
        back_populates="game", cascade="all, delete-orphan",
        order_by="ResultCorrection.created_at",
    )


class ResultCorrection(Base):
    __tablename__ = "result_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    old_result: Mapped[str] = mapped_column(String(8))
    new_result: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), default="裁判长")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    game: Mapped["Game"] = relationship(back_populates="corrections")


class PairingRevision(Base):
    """受控重配对记录（与成绩更正互相独立的第二条审计线）。

    原始发布内容保留在 rounds.pairing_snapshot，永不覆盖；
    本表记录"在什么局面下、因何原因、把哪些棋桌改成了什么"，
    确认前先存一行 draft（含方案指纹），确认时复检棋桌开赛状态。
    """
    __tablename__ = "pairing_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"))
    kind: Mapped[str] = mapped_column(String(24), default="controlled_repair")
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft|applied|expired
    reason: Mapped[str] = mapped_column(Text, default="")

    # 生成方案时的局面指纹：{game_id: (status, started?), withdrawals: [...]}
    situation_fingerprint: Mapped[str] = mapped_column(String(128))
    diff_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    baseline_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    proposed_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    created_by: Mapped[str] = mapped_column(String(64), default="裁判组")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expiry_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    round: Mapped["Round"] = relationship(
        back_populates="revisions", foreign_keys=[round_id]
    )
    games: Mapped[list["Game"]] = relationship(
        back_populates="revision", foreign_keys="Game.revision_id"
    )
