# Portfolio · 投资组合管理

一个轻量的 A 股投资组合与分红透视工具。后端用 FastAPI + SQLite，前端是 Vue 3 + Vite（路由用 vue-router 的 hash 模式）。

```
portfolio/
├── portfolio_api/         # 后端 FastAPI
└── portfolio_web/         # 前端 Vue 3
```

## 功能

- 多个「投资组合」管理（增删改）：header 里直接 **新增 / 重命名 / 删除** 组合（删除前二次确认，会提示其下资产与目标一并删除）；一个组合都没有时给出空态引导
- 每个组合下挂若干资产：持仓数量、当前价、每股分红、TTM 股息率等
- **支持个股与 ETF / LOF / 封闭式基金**
  - 个股：`0`/`3`/`6`/`9` 开头，分红取最近一次已实施
  - ETF（如 `561580` 央企红利ETF）：`5`/`15`/`16`/`18` 开头，红利 ETF 多为**月度分红**，TTM 按**近 12 个月合计**计算
- 一键拉取实时行情（腾讯）+ 历史分红（个股走 cninfo，ETF 走 sina，均经 akshare）
- 加权 TTM 收益率、预计年收入、资产明细展示
- **成本与盈亏**：记录每股建仓成本（新增时未填则默认取当前价），自动算持仓成本、浮动盈亏与盈亏比例，列表按标的展示、概览卡片汇总总盈亏
  - 成本是历史事实，刷新价格**不会**改写它
  - **支持负成本**（分红 / 高抛低吸后摊薄成本转负，A 股常见）；成本 ≤ 0 时盈亏比例不适用，只展示金额
  - 盈亏配色遵循 A 股惯例：**红涨绿跌**
- **持仓分布可视化**：持仓市值分布 + 预计年收入分布两张饼图（实心，饼图下方附 TOP5 图例，悬浮查看任一项明细），同一标的跨图同色，便于对照股息效率
- **年收入目标**：每个组合可维护多个「年收入目标」（目标名称 + 目标年收入，单位元），选中目标后展示：
  - 当前（年 / 月均）与目标（年 / 月均）、缺口、进度条、达成 / 超额状态
  - 路径 ①：收益率不变，**还需再投入多少本金**（默认按组合加权 TTM 收益率，可手填「假设加仓收益率」覆盖，不持久化）
  - 路径 ②：本金不变，**TTM 收益率需要达到多少**（口径为「年收入 ÷ 持仓市值」，**不是**加权 TTM，UI 有 tooltip 说明差异）
  - 目标绑定组合（切换组合只看该组合的目标、删除组合级联删除目标）；同组合内重名返回 409；「上次选中的目标」按组合记在 localStorage，手填的假设收益率不记
- **列表可自定义**：「列设置」勾选要显示的列；拖动表头调整列顺序；拖动列边界调整宽度（双击复位）；三项设置都持久化到浏览器
- 历年分红展开表（含 24 / 25 / 26 年每股分红列）、按字段排序
- **收入日历**（header 第二个 tab，`#/calendar`）：按当前组合看一整年的分红现金流
  - **年视图**：Q1–Q4 × 3 月的网格，每格显示当月合计金额 + 笔数 + 状态色点（已到账 / 已公告 / 预测 / 补录 / 无）
  - **月视图**：12 根**按来源堆叠**的柱（股 / ETF / 债 / OTC / REITs / 自定义 / 自建 / 补录）+ 顶部「年度累计」折线
  - 点月份 → 底部 70vh **月 Sheet** 列出该月逐笔：标的、类型、派息日、金额、状态
  - 每笔支持「**点击确认**」（把预告标成已到账，幂等、不加金额）与「**+ 补录**」（手动新增一笔，不能补未来日期）
  - 数据与「TTM / 历年分红」共用同一份 akshare 分红缓存，按派息日归月；akshare 只收录**已发生**的分红，因此未来月份用「**去年同期预估**」补上（标记「预估」，与已收录分开统计）
  - 顶部同时给出 `{年}年合计`（= 已收录 + 预估）、`已收录 / 预估` 拆分、以及 `近 12 个月`（与概览卡「预计年收入」同口径，两者相等），一眼看清「自然年合计」为什么和「预计年收入」不同
  - 没有任何去年同期记录可参照的未来月份，显示 ¥0.00 并标注「待收录」（不外推）

