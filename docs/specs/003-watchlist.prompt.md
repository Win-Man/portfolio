# 交付给实现模型的 Prompt（003 · 观察池）

> 用法：把本文件全文 + `docs/specs/003-watchlist.md` 一起交给实现模型（或直接粘贴下面 `---` 之间的内容）。
> 两者冲突时，以 `003-watchlist.md` 为准。

---

你是一名全栈工程师，请在一个已有的 FastAPI + Vue 3 项目中实现「观察池（Watchlist）」功能。

背景：这个项目现在只管**已买入的持仓**。用户想加一块「还没买、但想买」的候选池：给红利标的设目标股息率（必填）与目标价（选填），每天自动记录价格与 TTM 股息率，用来判断什么时候该下手。

## 唯一事实来源

`docs/specs/003-watchlist.md`。**先完整读完，再按它实现。**下面是摘要，冲突时一律以 spec 为准。

## 动手前必读

后端：`portfolio_api/main.py`（`lifespan`、`Base.metadata.create_all`、router 注册）、`models.py`、`schemas.py`、`config.py`、`portfolio_math.py`（纯函数模块的写法范本）、`routers/assets.py`（重点看 `fetch_ttm_dividend_per_share`、`fetch_real_time_price`、`_sum_trailing_12m`）、`routers/stocks.py`（重点看 `fetch_dividend_from_eastmoney`、`_akshare_lock`、分红缓存）、`routers/goals.py`（部分更新语义与 404/409/422 的写法范本）、`market.py`、`tests/conftest.py`。

前端：`portfolio_web/src/App.vue`、`router.js`、`views/Portfolio.vue`、`views/CalendarView.vue`、`components/MonthlyStackedBarChart.vue`（双轴 ECharts 范本）、`components/PortfolioGoals.vue`（信号/状态配色范本）、`components/AssetForm.vue`（转持仓要复用它）、`api/goal.js`（无 mock 兜底 + 透传 detail 的范本）、`utils/format.js`、`utils/goal.js`（纯函数 + 单测范本）、`styles/main.css`。

无参考截图，视觉按本文与 spec §7.4 的配色与布局描述实现。

## 需求

header 增加第三个 tab「观察池」，与「投资组合」「收入日历」并列，路由 `#/watchlist`：

- 全局候选清单（**不绑定任何组合**），每条 = 代码 + 名称 + 目标股息率（必填）+ 目标价（选填）+ 备注
- 每日收盘后由后端定时任务自动抓「实时价 + TTM 每股分红」落快照；页面也有「刷新全部 / 单条刷新」
- 列表展示现价、TTM 分红、当前股息率、距目标百分点、触发价、现价距触发价、目标价、**两个互相独立的信号**、数据日期、「已持有」标记
- 点「走势」展开双轴折线：价格（右轴 ¥）+ 股息率（左轴 %），时间窗 30/90/180 天
- 「转入持仓」跳到 `#/` 并打开既有 `AssetForm` 预填代码/名称/现价

## 锁定的口径（不得自行更改）

