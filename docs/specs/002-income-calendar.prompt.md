# 给实现模型的 Prompt（002 · 收入日历）

> 配套 docs/specs/002-income-calendar.md 实现。冲突时以 spec 为准。

---

你是一名全栈工程师，请在一个已有 FastAPI + Vue 3 项目里实现「收入日历」功能。

## 唯一事实来源
docs/specs/002-income-calendar.md。先完整读完，再按它实现。下面是摘要，冲突时一律以 spec 为准。

## 动手前必读
后端：portfolio_api/models.py、schemas.py、routers/portfolios.py、routers/assets.py、routers/stocks.py（重点看 fetch_dividend_from_eastmoney 与 _dividend_cache）、main.py、portfolio_math.py（如果还不存在请新建，§6.3 口径需要它）。
前端：portfolio_web/src/main.js（目前没有 vue-router）、App.vue、views/Portfolio.vue、components/PortfolioOverview.vue、components/PortfolioCharts.vue、components/ChartPie.vue、api/portfolio.js、utils/format.js、utils/chartColors.js、styles/main.css。
两图：image.png（年视图 + 月视图 + 月 Sheet 弹层）—— 视觉以图为准。

## 需求
header 新增 tab「收入日历」与「投资组合」并列。tab 内容为当前选中组合的年度分红日历：
- 年视图：Q1–Q4 行 × 3 列网格，每格 = 该月合计 ¥ + 笔数 + 状态色点
- 月视图：12 根堆叠柱按来源拆（股/ETF/债/OTC/REITs/自定义/自建/补录），顶部一条"年度累计"折线
- 点击月 → 底部 Sheet（70vh）列出该月所有笔分红
- 每笔有状态（预测/已公告/已到账/手动）支持「点击确认」改 paid、「+ 补录」手动新增

## 锁定的口径（不得自行更改）
1. D1 数据：复用 routers/stocks.py 已有 _dividend_cache，按 EX_DIVIDEND_DATE 归月，amount = quantity × DIVIDEND_PER_SHARE。不另起缓存。
2. D2 "预测"= akshare IMPLEMENT_STATUS 不是"已实施"（"已发布/实施中/董事会预案/股东大会通过"等）。已实施=paid（白底），其他=predicted/announced（黄/橙底）。超出 akshare 收录的未来月份=¥0，不外推。
3. D3 范围：当前组合。不做跨组合汇总。
4. D4 状态机：每笔 status ∈ {predicted, announced, paid, manual}，初始从 akshare 推。
5. D5 akshare 状态映射：含"已实施"→paid；含"已发布"/"实施中"→announced；其余→predicted（缺失/未知都保守按 predicted）。用户 confirm 后强制 paid。
6. D6 "点击确认"= 改 status 不改 amount。POST /api/portfolios/{pid}/dividends/confirm body {code, ex_date}，幂等。
7. D7 "补录"= 手动新增。POST /api/portfolios/{pid}/dividends/manual body {code, ex_date, amount_per_share, note?}。date ≤ today；amount > 0。必须属于组合内某资产。
8. D8 路由：项目目前没 vue-router。引入 vue-router@4（唯一新依赖） + provide/inject 共享 selectedPortfolioId。
9. D9 年视图：4×3 网格，状态色点见 spec §3 D10。
10. D10 月视图：ECharts 堆叠柱 + 累计折线，颜色 hardcode：股=#3b82f6 / ETF=#22c55e / 债=#f59e0b / OTC=#8b5cf6 / REITs=#ec4899 / 自定义=#06b6d4 / 自建=#d946ef / 补录=#94a3b8；折线=#059669。
11. D11 月 Sheet：底部 70vh 弹层，< 上一月 / 标题 / > 下一月；副行"预计 约 N 笔，合计 ¥X"；右上"+ 补录"展开行内表单。
12. D12 不改 y2024/25/26_dividend 语义（保持只算已实施），不改 AssetResponse 字段，不改 PortfolioSummary。