- **观察池**（header 第三个 tab，`#/watchlist`）：给「还没买、但想买」的红利标的建一份**全局**候选清单（**不绑定组合**、不参与组合汇总）
  - 每条设 **目标股息率**（必填，`0 < v ≤ 30`）与 **目标价**（选填）；后端每日收盘后自动抓「实时价 + TTM 每股分红」写当日快照，也支持「刷新全部 / 单条刷新」
  - 列表给出：现价、TTM 每股分红、当前股息率、距目标（百分点）、**触发价**（= TTM 分红 ÷ 目标股息率）、距触发价、目标价，以及**两条互相独立**的信号（股息率线 / 价格线，**绝不合并**成单一「可买入」结论）
  - 每行可展开 220px 双轴走势（价格右轴 ¥ + 股息率左轴 %），时间窗 30 / 90 / 180 天；**缺日如实断线**，不插值、不回填
  - 「已持有」标记：该代码已在任意组合里时显示 chip（hover 看组合名），该行**仍保留**在列表中
  - 「转入持仓」：跳回 `#/` 并打开既有资产表单、预填代码 / 名称 / 现价（数量自己填），仍走既有写入接口，没有第二条资产写入链路
  - 只有**真实价**才落库：行情降级模拟价时不写快照，保留上次数据并提示「行情获取失败，已保留上次数据」

> 数据来源与每个数字的计算口径详见 [`docs/data-processing.md`](docs/data-processing.md)。

## 环境要求

- Python ≥ 3.11（推荐 3.12 / 3.13）
- Node.js ≥ 20
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

## 快速开始

### 1. 后端（端口 8000）

```bash
cd portfolio_api
uv sync                                  # 创建 .venv 并安装依赖
uv run uvicorn main:app --reload --port 8000
# 健康检查
curl http://localhost:8000/              # {"message":"投资组合管理 API","docs":"/docs"}
```

首次启动会自动：

- 建表（SQLite `portfolio.db`）
- 种入 3 个示例组合 + 若干资产
- 第一次搜索股票时拉 akshare 数据并写入 `stock_cache.json`

交互文档：http://localhost:8000/docs

### 2. 前端（端口 5173）

```bash
cd portfolio_web
npm install
npm run dev
```

打开 http://localhost:5173

前端通过 vite proxy（已在 `vite.config.js` 配置）把 `/api/*` 转给后端 8000，无需 CORS 配置。

## 常用命令

### 后端

```bash
uv run uvicorn main:app --reload          # 热重载开发
uv run python -c "import main; print(len(main.app.routes))"  # 静态校验导入
```

### 前端

```bash
npm run dev      # 开发服务器
npm run build    # 生产构建到 dist/
npm run preview  # 预览生产构建
```

## API 概览

交互文档见 http://localhost:8000/docs 。金额单位一律「元」，收益率为百分数（`4.5` = 4.5%）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET / POST | `/api/portfolios` | 组合列表 / 新增（名称 1–50 字，自动去首尾空白，纯空白 → 422） |
| PUT / DELETE | `/api/portfolios/{pid}` | 组合改名（同样是 1–50 字）/ 删除（级联删除其资产与目标） |
| GET | `/api/portfolios/{pid}/summary` | 组合汇总（总市值、加权 TTM 收益率、预计年收入、成本盈亏） |
| GET / POST | `/api/portfolios/{pid}/assets` | 资产列表 / 新增 |
| PUT / DELETE | `/api/assets/{aid}` | 资产更新 / 删除 |
| POST | `/api/assets/{aid}/refresh-price`、`/api/portfolios/{pid}/assets/refresh-all-prices` | 刷新实时价格 |
| **GET / POST** | **`/api/portfolios/{pid}/goals`** | **年收入目标列表（created_at ASC, id ASC）/ 新增（201）；同组合重名 409** |
| **PUT / DELETE** | **`/api/goals/{gid}`** | **目标更新（部分更新语义）/ 删除** |
| **GET** | **`/api/portfolios/{pid}/goals/{gid}/progress?assumed_yield=4.5`** | **目标进度：当前 / 目标年收入与月均、缺口、进度、两条达成路径；`assumed_yield` 为可选假设加仓收益率（百分数，`0–100`，省略则取组合加权 TTM）** |

