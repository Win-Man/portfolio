# 003 · 观察池（Watchlist）

## 1. 一句话

给「还没买、但想买」的红利标的建一个全局候选池：每个标的设**目标股息率**（必填）与**目标价**（选填），系统每日收盘后自动抓价格与 TTM 每股分红落快照，页面展示当前指标、两条**互相独立**的达标信号、以及价格/股息率走势曲线，辅助判断何时买入。

## 2. 背景与现状（已在仓库中核对）

### 2.1 可复用的数据链路（本功能的技术前提）

| 能力 | 现有实现 | 是否依赖「已持有」 |
|---|---|---|
| 实时价格 | `routers/assets.py::fetch_real_time_price(code, current_price, dividend_per_share)` → 腾讯行情，失败降级 mock + `source` 字段 | **否**，只吃 code |
| TTM 每股分红（滚动 12 个月合计） | `routers/assets.py::fetch_ttm_dividend_per_share(code)` → 内部调 `stocks.fetch_dividend_from_eastmoney(code)` | **否**，只吃 code |
| 分红原始记录（含 in-memory 6h 缓存） | `routers/stocks.py::fetch_dividend_from_eastmoney` / `fetch_all_dividend_records` | **否** |
| 代码归一化 / 市场前缀 / ETF 判定 | `market.py::normalize_code` / `market_code` / `is_etf` / `guess_asset_type` | 否 |
| 标的搜索（代码或名称） | `GET /api/stocks/search` | 否 |

**结论**：观察池不需要先建资产记录，价格与 TTM 分红全部可以按 code 直接拉。不要为此改动 `assets` 表或 `AssetResponse`。

### 2.2 现有前端结构

- 路由：`src/router.js`，hash 模式，已有 `/`（Portfolio）与 `/calendar`（CalendarView）；`beforeEach` 只为 calendar 做「组合是否已选」的兜底。
- 外壳：`App.vue`，`header` 内 `<nav class="app-tabs">` 两个 `router-link` + 右侧 `header-actions`（组合选择器 + 组合增删改按钮）。`router-view` 包在 `<keep-alive>` 里。
- ECharts 已有封装：`components/ChartPie.vue`（饼图，`defineAsyncComponent` 引入）、`components/MonthlyStackedBarChart.vue`（堆叠柱 + 折线，双轴先例）。
- 数字格式化：`utils/format.js` 的 `formatAmount`（千分位 + 2 位小数，**不用万/亿**）、`formatCost`（3 位小数）、`formatSigned`（带 +/−）。
- 测试：`npm test` = `node --test src/utils/*.test.mjs`，纯函数放 `utils/` 并在同目录建 `*.test.mjs`。

### 2.3 现有约束

- 无 Alembic，新表靠 `main.py` 的 `Base.metadata.create_all` 自动建；`run_lightweight_migrations()` 只用于**补既有表的新列**，本功能不需要它。
- akshare 底层 mini_racer（V8）不支持多线程并发，所有 akshare 调用已被 `stocks.py::_akshare_lock` 串行化。**任何新增的分红拉取必须走 `fetch_ttm_dividend_per_share` / `fetch_dividend_from_eastmoney`，不得直接调 `ak.*`。**
- 服务以单 worker 运行（`uv run uvicorn main:app --reload`）。

## 3. 已锁定的决策（D1–D16，不得自行更改）