1. **D1** 观察池全局，不绑组合，不参与组合汇总；独立 tab `#/watchlist`，该 tab 下 header 的组合选择器与组合按钮**隐藏**。
2. **D2** 目标股息率必填（`0 < v <= 30`，百分数）；目标价选填（`> 0`，`0` 表示清空）。
3. **D3** 两条线**各自独立**给信号，**绝不合并**成一个「可买入」结论。UI 并排两个 badge。
4. **D4** `yield_trigger_price = ttm_dividend / (target_yield/100)`；`ttm_dividend <= 0` → `null`，显示「—」。
5. **D5/D6** 后端定时任务每日 `15:30`（Asia/Shanghai）抓取，写 `watchlist_snapshots`，同日唯一；`WATCHLIST_SNAPSHOT_TIME` 可改，`WATCHLIST_SNAPSHOT_ENABLED=false` 可关。
6. **D7** **只有 `source == "real"` 才写快照**。行情失败降级 mock（±3% 随机价）时不写库，保留上次数据，回 `refresh_error`。
7. **D8** 本次 TTM 分红抓到 0、而历史最新快照 `ttm_dividend > 0` 时，**沿用历史值**（防数据源抖动误判为不分红）。
8. **D9** 所有派生数值与两个信号**只在后端算**（新模块 `watchlist_math.py`），前端只做「信号→颜色/文案」映射、排序、序列构建，**前端不得出现阈值比较**。
9. **D10** 价格 2 位小数、股息率 2 位小数 + `%`、百分点差 2 位小数、金额用 `formatAmount`（**禁万/亿**）；后端不四舍五入，`null` 表示不适用而不是 `0`。
10. **D11** code 出现在任意组合资产里 → `is_held = true` + `held_portfolios`（组合名数组）；**保留在列表**，不删不隐藏。
11. **D12** 转入持仓只能复用既有 `AssetForm` + 既有 `POST /api/portfolios/{pid}/assets`，**不得**新增第二条资产写入链路。
12. **D13** 零新依赖：后端只用标准库 `asyncio` + `zoneinfo`，前端复用已有 `echarts`。不要 APScheduler / celery。
13. **D14** 不改 `assets` / `goals` / `calendar` 的任何既有接口、口径、字段与测试断言；不改 `main.css` 既有规则。
14. **D15** 快照缺日就是缺，图表如实断线（`null`），不做插值、不回填。
15. **D16** 周末与节假日照常写快照（腾讯返回最近收盘价），保证日期连续。

## 核心计算口径（严格照此实现）

```
current_yield            = ttm_dividend / price * 100            # price>0 且 ttm>0，否则 None
yield_trigger_price      = ttm_dividend / (target_yield / 100)   # ttm<=0 → None
yield_gap_points         = current_yield - target_yield          # 任一 None → None
yield_trigger_gap_percent= (price - yield_trigger_price) / yield_trigger_price * 100
price_gap_percent        = (price - target_price) / target_price * 100   # 未设 → None

yield_signal: current_yield is None → "unknown"
              current_yield >= target_yield           → "hit"
              current_yield >= target_yield * 0.97    → "near"
              否则                                    → "watch"

price_signal: target_price is None → "not_set"
              price is None        → "unknown"
              price <= target_price                   → "hit"
              price <= target_price * 1.03            → "near"
              否则                                    → "watch"

is_stale   = 今天(Asia/Shanghai) - snapshot_date > 5 天
stale_days = 天数差（无快照 → None）
```

`current_yield` **现算**，不读快照里的 `ttm_yield`（那个只用来画曲线）。
`near` 的两条阈值方向相反（`×0.97` / `×1.03`），语义都是「相对差 3% 以内」，**不要互相推导**。

## 快照写入（定时任务与两个 refresh 端点共用 `_refresh_one(db, item)`）

```
1. price_data = await fetch_real_time_price(code, current_price=<上一条快照价或 0>)
2. ttm = await fetch_ttm_dividend_per_share(code)          # 必须先分红后价格
3. if ttm == 0 and 上一条快照.ttm_dividend > 0: ttm = 上一条快照.ttm_dividend
4. if price_data["source"] != "real": 不写库，记 refresh_error，返回
5. today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
   UPSERT (item_id, today) → 有则更新 price/ttm_dividend/ttm_yield，无则插入
```

- 顺序必须是**先分红后价格**：反了会写出 `ttm_dividend=0` 的脏快照，当天信号直接变 `unknown`。
- UPSERT 用「先查后写」的 ORM，**不要用 SQLite 方言 `ON CONFLICT`**。
- `POST /api/watchlist/refresh` 逐条 `_refresh_one`，单条异常吞掉继续，**不整体 500**。

## 定时任务

- `watchlist_scheduler.py`：`lifespan` 里 `asyncio.create_task`，`yield` 后 `cancel()` + 吞 `CancelledError`。
- 循环：**先 tick 再 `sleep(60)`**（启动即补抓，不等一分钟）。
- tick：`now < 配置时间` 直接返回 → 查「今天缺快照的 item」（所有 item 减去已有当天快照的）→ 没有就返回 → 逐条 `_refresh_one`。
- **按 `trade_date` 最多重试 3 轮**（模块级 `_attempt_state: dict[date, int]`），防外部源挂掉时每分钟重试。
- 时间串解析失败 → warning + 回退 `15:30`，**不允许启动即崩**。
- 单 worker 假设（`--workers N > 1` 会重复抓），在 README 注明即可，本期不处理。