目标接口的错误约定：组合不存在 → `404 Portfolio not found`；目标不存在或不属于该组合 → `404 Goal not found`；名称为纯空白 / 金额 ≤ 0 / `assumed_yield` 越界 → `422`；同组合重名 → `409 同名目标已存在`。
所有「无意义」的数值（如市值为 0 时的所需收益率）返回 `null` 而不是 `0`，前端显示「—」。

### 收入日历

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portfolios/{pid}/calendar?year=2026` | 年度日历：恒 12 个月（合计 / 笔数 / `by_status` / `by_source` / `estimated_amount`）+ `total_year` / `total_count` / `asset_count`；`recorded_total` = 已收录、`estimated_total` = 去年同期预估（`total_year = recorded_total + estimated_total`）；`rolling_12m_total` = 近 12 个月已到账合计（口径与概览「预计年收入」一致）；`year` 省略则取当年 |
| GET | `/api/portfolios/{pid}/calendar/{year}/{month}` | 单月逐笔明细（`items[]`：标的、派息日、每股分红、持股数、金额、状态、来源、是否已确认 / 补录 / 模拟价 / **预估** `is_estimated`） |
| POST | `/api/portfolios/{pid}/dividends/confirm` | body `{code, ex_date}` → 把该笔标成已到账（**幂等**，重复调用不改 `confirmed_at`、不加金额）；找不到该笔 → 404 |
| POST | `/api/portfolios/{pid}/dividends/manual` | body `{code, ex_date, amount_per_share, note?}` → 201 新增一笔补录；未来日期 / 金额 ≤ 0 / 标的不在组合内 → 422 |
| GET | `/api/portfolios/{pid}/dividends/manual` | 该组合的全部补录记录 |
| DELETE | `/api/portfolios/{pid}/dividends/manual/{record_id}` | 删除补录；记录不存在或不属于该组合 → 404 |

状态口径：`paid`（akshare `IMPLEMENT_STATUS` 含「已实施」）/ `announced`（含「已发布」「实施中」）/ `predicted`（其余，含缺失，保守）/ `manual`（补录）。
来源口径：`manual` → 补录；ETF/基金代码 → `etf`；`asset_type` 含「债」/「OTC」/「REITs」/「自建」/「自定义」→ `bond`/`otc`/`reit`/`self`/`custom`；其余 → `stock`。
金额 = 持股数 × 该期每股分红；同 `(code, ex_date)` 时补录**覆盖** akshare 记录；`cost_price` 不参与本功能。
**去年同期预估**：把最近 12 个月内的每一笔真实记录 +1 年投影到未来月份（月份严格晚于当前月、且该标的该月没有真实记录时才生成），`is_estimated=true`、状态记为 `predicted`，只计入 `total_year` / `estimated_total`，**不**计入 `rolling_12m_total`（已到账口径），也不能「点击确认」。

### 观察池

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/watchlist` | 观察项列表（`created_at ASC, id ASC`）。**只读库，绝不触发外部抓取** |
| POST | `/api/watchlist` | 新增（201）。`code` 归一化后必须是 6 位数字；同池重码 → `409 该标的已在观察池中`；**不自动抓价** |
| PUT | `/api/watchlist/{id}` | 部分更新：字段为 `null` 则不改；`target_price=0` 表示**清空**（存 `NULL`） |
| DELETE | `/api/watchlist/{id}` | 删除并级联删除其全部快照 → `{"message": "Watchlist item deleted"}` |
| POST | `/api/watchlist/refresh` | 逐条抓取并 UPSERT 当天快照，返回刷新后的完整列表；单条异常吞掉继续下一条，**永不因外部数据源失败返回 5xx** |
| POST | `/api/watchlist/{id}/refresh` | 单条刷新（同上，失败时返回上次数据 + `refresh_error`） |
| GET | `/api/watchlist/{id}/snapshots` | 快照序列（`trade_date ASC`）。三种取法，优先级从高到低：`?all=true`（全部历史）/ `?start=&end=`（自定义闭区间，任一端可省略）/ `?days=N`（最近 N 天，默认 90、范围 1–730，越界 → 422）。`start > end` → `422 起始日期不能晚于结束日期` |
| GET | `/api/watchlist/{id}/history-range` | 该标的**已有快照**的区间 `{ first_date, last_date, count }`（无数据时前两者为 `null`、`count=0`）。只读库，供前端给日期选择器设 min/max |
| GET | `/api/watchlist/{id}/dividends` | **除权（派息）日** `[{ ex_date, dividend_per_share }]`（`ex_date ASC`）。取法同 `/snapshots`：`?all=true` / `?start=&end=` / `?days=N`（默认 365）。只用于走势图打竖线，**不写库**；外部失败返回 `200 []`，不报 5xx |

