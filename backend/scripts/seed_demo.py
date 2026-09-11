"""播种四场可复现的演示赛事。

  [演示1] 周末杯·7 人奇数场      5 轮，已发布 2 轮（含一次成绩更正），可预览/发布第 3 轮
  [演示2] 月例会·4 人并列排名场   3 轮，已发布 2 轮，榜首两人与榜尾两人分别并列
  [演示3] 训练组·3 人无配对场     4 轮，已发布 3 轮（所有组合均已交手），第 4 轮无合法配对
  [演示4] 申诉退赛·6 人受控重排场 4 轮，R1 成绩已翻转改判；R2 第一桌已开赛锁定、
                                  叶让退赛，可直接"生成重排方案 → 对比 → 确认"

结果策略全部确定性：
  odd 场——等级分高者胜（同分和棋），随后对第 1 轮某盘做成绩更正
  ties 场——第 1 轮白胜、第 2 轮和棋，制造积分/对手分/胜局/等级分全同的并列
  infeasible 场——白胜/黑胜/和棋各一轮，三轮覆盖全部两两组合
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, repairs, services  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402


def _reset(db):
    """删除同名旧演示赛事，保证脚本可重复执行（演示库专用）。"""
    old = list(db.scalars(
        select(models.Tournament).where(models.Tournament.name.like("[演示%"))
    ))
    for t in old:
        db.delete(t)
    db.commit()


def _create(db, name, rounds, players):
    from app.schemas import PlayerIn, TournamentIn
    return services.create_tournament(db, TournamentIn(
        name=name, total_rounds=rounds,
        players=[PlayerIn(name=n, rating=r, registration_no=no)
                 for no, n, r in players],
    ))


def _publish_and_fill(db, tid, policy, *, enter=services.enter_result):
    """发布下一轮并按策略填入结果，返回 Round。"""
    rd = services.publish_round(db, tid, False, "")
    games = list(db.scalars(select(models.Game).where(models.Game.round_id == rd.id)))
    pmap = {p.id: p for p in db.scalars(
        select(models.Player).where(models.Player.tournament_id == tid)
    )}
    for g in sorted(games, key=lambda x: (x.is_bye, x.id)):
        if g.is_bye:
            continue
        if policy == "higher":
            wr, br = pmap[g.white_id].rating, pmap[g.black_id].rating
            result = "D" if wr == br else ("W" if wr > br else "B")
        else:
            result = policy  # "W" / "B" / "D"
        enter(db, g.id, result, "裁判组（演示预录）")
    return rd


def seed_all() -> dict[str, int]:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        _reset(db)

        # ---- 演示 1：7 人奇数场 ----
        odd_players = [
            (1, "柯浩", 1820), (2, "林雪", 1760), (3, "苏远", 1705),
            (4, "周明", 1680), (5, "陈曦", 1640), (6, "韩冬", 1590),
            (7, "叶童", 1530),
        ]
        t1 = _create(db, "[演示1] 周末杯·7人奇数场", 5, odd_players)
        r1 = _publish_and_fill(db, t1.id, "higher")
        _publish_and_fill(db, t1.id, "higher")

        # 成绩更正：第 1 轮第一盘（非轮空）由原裁定改为和棋
        g1 = db.scalar(
            select(models.Game).where(models.Game.round_id == r1.id,
                                      models.Game.is_bye == False)  # noqa: E712
        )
        services.correct_result(
            db, g1.id, "D",
            "复盘数子确认终局结果应为和棋；原裁判裁定保留备查，积分与小分重算",
            "裁判长",
        )

        # ---- 演示 2：4 人并列场（同等级分） ----
        tie_players = [
            (1, "赵一", 1600), (2, "钱二", 1600),
            (3, "孙三", 1600), (4, "李四", 1600),
        ]
        t2 = _create(db, "[演示2] 月例会·4人并列排名场", 3, tie_players)
        _publish_and_fill(db, t2.id, "W")
        _publish_and_fill(db, t2.id, "D")

        # ---- 演示 3：3 人无配对场 ----
        inf_players = [(1, "南宫胜", 1700), (2, "西门局", 1650), (3, "北冥子", 1600)]
        t3 = _create(db, "[演示3] 训练组·3人无合法配对场", 4, inf_players)
        _publish_and_fill(db, t3.id, "W")
        _publish_and_fill(db, t3.id, "B")
        _publish_and_fill(db, t3.id, "D")

        # ---- 演示 4：6 人受控重排场 ----
        rep_players = [
            (1, "顾盘", 1810), (2, "路衡", 1760), (3, "闻笙", 1710),
            (4, "沈砚", 1660), (5, "池雨", 1610), (6, "叶让", 1560),
        ]
        t4 = _create(db, "[演示4] 申诉退赛·6人受控重排场", 4, rep_players)
        r4_1 = _publish_and_fill(db, t4.id, "W")

        # R1 申诉翻转：第一盘 W -> B（跨轮影响积分与对手小分）
        g_flip = db.scalar(
            select(models.Game).where(models.Game.round_id == r4_1.id,
                                      models.Game.is_bye == False)  # noqa: E712
        )
        services.correct_result(
            db, g_flip.id, "B",
            "申诉受理：终局数子复核，原判白胜翻转为黑胜", "裁判长",
        )

        # R2 发布：第一桌开赛锁定，叶让退赛（留一个待确认的重排局面）
        r4_2 = services.publish_round(db, t4.id, False, "")
        g4 = list(db.scalars(select(models.Game).where(models.Game.round_id == r4_2.id)))
        first_board = next(g for g in g4 if not g.is_bye)
        repairs.mark_started(db, first_board.id)
        yerang = next(p for p in db.get(models.Tournament, t4.id).players
                      if p.name == "叶让")
        # 若叶让恰好在锁定桌（确定性求解下基本不会），选另一人
        if yerang.id in (first_board.white_id, first_board.black_id):
            yerang = next(p for p in db.get(models.Tournament, t4.id).players
                          if p.id not in (first_board.white_id, first_board.black_id))
        repairs.withdraw_player(db, t4.id, yerang.id, "赛前突发伤病退赛")

        ids = {"odd": t1.id, "ties": t2.id, "infeasible": t3.id, "repair": t4.id}
        print("演示赛事就绪：", ids)
        return ids
    finally:
        db.close()


if __name__ == "__main__":
    seed_all()