1. **D1 归属**：观察池是**全局**的，不绑定任何组合、不参与组合汇总。独立第三个 tab `#/watchlist`。
2. **D2 锚点**：每条观察项必须填**目标股息率**（百分数，如 `5.5` 表示 5.5%）；**目标价选填**。目标价不是必填，也不影响股息率线的判定。
3. **D3 双线独立**：股息率线与价格线**各自独立**给出信号，**不合并**成单一「可买入」结论。理由：两条线在时间上常常一前一后触发，合并会掩盖信息。UI 上并排两个 badge。
4. **D4 触发价**：`yield_trigger_price = ttm_dividend / (target_yield / 100)`，仅用于展示「按你的目标股息率，现价要跌到这个数」。TTM 分红 ≤ 0 时为 `null`，条款显示「—」并给出「无分红数据」提示。
5. **D5 快照**：每日收盘后由**后端定时任务**抓取，写入 `watchlist_snapshots`，同日唯一（`UNIQUE(item_id, trade_date)`），重复抓取按 UPSERT 覆盖。
6. **D6 抓取时间**：默认 `15:30`（`Asia/Shanghai`），可由 `WATCHLIST_SNAPSHOT_TIME` 覆盖；`WATCHLIST_SNAPSHOT_ENABLED=false` 可整体关闭（测试必须关闭）。
7. **D7 只落真实价**：**只有 `price_source == "real"` 才写快照**。腾讯行情失败降级 mock（±3% 随机价）时**不写库**，避免随机假价污染曲线与信号；该标的当天保留上一次快照，并在响应里给 `refresh_error` 说明。
8. **D8 分红抖动保护**：本次抓到的 TTM 每股分红为 0、而该标的历史最新快照的 `ttm_dividend > 0` 时，**沿用历史值**（数据源抖动比「突然不分红」常见得多），不写 0。
9. **D9 口径唯一**：`yield_signal` / `price_signal` / 所有派生数值**只在后端计算**，前端只做「信号 → 颜色/文案」映射与排序、序列构建。前端不得重算阈值。
10. **D10 金额与精度**：价格 2 位小数；股息率 2 位小数 + `%`；百分点差 2 位小数；金额一律 `formatAmount`（禁万/亿）。后端计算不四舍五入，`null` 表示「不适用」而不是 `0`。
11. **D11 已持有标记**：某 code 出现在**任意组合**的资产里时，该观察项 `is_held = true`，并回传 `held_portfolios`（组合名数组）。列表行显示「已持有」chip，**仍保留在列表里**，不自动删除、不隐藏。
12. **D12 转入持仓**：列表行提供「转入持仓」→ 跳回 `#/`，自动打开既有 `AssetForm` 并预填 `code` / `name` / `price`（`cost_price` 留空，由后端按现值兜底）。**不新增第二条资产写入链路**，不新建组合。
13. **D13 无新依赖**：后端只用标准库 `asyncio` + `zoneinfo`；前端复用已有 `echarts`。不引入 APScheduler / celery / vue-chartjs。
14. **D14 不改既有行为**：`assets` / `goals` / `calendar` 的接口、口径、测试断言全部不变；不改 `AssetResponse` / `PortfolioSummary`；不改 `main.css` 既有规则。
15. **D15 快照不插值**：没跑服务的日期就是缺数据，图表如实断开（`null` 断线），**不做前向填充**、不回填历史。
16. **D16 非交易日照常写**：周末与节假日也写快照（腾讯行情返回最近收盘价），保证曲线日期连续。

## 4. 目标 / 非目标

### 目标

- 全局候选清单的增删改查，每条含：代码、名称、类型、目标股息率、目标价、备注。
- 每日自动 + 手动刷新：实时价、TTM 每股分红、当前股息率，落每日快照。
- 列表展示：现价、TTM 分红、当前股息率、距目标（百分点）、触发价、现价距触发价、目标价、两个独立信号、数据日期、已持有标记。
- 每个标的可展开查看走势：价格（右轴 ¥）+ 股息率（左轴 %）双折线，时间窗 30 / 90 / 180 天。
- 顶部汇总：标的数、已达标数（分别统计两条线）、数据最新日期、是否含陈旧数据。
- 一键转入持仓（D12）。

### 非目标（明确不做）

- ❌ 不做推送 / 通知（邮件、IM、桌面通知、企业微信）——只在页面内高亮。
- ❌ 不做自动下单、券商对接。
- ❌ 不做 PE / PB / ROE / 分红连续性评分等基本面指标——本期只看价格与股息率。
- ❌ 不做观察池与组合的绑定、不做多观察池分组 / 标签 / 排序拖拽。
- ❌ 不做观察池列设置（列固定，横向可滚动）。
- ❌ 不做历史快照回填、不做数据补跑脚本。
- ❌ 不做多用户、不做权限。
- ❌ 不做 TTM 分红的「手动覆盖」字段（数据源异常时前端提示，用户可等次日或手动刷新）。
- ❌ 不引入 Alembic。

## 5. 数据模型

新表两张，由 `Base.metadata.create_all` 自动建，**不写 `run_lightweight_migrations` 条目**。

### 5.1 `watchlist_items`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Integer | PK, index | |
| `code` | String | NOT NULL, index | 纯 6 位数字，入库前经 `normalize_code`（`sh600900` → `600900`） |
| `name` | String | NOT NULL | 1–50 字，去首尾空白后非空 |
| `asset_type` | String | default `'股票'` | `'股票'` / `'ETF'`；缺省按 `guess_asset_type(code)` 推断 |
| `target_yield` | Float | NOT NULL | 百分数，`0 < v <= 30` |
| `target_price` | Float | NULL | 元，`> 0`；NULL = 未设 |
| `note` | String | default `''` | 1–200 字，可空 |
| `created_at` | DateTime | server_default now | |
| `updated_at` | DateTime | server_default now, onupdate now | |

关系：`snapshots = relationship("WatchlistSnapshot", back_populates="item", cascade="all, delete-orphan", order_by="WatchlistSnapshot.trade_date")`