错误约定：观察项不存在 → `404 Watchlist item not found`；`target_yield` 缺失 / ≤0 / >30、`target_price < 0`、`name` 纯空白或 >50 字、`code` 归一化后不是 6 位数字、`days` 越界 → `422`。

计算口径（**只在后端算**，`watchlist_math.py` 是唯一实现；前端不做任何阈值比较）：

```
current_yield             = ttm_dividend / price * 100                 # price>0 且 ttm>0，否则 null
yield_trigger_price       = ttm_dividend / (target_yield / 100)        # ttm<=0 -> null（显示「—」）
yield_gap_points          = current_yield - target_yield               # 百分点差，任一 null -> null
yield_trigger_gap_percent = (price - yield_trigger_price) / yield_trigger_price * 100   # 负数 = 现价已低于触发价
price_gap_percent         = (price - target_price) / target_price * 100 # 未设目标价 -> null

yield_signal: null -> unknown；>= 目标 -> hit；>= 目标×0.97 -> near；其余 -> watch
price_signal: 未设目标价 -> not_set；无价格 -> unknown；<= 目标 -> hit；<= 目标×1.03 -> near；其余 -> watch
is_stale    : 今天(Asia/Shanghai) − snapshot_date > 5 个自然日；无快照 -> false / stale_days = null
```

- 快照表 `watchlist_snapshots` 上 `(item_id, trade_date)` **唯一**，同日重复刷新按「先查后写」UPSERT 覆盖，不会新增行（不依赖 SQLite 的 `ON CONFLICT`，便于换库）。
- 分红抖动保护：本次抓到的 TTM 分红为 0、而该标的历史最新快照 `ttm_dividend > 0` 时**沿用历史值**，避免数据源抖动被误判成「突然不分红」。

#### 历史快照回填

观察池的日快照从「加入当天」才开始写，所以新加的标的展开走势只有一个点。为了让历史可见，`watchlist_history.py` 会在**每次刷新时**（定时任务或手动「刷新全部」都算）用真实数据把缺口补上 —— 默认一直补到**上市日**：

| 字段 | 来源 | 口径 |
|---|---|---|
| `price` | 腾讯日K `web.ifzq.gtimg.cn`（不复权，个股 / ETF 同一接口） | 当日**收盘价** |
| `ttm_dividend` | 复用 `_sum_trailing_12m`，把「今天」换成该交易日 | 该日往前滚动 12 个月的每股分红合计 |
| `ttm_yield` | 现算 `ttm_dividend / price * 100` | 两者都 > 0 才写，否则 `null` |
| `price_source` | 固定 `real` | — |

- 范围由 `WATCHLIST_BACKFILL_FULL` 控制：`true`（默认）补到**上市日**；`false` 退回只补最近 `WATCHLIST_BACKFILL_DAYS` 天（默认 365）。`WATCHLIST_BACKFILL_ENABLED=false` 可整体关闭（**测试环境默认关闭**）。
- **一次请求装不下全历史**：腾讯日K单次最多回 **2000 根**（3000 即 `param error`），交通银行这种 2007 年上市的标的有 4700+ 个交易日。因此走 `kline/kline`（支持 `code,day,start,end,count` 区间，`fqkline/get` 会忽略区间只回最近 N 根）从 `end` 往前**翻页**，直到翻到上市日或整页不足 2000 根（≈ 8 年/页，最多 8 页）。
- **只补缺、不覆盖**：已存在的 `trade_date` 一律跳过 —— 当天那条实时快照（可能是 15:00 之后的价）不会被日K收盘价盖掉。
- **不重复翻页**：进程内用 `_EARLIEST_BAR[code]` 记住「已拉到哪一天」，命中即跳过；进程重启后每个标的会重新探测一次（1–3 个日K请求，约 0.5s），之后同进程内不再请求。这是一条**性能缓存**而非数据，丢了不影响正确性。
- **只用真实数据**：日K 拉不到就整段放弃（返回空、记 warning），绝不插值、绝不用 mock 补点；分红的 `TTM` 拉不到时价格历史照写、股息率记 `null`（`null` = 算不出，不是 0）。
- 不走 akshare：日K 是纯 HTTP（`httpx` 已在用），不用抢 `_akshare_lock`，也没有 mini_racer 多线程崩溃风险。

