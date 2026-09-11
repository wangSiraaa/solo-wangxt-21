# 围棋俱乐部瑞士制比赛工作台

从报名、CP-SAT 配对、轮次发布、裁判录入、申诉改判、临时退赛到终局排名的一体化工作台。

- **前端**：Vue 3 + Vite（积分组 / 对阵 / 配对依据 / 裁判录入 / 受控重排）
- **后端**：FastAPI + OR-Tools CP-SAT（字典序分层优化，确定性求解）
- **数据库**：PostgreSQL（SQLAlchemy；SQLite 仅用于离线演示/测试）

## 一、快速开始

### Docker Compose（PostgreSQL 正式形态）

```bash
docker compose up --build
# 前端 http://localhost:5173  后端 http://localhost:8000/docs
docker compose exec backend python scripts/seed_demo.py   # 播种四场演示赛事
```

### 本地开发

```bash
# 后端
cd backend
python -m pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://go:go@localhost:5432/go_club"
python -m uvicorn app.main:app --reload
python scripts/seed_demo.py

# 前端
cd frontend
npm install && npm run dev        # http://localhost:5173 （已配置 /api 代理到 8000）
```

### 一键验证（无需 Docker，SQLite 内存流程）

```bash
cd backend
DATABASE_URL="sqlite:////tmp/check.db" python scripts/verify_demo.py
python -m pytest tests/ -q         # 7 项端到端测试
```

## 二、四场可复现演示赛事

| 赛事 | 看点 |
| --- | --- |
| 演示1 周末杯·7 人奇数场 | 每轮恰好 1 人轮空；含一次成绩更正，原裁定保留 |
| 演示2 月例会·4 人并列场 | 两人并列第 1、两人并列第 3（名次跳号），报名序号不打破并列 |
| 演示3 训练组·3 人无配对场 | 三轮打完全部组合，第 4 轮**无合法配对**：发布被阻止并给根因诊断 |
| 演示4 申诉退赛·6 人受控重排场 | R1 胜负翻转、R2 一桌已开赛、一人退赛，可直接做"生成方案→对比→确认" |

## 三、配对引擎（OR-Tools CP-SAT）

### 硬规则（无解也绝不悄悄放松）

| 代码 | 含义 |
| --- | --- |
| `ONE_GAME_PER_ROUND` | 每名在场选手每轮恰好出场一次（对局或轮空） |
| `NO_REPEAT` | 任何两人不重复交手（已交手的两人之间不生成对阵边） |
| `BYE_COUNT` | 在场人数奇→恰好 1 轮空，偶→0 轮空 |
| `LOCKED_BOARDS` | 受控重排时已开赛棋桌（选手、颜色、结果）完全不动 |

### 软约束（字典序分层，A→B→C 依次最优，可明确指出被放宽的层）

- **A `SCORE_PROXIMITY`**：对阵积分差最小
- **B `BYE_FAIRNESS`**：轮空优先给积分最低、历史轮空最少者
- **C `COLOR_BALANCE`**：先后手总次数差最小，其次避免连续同色

求解器单线程 + 固定随机种子（见 `app/rules.py`），**同输入同输出**，刷新不变化。
无可行解时返回结构化诊断：允许对阵图连通分量、奇数分量数、需要的轮空数、
无处可赛选手，以及"授权放宽 NO_REPEAT"这唯一人工出口。

### 配对依据精确到选手

`pairing_snapshot.player_reasons` 中每人都有：对手、积分差、执子颜色及原因、
赛后先后手累计；轮空者有入选原因；退赛者显式标注移出；锁定桌标注"已开赛不动"。

## 四、已发布轮次为什么刷新不变

发布（`POST /tournaments/{id}/rounds/publish`）时向 `rounds` 写入三份不可变快照：

- `roster_snapshot`：当时参赛名单（赛前积分/白黑累计/历史轮空）
- `rule_snapshot`：规则全文与 `rule_version`（当前 `swiss-cp-v1.0.0`）
- `pairing_snapshot`：求解器完整输出（对阵、轮空、逐人依据、软硬约束状态）

之后任何改判、重排、代码升级都不修改这些列。

## 五、两条互相独立的审计线

| 事件 | 表 | 不可变内容 | 可变内容 |
| --- | --- | --- | --- |
| 成绩改判 | `result_corrections` | `games.verdict`（裁判首次裁定）永不覆盖 | `games.current_result` 驱动积分/小分 |
| 对阵重排 | `pairing_revisions` | 发布快照、被撤销棋桌行（置 `cancelled` 保留） | 插入新 `active` 棋桌关联修订号 |