**不加数据库级 `UNIQUE(code)`**（沿用 001 的先例：重码走 API 返可读的 409）。

### 5.2 `watchlist_snapshots`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Integer | PK, index | |
| `item_id` | Integer | FK → `watchlist_items.id`, NOT NULL, index | |
| `trade_date` | Date | NOT NULL | `Asia/Shanghai` 当天日期 |
| `price` | Float | NOT NULL | 写库时**只来自 `source == "real"`** |
| `ttm_dividend` | Float | NOT NULL, default 0 | 每股分红（滚动 12 个月合计） |
| `ttm_yield` | Float | NULL | `ttm_dividend<=0` 或 `price<=0` → NULL（不写 0） |
| `price_source` | String | default `'real'` | 当前实现恒为 `'real'`，字段保留以兼容未来 |
| `created_at` | DateTime | server_default now | |

`UniqueConstraint("item_id", "trade_date", name="uq_watchlist_snapshot_item_date")` —— UPSERT 的判重依据，**必须加**。

## 6. 后端 API 契约

### 6.1 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/watchlist` | 200 `[WatchlistItemResponse]`，排序 `created_at ASC, id ASC`。**只读库，不触发任何外部抓取** |
| POST | `/api/watchlist` | 201 `WatchlistItemResponse`。新增后不自动抓价（首次数据由按时刷新或手动刷新产生） |
| PUT | `/api/watchlist/{item_id}` | 200，**部分更新**语义（字段为 `null` 则不改） |
| DELETE | `/api/watchlist/{item_id}` | 200 `{"message": "Watchlist item deleted"}`，级联删快照 |
| POST | `/api/watchlist/refresh` | 200 `[WatchlistItemResponse]`，逐条抓取并 UPSERT 当天快照，返回刷新后的完整列表 |
| POST | `/api/watchlist/{item_id}/refresh` | 200 `WatchlistItemResponse`，只刷一条 |
| GET | `/api/watchlist/{item_id}/snapshots?days=90` | 200 `[WatchlistSnapshotResponse]`，按 `trade_date ASC`；`days` 默认 90，范围 `7–730`，越界 → 422 |

错误约定：

| 场景 | 状态码 | detail |
|---|---|---|
| 观察项不存在 | 404 | `Watchlist item not found` |
| code 已存在（同池内） | 409 | `该标的已在观察池中` |
| `target_yield` 缺失 / ≤0 / >30 | 422 | 校验信息 |
| `target_price` < 0 | 422 | `目标价必须大于 0`（`0` 表示清空，见 6.6） |
| `name` 纯空白 / >50 字 | 422 | `名称不能为空` |
| `code` 归一化后不是 6 位数字 | 422 | `代码格式不正确` |
| `days` 越界 | 422 | — |

刷新接口**永不因外部数据源失败返回 5xx**：失败降级见 6.4。

### 6.2 响应模型

```python
class WatchlistCreate(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=50)
    asset_type: Optional[str] = None          # None → guess_asset_type(code)
    target_yield: float                       # 0 < v <= 30
    target_price: Optional[float] = None      # None / 0 → 未设
    note: Optional[str] = ""

class WatchlistUpdate(BaseModel):
    # 部分更新：None = 不改
    name: Optional[str] = None
    asset_type: Optional[str] = None
    target_yield: Optional[float] = None
    target_price: Optional[float] = None      # 0 = 清空目标价（存 NULL）
    note: Optional[str] = None

class WatchlistItemResponse(BaseModel):
    # 基本信息
    id: int
    code: str
    name: str
    asset_type: str
    target_yield: float
    target_price: Optional[float]
    note: str
    created_at: datetime
    updated_at: datetime
    # 最新快照（无快照时全部为 None / 0）
    price: Optional[float] = None
    ttm_dividend: float = 0
    current_yield: Optional[float] = None
    snapshot_date: Optional[date] = None
    price_source: Optional[str] = None
    is_stale: bool = False          # snapshot_date 距今 > 5 个自然日
    stale_days: Optional[int] = None
    # 派生
    yield_trigger_price: Optional[float] = None
    yield_gap_points: Optional[float] = None          # current_yield − target_yield
    yield_trigger_gap_percent: Optional[float] = None # (price − trigger) / trigger × 100
    price_gap_percent: Optional[float] = None         # (price − target_price) / target_price × 100
    # 信号（互不合并）
    yield_signal: Literal["hit", "near", "watch", "unknown"]
    price_signal: Literal["hit", "near", "watch", "not_set", "unknown"]
    # 持仓关系
    is_held: bool = False
    held_portfolios: List[str] = []
    # 非持久化：本次刷新的错误说明（GET 时恒 None）
    refresh_error: Optional[str] = None

class WatchlistSnapshotResponse(BaseModel):
    trade_date: date
    price: float
    ttm_dividend: float
    ttm_yield: Optional[float]
    price_source: str
```