#### 走势时间维度

展开行的走势图右上有 5 个档位：**30 天 / 90 天 / 180 天 / 全部 / 自定义**。

- `30 / 90 / 180` → `GET .../snapshots?days=N`，日常看水位用；
- `全部` → `GET .../snapshots?all=true`，上市日至今（依赖上面的全历史回填），横轴标签自动切成 `YYYY-MM`；
- `自定义` → 两个 `<input type="date">` 选起止日期，点「应用」才发请求（改日期不会偷偷换图）。默认区间是**最近 3 年**（历史不足 3 年时给全部），日期选择器的 min/max 来自 `GET .../history-range`，选不出没数据的日子。
- 标题右侧实时显示**当前曲线实际覆盖的范围**：`2007-05-15 ~ 2026-11-01 · 4,716 个交易日`。长区间最容易出的错觉是「图上怎么只有一条直线」，把首尾日期和点数摊开写，一眼能确认「全部」是不是真的取到了上市日。
- 切档位有请求令牌防抖：连续点 30 → 全部 → 180，慢的那次响应不会盖住后发的请求。
- 除权日只拉一次 `?all=true`，之后切任何区间都在前端本地裁剪，不再请求。

#### 除权日竖线（解释股息率曲线的台阶）

走势图上的淡灰竖线标出**除权日**，鼠标悬停当天还会多一行「除权日 · 每股派 ¥x.xxx」。

为什么需要它：TTM 股息率的定义是「**该日往前滚动 12 个月**的每股分红合计 ÷ 现价」。除权日当天有一期分红进入这个窗口，一年后同一期又会滚出窗口 —— 所以股息率曲线在除权日会出现**台阶式跳变**（不是价格异常，也不是数据错误）；ETF 的月度分红则表现为每月一个小台阶。

- 竖线颜色 `#cbd5e1`（`utils/watchlistColors.js` 的 `MARK_COLORS`），刻意比两条曲线更淡：它只解释数据，不参与任何判断。
- 除权日**只从分红记录里取**，不由快照反推 —— 快照里 `ttm_dividend` 的变化既可能是「换入」也可能是「滚出」，无法区分。
- 只在窗口内画：30 天窗口里一期除权都没有时，连「浅灰竖线 = 除权日」的提示一起隐藏。
- 除权日那天若正好缺快照（停牌 / 回填缺日），竖线顺延到之后最近的一个有数据的交易日；早于窗口起点的除权日直接丢弃，避免在左边缘凭空多出一条线。
- 长区间上偶尔会冒出一根很高的尖峰（交通银行 2014 / 2017 / 2019 / 2021 年都被顶到 9%~15%）：那是**相邻两期年报的除权日相隔不足 365 天**，两期同时落在「往前 365 天」这个闭区间里 —— 是 TTM 口径的边界现象，等旧一期滚出窗口（通常几天内）就自动回落，不是数据错误。尖峰正好落在除权竖线上，可以对照 tooltip 里的「除权日 · 每股派」自行验证。
- 日K 用**不复权**价：送转股当天价格会按比例跳水（交通银行 2011 年是 10 送 1），对应日期的股息率会相应抬升。用前复权反而会把历史价按分红整体下折，算出系统性偏高的股息率，所以这里刻意选择不复权 + 当期实际分红。

#### 定时快照任务