## API

```
GET    /api/watchlist                        → 200 [WatchlistItemResponse]（只读库，绝不触发外部抓取）
POST   /api/watchlist                        → 201（不自动抓价）
PUT    /api/watchlist/{item_id}              → 200（部分更新：字段为 null 则不改）
DELETE /api/watchlist/{item_id}              → 200 {"message":"Watchlist item deleted"}（级联删快照）
POST   /api/watchlist/refresh                → 200 [WatchlistItemResponse]（逐条抓 + UPSERT 当天）
POST   /api/watchlist/{item_id}/refresh      → 200 WatchlistItemResponse
GET    /api/watchlist/{item_id}/snapshots?days=90 → 200 [WatchlistSnapshotResponse]（ASC，days 7–730）
```

排序：`created_at ASC, id ASC`。
错误：观察项不存在 → 404 `Watchlist item not found`；同池 code 重复 → 409 `该标的已在观察池中`；`target_yield` 缺失/≤0/>30、`target_price < 0`、`name` 纯空白或 >50 字、`code` 归一化后非 6 位、`days` 越界 → 422。
完整字段定义见 spec §6.2，**照抄，不要改字段名、不要少字段**。

## 数据模型（靠 `Base.metadata.create_all` 自动建，**不写 `run_lightweight_migrations` 条目**，不加 Alembic）

新表 `watchlist_items`：`id`、`code`(String, NOT NULL, index)、`name`、`asset_type`(default `'股票'`)、`target_yield`(Float, NOT NULL)、`target_price`(Float, NULL)、`note`(default `''`)、`created_at`、`updated_at`。
关系 `snapshots = relationship(..., cascade="all, delete-orphan", order_by="WatchlistSnapshot.trade_date")`。
**不加** DB 级 `UNIQUE(code)`（重码走 API 返 409）。

新表 `watchlist_snapshots`：`id`、`item_id`(FK, index)、`trade_date`(Date, NOT NULL)、`price`(Float, NOT NULL)、`ttm_dividend`(Float, default 0)、`ttm_yield`(Float, NULL)、`price_source`(default `'real'`)、`created_at`。
`UniqueConstraint("item_id", "trade_date", name="uq_watchlist_snapshot_item_date")` —— **必须加**，UPSERT 靠它判重。

## 关键边界

- 观察池为空 → `200 []` + 前端空态
- 新增后从未刷新 → `price/current_yield/yield_trigger_price` 全 `null`，`yield_signal="unknown"`
- 标的无分红记录 → `ttm_dividend=0` → 触发价 `null`、信号 `unknown`，**不显示 0% 触发价**
- 首次刷新即行情失败 → 仍无快照、价格「—」+「待抓取」
- `PUT target_price = 0` → 存 `NULL`、`price_signal="not_set"`
- 同标的多组合持有 → `held_portfolios` 多个名字（id 升序去重）
- `code` 归一化：`sh600900`/`SH600900`/`600900` 统一 `600900`，判重按归一化值
- 快照 6 天未更新 → `is_stale=true` + 行首黄条

## 前端要点