`yield_signal` / `price_signal` 的取值必须严格用上述字面量。

### 6.3 计算口径（严格按此实现，放新模块 `portfolio_api/watchlist_math.py`）

纯函数、不访问数据库、不抛业务异常，风格对齐 `portfolio_math.py`。所有 `None` 传播必须显式处理。

```
current_yield = ttm_dividend / price * 100          # price>0 且 ttm_dividend>0，否则 None
yield_trigger_price = ttm_dividend / (target_yield / 100)   # ttm_dividend<=0 → None
yield_gap_points = current_yield - target_yield      # 任一 None → None
yield_trigger_gap_percent = (price - yield_trigger_price) / yield_trigger_price * 100
price_gap_percent = (price - target_price) / target_price * 100   # target_price 为 None → None

# 股息率线（现价越低越好 → 股息率越高越好）
yield_signal:
    current_yield is None        → "unknown"      # 无价格 / 无分红
    current_yield >= target_yield                → "hit"
    current_yield >= target_yield * 0.97         → "near"   # 相对差 ≤3%
    else                                         → "watch"

# 价格线（独立判定）
price_signal:
    target_price is None         → "not_set"
    price is None                → "unknown"
    price <= target_price                        → "hit"
    price <= target_price * 1.03                 → "near"   # 高出 ≤3%
    else                                         → "watch"
```

注意 `near` 的两条阈值方向相反（股息率用 `×0.97`、价格用 `×1.03`），但语义都是「相对差 3% 以内」，**不要互相推导、不要合并判断**。

`is_stale` = `snapshot_date` 与「今天（Asia/Shanghai）」相差 **> 5 个自然日**（覆盖周末 + 小长假）；`stale_days` 为差值天数，无快照时两者分别为 `False` / `None`。

`current_yield` 一律**由 `price` 与 `ttm_dividend` 现算**，不直接读快照里的 `ttm_yield`（避免历史快照口径漂移；快照里的 `ttm_yield` 只用于画曲线）。

### 6.4 快照写入规则（`_refresh_one(db, item)`，定时任务与两个 refresh 端点共用这一份实现）

```
1. price_data = await fetch_real_time_price(item.code, current_price=<上一条快照 price 或 0>)
2. ttm = await fetch_ttm_dividend_per_share(item.code)
3. 分红抖动保护（D8）：if ttm == 0 and 上一条快照.ttm_dividend > 0: ttm = 上一条快照.ttm_dividend
4. if price_data["source"] != "real":
       # D7：不写库
       记录 refresh_error = "行情获取失败，已保留上次数据"
       返回（快照不变）
5. today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
   yield_val = ttm / price * 100 if (ttm > 0 and price > 0) else None
   UPSERT watchlist_snapshots WHERE (item_id=item.id, trade_date=today)
       → 存在：更新 price / ttm_dividend / ttm_yield / price_source
       → 不存在：插入
6. refresh_error = None
```

- 顺序必须是**先分红、后价格**，否则价格没问题但分红拉取失败时会写出一条 `ttm_dividend=0` 的脏快照，把当天的 `yield_signal` 直接打成 `unknown`。
- UPSERT 用「先查后写」的 ORM 实现，**不要用 SQLite 方言的 `ON CONFLICT`**（保持可换库）。
- `POST /api/watchlist/refresh` 逐条调用 `_refresh_one`，单条异常 `try/except` 捕获后继续下一条，不整体 500。

### 6.5 定时任务（`portfolio_api/watchlist_scheduler.py`）

- 在 `main.py` 的 `lifespan` 里 `asyncio.create_task(...)` 启动，`yield` 之后 `cancel()` 并 `await` 收尾（吞掉 `CancelledError`）。
- 循环体：**先 tick 一次，再 `await asyncio.sleep(60)`**（启动即补抓，不等一分钟）。
- 每次 tick：
  1. `now = datetime.now(ZoneInfo("Asia/Shanghai"))`
  2. 若 `now.time() < 配置时间` → 返回
  3. 查「今天缺快照的 item」= 所有 item 减去「`trade_date = today` 已有快照的 item_id」
  4. 没有缺失 → 返回
  5. 对缺失项逐条跑 `_refresh_one`
- **重试退避**：同一 `trade_date` 最多尝试 3 轮；用模块级 `_attempt_state: dict[date, int]` 记录，超过即当天放弃（避免外部源挂掉时每分钟重试）。
- 配置项加在 `config.py`：`watchlist_snapshot_enabled: bool = True`、`watchlist_snapshot_time: str = "15:30"`。
- 时间串解析失败（如 `"25:99"`）→ 记 warning 并回退 `15:30`，**不允许启动即崩**。
- 单 worker 假设：`--workers N > 1` 时每个进程都会跑，同日 UPSERT 幂等但会重复抓取；本期不处理，在 README 注明。