- 实现：`portfolio_api/watchlist_scheduler.py`，在 `main.py` 的 `lifespan` 里 `asyncio.create_task` 启动，关闭时 `cancel()` + `await` 收尾。**零新依赖**（标准库 `asyncio` + `zoneinfo`，不引入 APScheduler / celery）。
- 每 60 秒一个 tick，且**启动即 tick**：服务在 15:30 之后启动会立刻补抓当天缺失的快照，不用等到第二天。
- 默认 `15:30`（Asia/Shanghai）；`WATCHLIST_SNAPSHOT_TIME=09:00` 覆盖；`WATCHLIST_SNAPSHOT_ENABLED=false` 整体关闭（**测试环境默认关闭**，不起后台任务、不发真实网络请求）。时间串解析失败时记 warning 并回退 15:30，不会启动即崩。
- 同一 `trade_date` 最多尝试 **3 轮**，避免数据源整体挂掉时每分钟重试一整天。
- **单 worker 限制**：`uvicorn --workers N`（N > 1）时每个进程都会跑这个循环 —— 同一天的快照靠唯一约束 + UPSERT 幂等，不会写重，但会**重复抓取**同一标的。本期不做跨进程锁，生产部署请用单 worker，或把定时抓取挪到外部调度。

#### 与「投资组合」页共用的视觉骨架

观察池和投资组合是同一套设计语言，**样式定义只有一份**，都在 `src/styles/main.css`：

| 区块 | 共用类 | 两页的落点 |
|---|---|---|
| 顶部 KPI 卡 | `.overview-grid` / `.overview-item`（`.highlight` 绿、默认蓝渐变） | 资产总额、加权收益率… ｜ 标的总数、股息率达标… |
| 卡片骨架 | `.card` + `.card-header` + `.card-title` + `.card-actions` | 资产列表 / 年收入目标 ｜ 观察标的 |
| 次级按钮 | `.btn-ghost`（透明底 + 描边） | 列设置 ｜ 刷新全部 |
| 表格 | `.data-table`（14px、12px 内边距、浅底表头、悬停高亮） | 资产列表 ｜ 观察标的 |
| 行内操作 | `.btn.btn-secondary.btn-sm` / `.btn.btn-danger.btn-sm` | 编辑 / 删除 ｜ 刷新 / 转入持仓 / 编辑 / 删除 |
| 加载 / 空态 | `.loading` / `.empty-state` / `.empty-hint` | 两页一致 |

只有「这张表特有」的东西留在 `WatchlistView.vue` 的 scoped 样式里：陈旧行的黄条、两条信号徽标、除权日图例、时间维度切换、自定义区间选择器。

两处刻意统一的交互细节：

- **展开入口**：观察池点「标的名称」展开走势，用的是资产列表点名称展开分红历史的同一个 ▶/▼ 手势，因此操作列不再单独占一个「走势」按钮，四条按钮的宽度与资产列表的两条按钮同一密度。
- **类型徽标**：只在非「股票」时打标（ETF 橙 / 其他灰），尺寸与资产列表的 `.asset-type-badge` 完全一致，`已持有` 标签也用同一套圆角与字号，并排时不打架。


## 数据源说明

| 数据 | 来源 | 备注 |
|---|---|---|
| 标的代码表（搜索用） | akshare | 个股 `stock_info_a_code_name` + ETF/LOF/封基 `fund_etf_category_sina`，合并缓存到 `stock_cache.json`（约 7 700 条） |
| 实时价格 | 腾讯行情 `qt.gtimg.cn` | 失败时返回模拟价格并标记 `source=mock` |
| 个股历史分红 | akshare `stock_dividend_cninfo` | 字段名按 akshare 当前版本硬编码 |
| ETF 历史分红 | akshare `fund_etf_dividend_sina` | 返回**累计分红**，代码内做相邻差分还原每期金额 |

`source` 字段约定（参见 `schemas.AssetResponse`）：

- `real`：腾讯行情返回的真实价
- `mock`：网络失败时的兜底价（前端应高亮提示）
- `stale`：尚未刷新过价格

### 价格显示精度

价格**不能一律保留 2 位小数** —— 交易所的最小价格变动不同：

| 标的类型 | 最小变动 | 显示位数 | 例 |
|---|---|---|---|
| 股票 | 0.01 | 2 位 | 8.57 |
| 场内 ETF / LOF / 基金 | 0.001 | 3 位 | 1.523 |

按 2 位显示 ETF 会把 `1.523` 写成 `1.52`（差 0.003 元，12 500 份就是 37.5 元）。口径统一收在 `utils/format.js` 的 `formatPrice(n, assetType)`：