## 核心计算口径（严格按此）
遍历组合 assets → 对每个 asset 调 fetch_dividend_from_eastmoney(code)（走现有 in-memory 缓存）→ 按 ex_date 分月 → amount = quantity × DIVIDEND_PER_SHARE → status 映射 → 应用 dividend_confirmations（按 (portfolio_id, code, ex_date) 强制 paid，is_confirmed=true）→ 合并 manual_dividend_records（同 (code, ex_date) 时 manual 覆盖 akshare）→ 过滤年/月 → 内存里算 total_amount/count/by_status/by_source。
来源归类 class by_source：
- is_manual → "manual"
- is_etf(code) → "etf"
- asset_type 含"债"→"bond"；"OTC"→"otc"；"REITs"→"reit"；"自建"→"self"；"自定义"→"custom"
- 其余 → "stock"

## API
GET    /api/portfolios/{pid}/calendar?year=2026                           → 200 CalendarResponse
GET    /api/portfolios/{pid}/calendar/{year}/{month}                       → 200 MonthDividendsResponse
POST   /api/portfolios/{pid}/dividends/confirm body={code, ex_date}        → 200（幂等）
POST   /api/portfolios/{pid}/dividends/manual body={code, ex_date, amount_per_share, note?} → 201
DELETE /api/portfolios/{pid}/dividends/manual/{record_id}                  → 200
GET    /api/portfolios/{pid}/dividends/manual                             → 200 [ManualDividendResponse]
响应模型定义见 spec §6.2。
错误：组合/资产/记录不存在 → 404；参数非法 → 422；confirm 找不到当年记录 → 404。

## 数据模型（迁移方式：Base.metadata.create_all 自动建）
新表 manual_dividend_records（id, portfolio_id FK, asset_id FK, code, ex_date Date, amount_per_share Float, status String, note, created_at, updated_at）
新表 dividend_confirmations（id, portfolio_id FK, code, ex_date Date, confirmed_at, confirmed_status default 'paid'，UNIQUE(portfolio_id, code, ex_date)）
资产删除不级联（历史保留）。
不要加 Alembic，不要改 run_lightweight_migrations。

## 关键边界
- 组合无资产 → 整年 0；不抛错
- akshare 全失败 + 无缓存 → 该 asset 跳过，calendar 仍 200
- 同一 (code, ex_date) manual 优先
- confirm 幂等（重复不变）
- 补录 date > today → 422；amount ≤ 0 → 422；code 不在组合资产里 → 422
- 跨年 / 跨月按钮自动切年 + 重拉
- 路由初始直接 /calendar 选不到组合 → 跳回 /
- price_source=mock 的资产在月 Sheet 行尾加 mock chip
- 负成本 / NULL 成本不影响本功能

## 前端要点
- 引入 vue-router@4（hash 模式：createWebHashHistory）；两个路由 / 与 /calendar
- App.vue 改：左侧两个 tab 切换 <投资组合|收入日历>，右侧组合选择器（两个 tab 共享，用 usePortfolioContext composable；selectedPortfolioId 写到 localStorage key portfolio.ui.selectedPortfolioId.v1，try/catch 静默降级）
- 视图区上方放"组合选择器"（替换原 header 内的选择器位置）
- CalendarView.vue 顶部：标题 + 年/月 segmented + 翻年按钮
- YearGrid：4×3 网格，点击切月视图 + 选月
- MonthlyStackedBarChart：ECharts option 含 8 series stack='total' + 1 line series；点柱 → 切回年视图 + 打开月 Sheet
- MonthSheet：底部 70vh，列表行 = 标的 + 类型 chip + 派息日 + 组合名 + 金额（formatSigned + 黄/红/白底色按状态）+ 操作（点击确认 / 删除）
- ManualDividendForm：行内展开
- utils/calendar.js 纯函数（bucketByMonth / sumByStatus / sumBySource / classifyStatusFromImpl / classifySource / cumulativeLine / stackedBarData / formatMonthLabel）
- utils/calendarColors.js：8 类来源 + 折线 + 状态色
- 颜色 hardcode 在 calendarColors.js，不要散落
- 不改 styles/main.css 既有规则

## 测试与验收（必须全部通过并在回复里逐条报告）
cd portfolio_api && uv run pytest -q                  # 既有 112 不许改断言，按 spec §10.1 新增 19 例
cd portfolio_api && uv run python -c "import main; print(len(main.app.routes))"
cd portfolio_web && npm test                          # 既有 40 不许改，按 spec §10.2 新增 8 组
cd portfolio_web && npm run build                     # 必须成功
最后按 spec §11 的 10 条浏览器手动验收自查，并说明任何偏离 spec 的地方。