### 6.6 关键边界

| 场景 | 期望行为 |
|---|---|
| 观察池为空 | `GET /api/watchlist` → `200 []`；前端空态引导 |
| 新增后从未刷新 | `price = null`、`current_yield = null`、`yield_signal = "unknown"`、`price_signal` 按是否设目标价取 `not_set` / `unknown` |
| 标的无任何分红记录 | `ttm_dividend = 0` → `yield_trigger_price = null`、`yield_signal = "unknown"`；前端提示「无分红数据」，**不要显示 0% 触发价** |
| 行情失败（mock） | 不写快照（D7），该行展示上次数据 + `refresh_error` 文案 |
| 首次刷新即行情失败 | 仍无快照，`price = null`，前端显示「—」+「待抓取」 |
| `target_price = 0`（PUT） | 视为清空，存 `NULL`，`price_signal = "not_set"` |
| `target_price` 存在且 `price` 为空 | `price_signal = "unknown"`，`price_gap_percent = null` |
| 标的同时在多个组合持有 | `held_portfolios` 返回多个组合名（按组合 id 升序去重） |
| 删除观察项 | 级联删除其全部快照；组合与资产不受影响 |
| 代码已存在于资产但不在观察池 | 允许加入观察池，标记 `is_held = true` |
| 代码归一化 | `sh600900` / `SH600900` / `600900` 入库统一为 `600900`，查重按归一化后的值 |
| 快照 6 天未更新 | `is_stale = true`，前端行首加「数据陈旧」提示 |

## 7. 前端实现

### 7.1 路由与 header

- `src/router.js` 新增 `{ path: '/watchlist', name: 'watchlist', component: WatchlistView }`。`beforeEach` **不加**观察池分支（它不需要组合）。
- `App.vue` 的 `<nav class="app-tabs">` 增加第三个 `router-link`：`投资组合 | 收入日历 | 观察池`。
- `App.vue` 的 `<div class="header-actions">` 加 `v-if="!isWatchlistTab"`：观察池是全局的，**不显示组合选择器与组合增删改按钮**。
- `views/Portfolio.vue` 需支持 `?addCode=&addName=&addPrice=` 预填（见 7.5）。

### 7.2 组件树

```
views/WatchlistView.vue           # 页面：工具条 + 汇总 + 表格 + 展开行图表
components/WatchlistForm.vue      # 新增 / 编辑弹窗（代码搜索复用现有搜索接口）
components/WatchlistTrendChart.vue # 展开行内的双轴折线（echarts，defineAsyncComponent）
api/watchlist.js                  # 无 mock 兜底；非 2xx 解析 detail 后 throw
utils/watchlist.js                # 纯函数：信号→文案/颜色、排序、序列构建、格式化
utils/watchlist.test.mjs
utils/watchlistColors.js          # 信号色与曲线色（集中，不散落）
```

### 7.3 视图规范

**工具条**（页面顶部，一行）：标题「观察池」+ 副标题「候选标的 · 达标信号每日收盘后更新」；右侧「刷新全部」（加载中禁用 + 转圈）与「＋ 新增标的」。

**汇总条**（四张小卡，等宽）：
1. 标的总数
2. 股息率达标数（`yield_signal === 'hit'`）
3. 价格达标数（`price_signal === 'hit'`，未设目标价的不计入分母）
4. 数据日期（取最新 `snapshot_date`；含陈旧数据时附「含 N 项陈旧」）

**表格**（`table` + `overflow-x: auto`，列固定，可横向滚动）：

| 列 | 内容 |
|---|---|
| 标的 | 名称（主）+ 代码（次，灰）+「已持有」chip（hover 显示组合名） |
| 现价 | `formatAmount(price)`；空则「—」 |
| 日涨跌 | 无（快照不存昨收，**不做**日涨跌，见非目标） |
| TTM 分红 | `formatCost(ttm_dividend)`（3 位小数）；`0` 显示「无分红」灰字 |
| 当前股息率 | `x.xx%`；null → 「—」 |
| 目标股息率 | `x.xx%` |
| 距目标 | `formatSigned(yield_gap_points)` + 「pct」；正数表示已超过目标 |
| 触发价 | `formatAmount(yield_trigger_price)`；null → 「—」 |
| 距触发价 | `formatSigned(yield_trigger_gap_percent)` + `%`；负数 = 现价已低于触发价 |
| 目标价 | `formatAmount(target_price)`；未设 → 「未设」灰字 |
| 信号 | 两个 badge 并排：`股息率 · 已达标` / `价格 · 已达标`，见 7.4 |
| 操作 | 「走势」（展开/收起图表）、「转入持仓」、「编辑」、「删除」（二次确认） |