- 位数由 `asset_type` 决定（非「股票」即 3 位）；**类型优先**，因为触发价是 `TTM DPS ÷ 目标股息率` 算出来的，天然带一长串小数，若按数值精度判断会让股票的 42.86 变成 42.857；
- 类型缺失（老数据 / 手工录入未带）时退回按数值精度判断，宁可多一位也不显示错的价格；
- 数据库存全精度原值，不四舍五入；缩进只在展示层做。

涉及位置：资产列表「最新价」、观察池「现价 / 触发价 / 目标价」、走势图价格轴与 tooltip。金额（市值、盈亏、年收入）仍固定 2 位，成本固定 3 位，口径不变。

`AssetForm` 的价格输入框 `step` 也必须是 `0.001`：设成 `0.01` 时浏览器会把 `1.523` 判为非法值并阻止提交。

## 重置数据

```bash
# 清空所有数据回到种子状态
rm portfolio_api/portfolio.db portfolio_api/stock_cache.json
# 重启后端即重新种子
```

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: akshare` | manifest 漏装 | `uv add akshare` |
| 后端首次调 `/assets` 很慢 | akshare 拉分红数据 | 正常现象，后续走缓存 |
| 股票搜索返回空 | akshare 首次拉全量代码表失败 | 看后端日志，重启后端会重试 |
| `npm run dev` 报 `vite: command not found` | node_modules 是错平台二进制 | `rm -rf node_modules package-lock.json && npm install` |
| 前端调后端 CORS 报错 | 没用 vite proxy | 检查 `vite.config.js` 中 `server.proxy['/api']` 是否存在 |

## 目录结构

```
portfolio_api/
├── main.py             # FastAPI 入口 + 种子数据 + /health
├── config.py           # pydantic-settings 配置（.env）
├── database.py         # SQLAlchemy
├── models.py           # ORM
├── schemas.py          # Pydantic
├── portfolio_math.py   # ★ 组合口径唯一实现（市值 / 年收入 / 加权 TTM / mock 标记 / 分红状态与来源归类）
├── watchlist_math.py   # ★ 观察池口径唯一实现（当前股息率 / 触发价 / 两条信号 / 陈旧判定，纯函数）
├── watchlist_scheduler.py  # ★ 观察池每日快照定时任务（asyncio + zoneinfo，零新依赖）
├── watchlist_history.py    # ★ 观察池历史回填（腾讯日K × 当日滚动 12 个月分红 → 历史快照）
├── market.py           # ★ 代码规则唯一来源（市场前缀 / 是否 ETF）
├── routers/
│   ├── portfolios.py   # 组合 CRUD + summary
│   ├── assets.py       # 资产 CRUD + 刷新价格 + TTM 计算
│   ├── goals.py        # 年收入目标 CRUD + 进度计算
│   ├── calendar.py     # 收入日历：按派息日归月 + 确认 / 补录
│   ├── watchlist.py    # ★ 观察池 CRUD + 每日快照 UPSERT + 走势序列
│   └── stocks.py       # 搜索 / 价格 / 分红（个股 + ETF 分派，含分红 in-memory 缓存）
├── tests/
│   ├── conftest.py
│   ├── test_api.py     # 通用 API 测试
│   ├── test_goals.py   # 年收入目标：CRUD / 校验 / 归属 / 级联 / 进度口径
│   ├── test_calendar.py          # 收入日历：聚合 / 状态映射 / 确认幂等 / 补录 / 边界
│   ├── test_portfolio_manage.py  # 组合新增 / 重命名 / 删除：名称校验与级联
│   ├── test_watchlist.py         # ★ 观察池：CRUD / 校验 / 两条信号 / 快照 UPSERT / mock 不落库 / 历史回填
│   └── test_etf.py     # ETF 支持专项测试
└── pyproject.toml      # uv 依赖