改判接口返回 `ranking_impact`：哪些选手的积分/Buchholz/名次因此变化。
**积分小分变化只来自成绩；对阵表变化只来自重排**，两者在数据层互不串改。
重复提交同一裁定 / 重复确认同一方案均幂等，只形成一次有效变更。

## 六、受控重配对操作流程（申诉 + 退赛）

1. 裁判对已开赛棋桌点"标记开赛"（`POST /games/{id}/start`），该桌进入锁定集。
2. 退赛（`POST /tournaments/{id}/players/{pid}/withdraw`，需理由）：
   - **不删除报名记录**；既有成绩与对手小分全部保留，继续计入终局排名；
   - 从当前轮起移出配对池；其在当前轮的未开赛棋桌立即置 `cancelled`；
   - 已在开赛桌上的选手退赛被拒绝（409）。
3. 生成重排方案（`POST /tournaments/{id}/repairs/plan`）：
   - 引擎只对空闲选手重新求解，锁定桌固定颜色；
   - 同时计算**维持原表**与**局部重排**两套全轮代价（A/B/C + 无桌人数）；
   - 返回撤销桌、新开桌、轮空变化、改动桌数。
4. 确认（`POST /repairs/{revision_id}/confirm`）：
   - 用方案生成时的 **SHA-256 局面指纹** 复检；期间若又有棋桌开赛 → **409 方案过期**，
     新桌自动加入锁定集，需重新生成；
   - 确认在一个事务内：撤销旧未开赛桌（行保留）→ 插入新桌 → 修订置 `applied`。

硬规则无解时方案 `feasible=false`、确认返回 422；只有裁判长勾选授权并填写
≥4 字理由放宽 `NO_REPEAT`，重复对阵才会在对阵表、选手依据、修订记录中全程标注。

## 七、终局排名

积分 → Buchholz 对手分（轮空轮按 0 分对手）→ 裁 1（去最高对手分）→
胜局数 → 等级分；全部相同则**并列同名次、并列后跳号**。报名序号仅决定并列者展示顺序。

## 八、主要 API

```
POST   /tournaments                              建赛 + 批量报名
POST   /tournaments/{id}/players                 补报名（首轮发布后名单冻结）
GET    /tournaments/{id}/status?preview=true     积分榜 / 轮次 / 下轮预览（工作台全量数据）
GET    /tournaments/{id}/pairings/preview        下轮配对预览（不落库）
POST   /tournaments/{id}/rounds/publish          发布轮次（冻结三份快照）
POST   /games/{id}/start                         标记棋桌开赛（重排锁定）
POST   /games/{id}/result                        裁判首次录入（不可覆盖）
POST   /games/{id}/correction                    成绩更正（原裁定保留，返回排名影响，幂等）
POST   /tournaments/{id}/players/{pid}/withdraw  退赛（记录保留）
POST   /tournaments/{id}/repairs/plan            受控重排方案 + 原表/重排代价对比
POST   /repairs/{rid}/confirm                    确认重排（指纹复检，幂等）
GET    /rules                                    当前规则全文
```

## 九、目录

```
backend/
  app/rules.py         规则版本与软硬约束定义（每轮发布写入快照）
  app/pairing.py       CP-SAT 引擎：solve_pairings / solve_repair / 无解诊断
  app/standings.py     积分、Buchholz、并列排名（只认 active 棋桌）
  app/repairs.py       退赛、开赛、指纹、方案对比、确认复检（第二条审计线）
  app/services.py      报名、发布、录入、更正（第一条审计线）、工作台状态
  app/models.py        Tournament/Player/Round/Game/ResultCorrection/PairingRevision
  scripts/seed_demo.py 四场演示赛事
  scripts/verify_demo.py 一键断言验证
  tests/               7 项端到端测试（奇数/并列/无解/不可变/更正/重排组合）
frontend/src/
  components/PairingConsole.vue   配对预览、软硬约束、逐人依据、发布
  components/ControlledRepair.vue 重排方案对比、改动桌数、确认/过期
  components/RoundsList.vue       棋桌开赛、录入、更正、修订历史、快照回看
  components/StandingsTable.vue   积分组、并列、退赛标识
```