**展开行**：点击「走势」在下方插入一行 `colspan` 满宽的 `WatchlistTrendChart`，高度 220px，一次只展开一个（再点收起）。图表上方有时间窗 `30 / 90 / 180 天` 三段切换（默认 90），切换时重新拉 `GET /api/watchlist/{id}/snapshots?days=N`。

**空态**：图标 + 「还没有观察标的」+ 「先去 /api/stocks/search 搜一个你想买的红利标的」+ 主按钮「＋ 新增标的」。

**加载 / 错误态**：首次加载显示骨架行；`GET` 失败显示错误条 + 「重试」按钮（不做假数据兜底）。

### 7.4 视觉与颜色

- 信号配色**集中在 `utils/watchlistColors.js`**，语义是「是否达标」，**与红涨绿跌无关**（沿用 `PortfolioGoals` 里「已达成 = 绿」的既有约定）：

| 信号 | 色 | 文案 |
|---|---|---|
| `hit` | `#059669`（绿底浅色填充 + 深绿字） | `已达标` |
| `near` | `#f59e0b`（琥珀） | `接近` |
| `watch` | `#94a3b8`（灰） | `观察中` |
| `unknown` | `#cbd5e1`（浅灰，虚边框） | `数据不足` |
| `not_set` | `#e2e8f0`（极浅灰） | `未设目标价` |

- 曲线配色：价格线 `#2563eb`（右轴 ¥），股息率线 `#059669`（左轴 %），目标股息率参考线 `#f59e0b` 虚线（`markLine`，仅当所有点都有值时画水平线）。
- 陈旧数据：行首 3px 左侧条 `#f59e0b`，并在「数据日期」列加 `⚠`。
- 组件用 `<style scoped>`；**不得修改** `main.css` 既有的 `.overview-*` / `.profit-*` / `.app-*` 规则；新增的样式类统一加 `wl-` 前缀。

### 7.5 转入持仓交互（D12）

`WatchlistView` 点「转入持仓」：

```js
router.push({ path: '/', query: {
  addCode: item.code,
  addName: item.name,
  addPrice: item.price ?? '',
  from: 'watchlist',
} })
```

`views/Portfolio.vue`：

- 该视图在 `<keep-alive>` 中，`onMounted` 只跑一次 → 用 **`onActivated` + `watch(() => route.query, ..., { immediate: true })`** 双保险。
- 当 `addCode` 存在：确保组合已选中（`selectedPortfolioId` 为空时落到第一个组合；一个组合都没有时 toast 提示「请先新建一个组合」并中止），然后打开既有 `AssetForm`，预填 `code` / `name` / `price`，`quantity` 留空待填、`cost_price` 留空。
- 打开后**立即** `router.replace({ path: '/' })` 清掉 query，防止刷新页面重复弹窗。
- 不新增任何资产写入逻辑——仍由 `AssetForm` 提交到既有 `POST /api/portfolios/{pid}/assets`。

## 8. 边界与异常清单（实现者逐条核对）

1. 观察池为空 → 页面空态，无报错。
2. 新增时 code 重复（含只差 `sh` 前缀）→ toast 显示后端 `409` 的 `detail`，弹窗不关闭。
3. 新增时 `target_yield` 填 `0` / 负数 / `>30` / 空 → 前端先拦（红字提示），后端 422 兜底。
4. 目标价留空保存 → `target_price = null`；把已有目标价清空后再保存 → 提交 `0`，后端存 `NULL`。
5. 刷新全部时部分标的行情失败 → 成功的行更新，失败的行保留旧值并显示「行情获取失败，已保留上次数据」，**整体仍 200**。
6. 首次刷新即失败 → 行内价格「—」+「待抓取」，不显示 0。
7. 标的分红数据缺失 → 触发价「—」，股息率信号 `数据不足`，hover 显示「无分红数据，无法按股息率定价」。
8. 快照陈旧（>5 天）→ 行首黄条 + 「⚠ 数据 N 天前」。
9. 删除观察项 → 二次确认；确认后行消失，图表若正展开则一并收起。
10. 转入持仓时无任何组合 → toast 提示，不跳转不弹表单。
11. 转入后返回观察池 → 该行出现「已持有」chip（重新 `GET` 列表即可，无需额外接口）。
12. 定时任务在测试环境必须关闭（`WATCHLIST_SNAPSHOT_ENABLED=false`），不得产生后台任务或网络请求。
13. `days` 传 `0` / `10000` / 非数字 → 422，前端不崩。
14. 曲线只有 1 个点 → 正常渲染（单点），不报错；0 个点 → 显示「暂无快照数据」。

