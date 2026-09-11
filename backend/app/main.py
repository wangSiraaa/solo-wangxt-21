"""FastAPI 入口。

工作台 API：
  POST /tournaments                      建赛 + 批量报名
  POST /tournaments/{id}/players         补报名（首轮发布后名单冻结）
  GET  /tournaments                      赛事列表
  GET  /tournaments/{id}/status          积分组 / 已发布轮次 / 终局排名（核心工作台数据）
  GET  /tournaments/{id}/pairings/preview   下一轮配对预览（不落库）
  POST /tournaments/{id}/rounds/publish      发布轮次（冻结名单 + 规则版本快照）
  POST /games/{id}/result                裁判首次录入（原裁定不可覆盖）
  POST /games/{id}/correction            成绩更正（保留原裁定，重算小分）
  GET  /rules                            当前规则全文与版本
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas, services
from .db import Base, engine, get_db
from .rules import RULES

app = FastAPI(title="围棋俱乐部瑞士制工作台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    Base.metadata.create_all(engine)


@app.get("/rules")
def get_rules():
    return RULES


@app.post("/tournaments", response_model=schemas.TournamentOut, status_code=201)
def create_tournament(data: schemas.TournamentIn, db: Session = Depends(get_db)):
    return services.create_tournament(db, data)


@app.get("/tournaments", response_model=list[schemas.TournamentOut])
def list_tournaments(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(models.Tournament).order_by(models.Tournament.id)))
    return rows


@app.get("/tournaments/{tournament_id}", response_model=schemas.TournamentOut)
def get_tournament(tournament_id: int, db: Session = Depends(get_db)):
    t = db.get(models.Tournament, tournament_id)
    if t is None:
        from fastapi import HTTPException
        raise HTTPException(404, "赛事不存在")
    return t


@app.post("/tournaments/{tournament_id}/players",
          response_model=schemas.PlayerOut, status_code=201)
def add_player(tournament_id: int, data: schemas.PlayerIn,
               db: Session = Depends(get_db)):
    return services.add_player(db, tournament_id, data.name, data.rating,
                               data.registration_no)


@app.get("/tournaments/{tournament_id}/status", response_model=schemas.FullStatus)
def status(tournament_id: int, preview: bool = False,
           override_no_repeat: bool = False, override_reason: str = "",
           db: Session = Depends(get_db)):
    return services.full_status(db, tournament_id, preview,
                                override_no_repeat, override_reason)


@app.get("/tournaments/{tournament_id}/pairings/preview")
def preview(tournament_id: int, override_no_repeat: bool = False,
            override_reason: str = "", db: Session = Depends(get_db)):
    return services.preview_pairing(db, tournament_id,
                                    override_no_repeat, override_reason)


@app.post("/tournaments/{tournament_id}/rounds/publish", status_code=201)
def publish(tournament_id: int, data: schemas.PairingPreviewIn,
            db: Session = Depends(get_db)):
    rd = services.publish_round(db, tournament_id, data.override_no_repeat,
                                data.override_reason)
    return {"id": rd.id, "round_no": rd.round_no, "rule_version": rd.rule_version,
            "published_at": rd.published_at.isoformat(),
            "pairing_snapshot": rd.pairing_snapshot}


@app.post("/games/{game_id}/result")
def enter_result(game_id: int, data: schemas.ResultIn,
                 db: Session = Depends(get_db)):
    g = services.enter_result(db, game_id, data.result, data.entered_by)
    return {"id": g.id, "verdict": g.verdict, "current_result": g.current_result}


@app.post("/games/{game_id}/correction")
def correct_result(game_id: int, data: schemas.CorrectionIn,
                   db: Session = Depends(get_db)):
    g = services.correct_result(db, game_id, data.new_result,
                                data.reason, data.created_by)
    return {"id": g.id, "verdict": g.verdict, "current_result": g.current_result,
            "note": "原裁定保留；积分与小分已按新结果重算"}