portfolio_web/
├── vite.config.js      # 含 /api proxy
├── scripts/
│   └── verify-chart-tooltip.mjs  # 用 CDP 验证饼图悬浮提示
└── src/
    ├── App.vue                   # 应用外壳：三个 tab（组合 / 日历 / 观察池）+ 组合选择器 + 组合管理
    ├── router.js                 # vue-router（hash 模式）：/、/calendar、/watchlist
    ├── views/
    │   ├── Portfolio.vue
    │   ├── CalendarView.vue      # ★ 收入日历（年/月视图 + 月 Sheet + 确认 / 补录）
    │   └── WatchlistView.vue     # ★ 观察池（KPI 汇总卡 + 「观察标的」卡片 + 表格 + 展开行双轴走势）
    ├── components/
    │   ├── PortfolioOverview.vue
    │   ├── PortfolioGoals.vue    # ★ 年收入目标区块（进度条 / 两条路径 / 假设收益率）
    │   ├── PortfolioCharts.vue   # 两张分布饼图（容器）
    │   ├── ChartPie.vue          # 可复用饼图组件（ECharts 封装）
    │   ├── YearGrid.vue          # ★ 年视图：Q1–Q4 × 3 月网格
    │   ├── MonthlyStackedBarChart.vue  # ★ 月视图：来源堆叠柱 + 年度累计折线（ECharts）
    │   ├── MonthSheet.vue        # ★ 月底部 Sheet：逐笔明细
    │   ├── ManualDividendForm.vue      # ★ 补录行内表单
    │   ├── WatchlistForm.vue     # ★ 观察标的新增 / 编辑弹窗（代码搜索复用 /api/stocks/search）
    │   ├── WatchlistTrendChart.vue     # ★ 展开行双轴折线（echarts，异步加载）
    │   ├── AssetList.vue
    │   ├── AssetItem.vue
    │   ├── AssetForm.vue         # 资产表单（新增了可选 props initialValues 供「转入持仓」预填）
    │   ├── GoalForm.vue          # 目标的新增 / 编辑弹窗
    │   ├── PortfolioForm.vue     # 组合的新增 / 重命名弹窗
    │   ├── Toast.vue
    │   └── InfoTooltip.vue
    ├── utils/
    │   ├── chartData.js          # 饼图数据整理（纯函数 + 单测）
    │   ├── chartData.test.mjs
    │   ├── goal.js               # 目标展示纯函数（月均 / 进度宽度 / 记忆选中）
    │   ├── goal.test.mjs
    │   ├── calendar.js           # 日历纯函数（分月分箱 / 状态与来源归类 / 累计折线 / 堆叠柱数据）
    │   ├── calendar.test.mjs
    │   ├── calendarColors.js     # 日历配色（8 类来源 + 状态点 + 选中格）
    │   ├── watchlist.js          # ★ 观察池展示纯函数（信号→文案/色、排序、序列、格式化、汇总）
    │   ├── watchlist.test.mjs
    │   ├── watchlistColors.js    # ★ 观察池信号色与曲线色（集中管理）
    │   └── chartColors.js        # 跨图共享配色
    ├── composables/
    │   ├── useToast.js
    │   └── usePortfolioContext.js  # 两个 tab 共享选中组合（provide/inject + localStorage）
    ├── api/
    │   ├── portfolio.js
    │   ├── goal.js               # 目标接口（无假数据兜底，透传后端 detail）
    │   ├── calendar.js           # 日历 / 确认 / 补录接口（同上）
    │   ├── watchlist.js          # ★ 观察池接口（无假数据兜底，透传后端 detail）
    │   └── stock.js
    └── styles/main.css
```

## 测试

```bash
# 后端：210 个用例（API / ETF / 历年分红口径 / 成本盈亏含负成本 / 刷新缓存时效 / 年收入目标 19 例 /
#       组合管理 6 例 / 收入日历 20 例 / 观察池 53 例）
cd portfolio_api && uv run pytest -q

# 前端：99 个用例（数字格式化 / 价格精度、饼图数据整理、TOP N 取数、悬浮提示、目标展示 18 例、
#       日历纯函数 15 例、观察池展示纯函数 19 例）
cd portfolio_web && npm test

# 浏览器端验证饼图悬浮提示（需本地安装 Chrome，可用 CHROME_PATH 覆盖路径）
cd portfolio_web && npm run verify:chart
```

## 待办

- 数据库迁移机制（Alembic）
- 测试覆盖（前端 vitest）
- 用户认证（多用户之前必须加）
- ETF 分红面板的「年均每股分红」对月度分红标的语义失真