## 9. 实现步骤拆解（write scopes）

按顺序做，每步可独立验证：

**Step 1 · 后端数据层**
`models.py`（两表）→ `schemas.py`（4 个模型）→ `watchlist_math.py`（纯函数）→ `config.py`（两个配置项）。
验证：`uv run python -c "import main"` 不报错，库里出现两张新表。

**Step 2 · 后端 API（不含 refresh）**
`routers/watchlist.py` 的 GET / POST / PUT / DELETE + `main.py` 注册 router。
验证：`POST` 一条 → `GET` 能读到，派生字段符合 6.3 的 null 传播规则。

**Step 3 · 刷新与快照**
`_refresh_one` + 两个 refresh 端点 + `GET .../snapshots`。
验证：mock 掉 `fetch_real_time_price` 返回 real/mock，断言写库/不写库两种分支；同日重复 refresh 不新增行。

**Step 4 · 定时任务**
`watchlist_scheduler.py` + `lifespan` 接线。
验证：把 `WATCHLIST_SNAPSHOT_TIME` 设为 `00:00`，启动后 2 秒内应产生当天快照；设 `ENABLED=false` 时不产生。

**Step 5 · 前端纯函数**
`utils/watchlist.js` + `watchlistColors.js` + `watchlist.test.mjs`。
验证：`npm test` 通过。

**Step 6 · 前端页面**
`api/watchlist.js` → `WatchlistForm.vue` → `WatchlistTrendChart.vue` → `WatchlistView.vue` → `router.js` / `App.vue` 接线。

**Step 7 · 转入持仓**
`Portfolio.vue` 的 query 预填逻辑。

**Step 8 · 文档**
`README.md` 增加「观察池」章节、API 表、定时任务说明（含单 worker 限制）、非目标。

## 10. 测试清单

### 10.1 后端 `tests/test_watchlist.py`（新增 ≥ 21 例）

`conftest.py` 需在测试启动前设 `WATCHLIST_SNAPSHOT_ENABLED=false`（或直接 monkeypatch `settings`），确保不启后台任务、不发真实网络请求。

1. 新增成功：返回 201，`code` 已归一化，`asset_type` 按代码推断
2. 新增重复 code → 409
3. 新增 `sh600900` 后再加 `600900` → 409（归一化后判重）
4. `target_yield = 0` → 422
5. `target_yield = 31` → 422
6. `target_price = -1` → 422
7. `name` 纯空白 → 422
8. `code` 非 6 位 → 422
9. 部分更新：只传 `target_yield`，其余字段不变
10. 更新 `target_price = 0` → 存 `NULL`，`price_signal = "not_set"`
11. 删除 → 200，且其快照被级联删除
12. 删除不存在的 id → 404
13. `GET /api/watchlist` **不调用**外部抓取（monkeypatch 抓取函数，断言 call_count == 0）
14. 无快照时 `price / current_yield / yield_trigger_price` 均为 `None`，`yield_signal = "unknown"`
15. `ttm_dividend = 0` → `yield_trigger_price is None`
16. 股息率线 `hit`：现价低到股息率 ≥ 目标
17. 股息率线 `near`：股息率刚好 `target × 0.97`
18. 股息率线 `watch`：股息率明显低于目标
19. 价格线 `hit` / `near`（`target × 1.03` 边界）/ `watch` 三种
20. `is_held` 跨组合识别：标的同时在两个组合持有 → `held_portfolios` 含两个名字
21. `is_stale`：快照日期 6 天前 → `True` 且 `stale_days == 6`；当天 → `False`
22. refresh（real）→ 写入当天快照；**同日再刷一次不新增行**（`count == 1`）
23. refresh（mock）→ **不写快照**，`refresh_error` 非空
24. refresh 时 `ttm_dividend` 抓回 0 但历史 > 0 → 沿用历史值
25. `snapshots?days=30` → 只返回 30 天内，且按 `trade_date ASC`
26. `snapshots?days=0` → 422
27. `watchlist_math` 纯函数：`None` 传播（`price=None` 时全部派生为 `None`，不抛异常）

既有 112+ 例**不得修改断言**。

### 10.2 前端 `src/utils/watchlist.test.mjs`（新增 ≥ 8 组）

1. `signalLabel` / `signalTone`：5 种信号映射正确
2. `sortForDisplay`：`hit` 在 `near` 前，`near` 在 `watch` 前；同档按 `yield_gap_points` 降序；`unknown` / `not_set` 排最后
3. `buildTrendSeries`：日期升序、`ttm_yield` 为 null 的点保留 `null`（断线），不插值
4. `buildTrendSeries([])` → 空数组，不抛错
5. `formatGapPercent`：正数带 `+`，负数带 `−`，`null` → `—`
6. `formatYield`：`5.5 → "5.50%"`，`null → "—"`
7. `isDataStale`：5 天 → false，6 天 → true
8. `summarize`：统计两类达标数与陈旧数正确