- `router.js` 加 `/watchlist`；**`beforeEach` 不加观察池分支**（它不需要组合）
- `App.vue`：第三个 `router-link`「观察池」；`header-actions` 加 `v-if="!isWatchlistTab"`
- `WatchlistView.vue` = 工具条（标题 + 「刷新全部」+「＋ 新增标的」）+ 四张汇总卡（标的数 / 股息率达标数 / 价格达标数 / 数据日期）+ 表格（`overflow-x:auto`，列固定）+ 展开行图表
- 汇总卡第 3 项的分母**不含**未设目标价的标的
- 展开行：`colspan` 满宽、高度 220px、**一次只展开一个**；窗格 30/90/180 天默认 90，切换重拉 `snapshots?days=N`
- `WatchlistForm.vue`：新增/编辑弹窗，代码输入复用 `GET /api/stocks/search` 的联想；目标价留空 → 提交省略，清空已有目标价 → 提交 `0`；提交失败弹窗**不关闭**并 toast 后端 `detail`
- `api/watchlist.js`：**无 mock 假数据兜底**，非 2xx 解析 `detail` 后 `throw new Error(detail || \`HTTP ${status}\`)`
- 信号配色集中在 `utils/watchlistColors.js`：`hit=#059669` / `near=#f59e0b` / `watch=#94a3b8` / `unknown=#cbd5e1` / `not_set=#e2e8f0`；文案「已达标 / 接近 / 观察中 / 数据不足 / 未设目标价」
- 信号色语义是「是否达标」，**与红涨绿跌无关**（沿用 `PortfolioGoals` 里「已达成=绿」的约定）
- 曲线：价格线 `#2563eb`（右轴 ¥）、股息率线 `#059669`（左轴 %）、目标股息率 `#f59e0b` 虚线 `markLine`
- 新增样式类统一 `wl-` 前缀 + `<style scoped>`，**不改** `main.css` 既有规则
- 转入持仓：`router.push({ path:'/', query:{ addCode, addName, addPrice, from:'watchlist' } })`；`Portfolio.vue` 在 `keep-alive` 里，必须用 **`onActivated` + `watch(route.query, {immediate:true})`**（`onMounted` 只跑一次不够），打开 `AssetForm` 预填 `code/name/price`、`quantity` 与 `cost_price` 留空，随即 `router.replace({path:'/'})` 清 query；无任何组合时 toast 提示并中止

## 新增 / 修改文件

新增：`portfolio_api/watchlist_math.py`、`watchlist_scheduler.py`、`routers/watchlist.py`、`tests/test_watchlist.py`；`portfolio_web/src/views/WatchlistView.vue`、`components/WatchlistForm.vue`、`components/WatchlistTrendChart.vue`、`api/watchlist.js`、`utils/watchlist.js`、`utils/watchlist.test.mjs`、`utils/watchlistColors.js`。

修改：`portfolio_api/models.py`、`schemas.py`、`config.py`、`main.py`、`tests/conftest.py`（关闭定时任务）；`portfolio_web/src/router.js`、`App.vue`、`views/Portfolio.vue`；`README.md`。

不得修改：`routers/assets.py`、`routers/stocks.py`、`routers/portfolios.py`、`routers/goals.py`、`routers/calendar.py`、`portfolio_math.py`、`market.py` 的任何既有逻辑与签名；`AssetResponse`/`PortfolioSummary`/`Goal*`/`Calendar*` 的任何字段；既有测试的任何断言；`main.css` 既有规则；`AssetList.vue`、`AssetItem.vue`、`ChartPie.vue`、`PortfolioOverview.vue`、`PortfolioGoals.vue`、`YearGrid.vue`（`AssetForm.vue` 只允许新增带默认值的可选 props）。

## 测试与验收（必须全部通过，并在回复里逐条报告）

```bash
cd portfolio_api && uv run pytest -q
cd portfolio_api && uv run python -c "import main; print(len(main.app.routes))"
cd portfolio_web && npm test
cd portfolio_web && npm run build
```

- 后端既有用例不许改断言，另按 spec §10.1 新增 **≥ 21 例**（CRUD / 409 / 归一化判重 / 各 422 / 级联删除 / GET 不触发外部抓取 / null 传播 / 三档信号 × 两条线 / is_held 跨组合 / is_stale 边界 / 同日 UPSERT 不重复 / mock 不写库 / ttm 抖动沿用历史 / snapshots days 过滤）
- `tests/conftest.py` 必须设 `WATCHLIST_SNAPSHOT_ENABLED=false`，测试期间**不得**起后台任务或发真实网络请求
- 前端按 spec §10.2 新增 **≥ 8 组**（信号映射 / 排序 / 序列构建含 null 断线 / 空数组 / 格式化 / 陈旧判定 / 汇总）
- 最后按 spec §11 的 10 条浏览器手动验收自查，并在回复里说明任何偏离 spec 的地方