## 11. 验收（自验清单，必须逐条打勾）

```bash
cd portfolio_api && uv run pytest -q
cd portfolio_api && uv run python -c "import main; print(len(main.app.routes))"
cd portfolio_web && npm test
cd portfolio_web && npm run build
```

浏览器手动：

1. header 出现第三个 tab「观察池」，点进去 URL 为 `#/watchlist`，且组合选择器与组合按钮**消失**
2. 新增一个标的（如 `561580` 央企红利ETF），填目标股息率 `6.0`，不填目标价 → 列表出现，价格「—」，信号「数据不足」+「未设目标价」
3. 点「刷新全部」→ 价格、TTM 分红、当前股息率、触发价都有值，信号按实际数据变化
4. 再点一次「刷新全部」→ 当天快照不重复（`GET .../snapshots?days=7` 仍只 1 条）
5. 新增一条目标价高于现价的，价格信号应为「已达标」而股息率信号按实际独立显示（验证两条线不合并）
6. 点「走势」→ 展开 220px 双轴图，切 30/90/180 天都能正常重绘
7. 点「编辑」改目标股息率 → 触发价与距触发价立即变化
8. 点「转入持仓」→ 跳到 `#/`，资产表单已预填代码/名称/现价，数量为空；保存后回到观察池该行出现「已持有」
9. 点「删除」→ 二次确认 → 行消失
10. 把 `WATCHLIST_SNAPSHOT_TIME=00:00` 后重启后端 → 启动 2 秒内自动写入当天快照；把 `WATCHLIST_SNAPSHOT_ENABLED=false` 重启 → 不再自动写

## 12. 交付物清单

**新增**

- `portfolio_api/watchlist_math.py`
- `portfolio_api/watchlist_scheduler.py`
- `portfolio_api/routers/watchlist.py`
- `portfolio_api/tests/test_watchlist.py`
- `portfolio_web/src/views/WatchlistView.vue`
- `portfolio_web/src/components/WatchlistForm.vue`
- `portfolio_web/src/components/WatchlistTrendChart.vue`
- `portfolio_web/src/api/watchlist.js`
- `portfolio_web/src/utils/watchlist.js` + `watchlist.test.mjs`
- `portfolio_web/src/utils/watchlistColors.js`

**修改**

- `portfolio_api/models.py`（两表 + 两个 `relationship`）
- `portfolio_api/schemas.py`（4 个模型）
- `portfolio_api/config.py`（2 个配置项）
- `portfolio_api/main.py`（注册 router + lifespan 接线）
- `portfolio_api/tests/conftest.py`（关闭定时任务）
- `portfolio_web/src/router.js`（1 条路由）
- `portfolio_web/src/App.vue`（第三个 tab + `header-actions` 条件渲染）
- `portfolio_web/src/views/Portfolio.vue`（query 预填）
- `README.md`

**不得修改**

- `portfolio_api/routers/assets.py`、`routers/stocks.py`、`routers/portfolios.py`、`routers/goals.py`、`routers/calendar.py`、`portfolio_math.py`、`market.py` 的任何既有逻辑与签名
- `schemas.py` 里 `AssetResponse` / `PortfolioSummary` / `Goal*` / `Calendar*` 的任何字段
- 既有测试文件的任何断言
- `portfolio_web/src/styles/main.css` 既有规则
- `components/AssetList.vue`、`AssetItem.vue`、`ChartPie.vue`、`PortfolioOverview.vue`、`PortfolioGoals.vue`、`YearGrid.vue`（`AssetForm.vue` 只允许**新增可选 props 默认值**，不得改变既有行为）

## 13. 实现者自查

- [ ] 所有 `None` 分支都按 6.3 传播，没有 `0` 冒充「不适用」
- [ ] `yield_signal` / `price_signal` 只由后端产生，前端 `utils/watchlist.js` 里**没有**任何阈值比较
- [ ] mock 价格不落库；ttm 为 0 而历史 > 0 时沿用历史值
- [ ] 同日重复刷新不产生重复快照（唯一约束 + UPSERT 生效）
- [ ] 定时任务可关闭，测试环境确实关掉了
- [ ] 转入持仓复用了既有 `AssetForm` 与既有 API，没有第二条写入路径
- [ ] 新增样式类全部 `wl-` 前缀，`main.css` 零改动
- [ ] `README.md` 已记录观察池、API 表、定时任务与单 worker 限制
