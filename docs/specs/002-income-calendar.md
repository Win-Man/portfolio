# 002 · 收入日历（Income Calendar）

> 自包含实现指令。冲突时以本文档为准；UI 视觉以附件截图（`image.png` × 2）为最终参照。

---

## 1. 一句话

在 header 新增「收入日历」tab（与「投资组合」并列），按当前选中组合展示
**年度分红日历**：年视图（12 月分 Q1–Q4 网格，每格月合计 ¥+ 笔数）与月视图（堆叠柱按来源拆 + 年度累计折线）；点击月份弹出底部 Sheet 列出该月逐笔分红，支持「点击确认」（把预告改成已到账）与「补录」（手动新增一笔）。

---

## 2. 背景与现状（已在仓库中核对）

### 2.1 现有分红数据链路

| 位置 | 现状 |
|---|---|
| `portfolio_api/routers/stocks.py:104` | `fetch_dividend_from_eastmoney(code)` → 拉 akshare `stock_dividend_cninfo`（个股）/ `fund_etf_dividend_sina`（ETF），返回的字段：`EX_DIVIDEND_DATE` / `DIVIDEND_PER_SHARE` / `BONUS_IT_RATIO` / `TRANSFER_RATIO` / `IMPLEMENT_STATUS` / `REPORT_TIME` |
| `routers/stocks.py:42-67` | in-memory 缓存：`(code) -> (fetched_at, result_list)`，TTL 6h；`invalidate_dividend_cache(code)` 在 `refresh-price` 成功后调用 |
| `routers/assets.py:166-187` | 现有 `y2024/25/26_dividend` 字段：**只是按 EX_DIVIDEND_DATE 落在该年的 `DIVIDEND_PER_SHARE` 累加**，没有任何"按月"信息 |
| `routers/assets.py:57-87` | `_sum_trailing_12m`：滚动 12 月合计，用于 TTM |
| `routers/assets.py:85` | `fetch_ttm_dividend_per_share`：单标的每股分红金额（每期 `DIVIDEND_PER_SHARE` 累加），无逐笔信息 |
| `market.py` | `is_etf(code)`：0/3/6/9 开头=个股，5/15/16/18 开头=ETF/基金 |

### 2.2 现有前端结构

- `portfolio_web/src/main.js` —— **没有 vue-router**，目前是单页（SPA 单路由）
- `views/Portfolio.vue` —— 整个应用就这一个视图
- `components/PortfolioOverview.vue`、`PortfolioCharts.vue`、`AssetList.vue`、`AssetForm.vue`
- `api/portfolio.js`、`api/stock.js`
- `utils/format.js`（`formatAmount` / `formatSigned` / `formatQuantity` / `formatCost`）、`utils/chartColors.js`（饼图共享配色，可复用）、`utils/chartData.js`（饼图整理函数）

### 2.3 现有 schema 关键约束

- `Asset.dividend_per_share`：每股分红（**滚动 12 月合计**，已被 `refresh-price` 路径回写进库）
- `Asset.cost_price` 可负 / NULL
- `Asset.price_source ∈ {"real","mock","stale"}`
- 错误处理：`<list> * { float }` → `null`，无业务意义时返回 null（沿用）

---

## 3. 已锁定的决策（D1–D12，不得自行更改）

| # | 决策 |
|---|---|
| **D1** | **数据**：后端在 `routers/stocks.py` 已有缓存基础上，新增一个「按 (code) → 全部历史分红」接口（**不另起缓存**，复用 `_dividend_cache`），后端按 `EX_DIVIDEND_DATE` 把这些原始记录**归到月份**，再乘 `quantity` 得到该月该笔的分红金额。**不预测**超出 akshare 收录的未来月份（详见 D2） |
| **D2** | **"预测"= akshare 已收录但 `IMPLEMENT_STATUS` 不等于"已实施"**。个股典型值："已实施" / "已发布" / "实施中" / "股东大会通过" / "董事会预案"；ETF 类似。一律计入月合计但在明细上标**"预测"**（黄底）；`IMPLEMENT_STATUS == "已实施"` 标**"已到账"**（白底）。**超出 akshare 收录范围**的未来月份显示 ¥0，不做外推 |
| **D3** | **范围**：当前组合 = 当前 header 选中的 portfolio_id。**不做跨组合汇总**。切换组合时整个日历刷新 |
| **D4** | **状态机**：每笔分红有 `status ∈ {predicted, announced, paid, manual}`，初始从 akshare 推得（详见 D5），用户可 `confirm` 把 predicted/announced → paid，或 `manual` 新增一笔 |
| **D5** | **状态映射规则**（aks hare 原始 → 内部 status）：<br>• `IMPLEMENT_STATUS` 含"已实施" → **paid**（已到账，白底）<br>• `IMPLEMENT_STATUS` 含"已发布" / "实施中" → **announced**（已公告，红底 / 黄底）<br>• 其他（"董事会预案" / "股东大会通过"）→ **predicted**（预测，灰底）<br>• 无 akshare 数据但用户手动加 → **manual**（补录，灰带边框） |
| **D6** | "**点击确认**" = 把 predicted/announced 改为 paid（**status 转换，不重复加金额**）。接口：`POST /api/portfolios/{pid}/dividends/confirm`，body `{code, ex_date}`，幂等（重复 confirm 不会重复加，只更新时间戳） |
| **D7** | "**补录**" = 用户手动新增一笔（akshare 漏收录 / 场外基金 / 历史补账）。`POST /api/portfolios/{pid}/dividends/manual`，body `{code, ex_date, amount_per_share, note?}`，写入 `manual_dividend_records` 表 |
| **D8** | **路由**：项目目前没 vue-router。新增 `vue-router@4`（轻量，加进 `package.json` devDep，`npm i`），两个路由 `/`（投资组合 → 现 `Portfolio.vue`）与 `/calendar`（收入日历 → 新 `CalendarView.vue`）。`selectedPortfolioId` 用 `pinia` 或 `provide/inject` 共享（推荐 `provide/inject` 不引新依赖） |
| **D9** | **年视图布局**（图 1）：Q1/Q2/Q3/Q4 行 × 3 月列网格；每格显示**该月合计 ¥xx.xx** + **月份副标 "x月"**；当前选中月描蓝边（`#2563eb` 实线 2px）；点击格 → 切换到月视图并选中该月。左右翻年按钮（`<` `>`），跨年时数据按需重新请求 |
| **D10** | **月视图布局**（图 1 下方柱状图）：12 根柱，**按来源堆叠**（股/ETF/债/OTC/REITs/自定义/自建/补录 8 类）；顶上一条折线 = **当年累计**（不可叠加，作为参照线）。颜色与图 1 图例一致：股=`#3b82f6` / ETF=`#22c55e` / 债=`#f59e0b` / OTC=`#8b5cf6` / REITs=`#ec4899` / 自定义=`#06b6d4` / 自建=`#d946ef` / 补录=`#94a3b8`；累计折线用深绿 `#059669` 实线 |
| **D11** | **月 Sheet**（图 2）：底部弹层（不是 Modal），高度 70vh；标题 "YYYY年MM月 预计 约 N 笔，合计 ¥XXX"；列表行 = 标的 / 类型标签 / 派息日 / 来源组合 / 金额（带符号）/ 状态标签 + 操作按钮。弹层头部右侧 `+ 补录` 按钮打开内嵌小表单（不跳转新页） |
| **D12** | **不引入新图表库**：堆叠柱 + 折线**用 ECharts**（项目已有，`PortfolioCharts` 已经在用）。月视图 = 新组件 `MonthlyStackedBarChart.vue`，与现有 `ChartPie.vue` 同样封装风格 |

---

## 4. 目标 / 非目标

### 目标
1. 后端：
   - 新表 `manual_dividend_records`：id, portfolio_id, asset_id, code, ex_date, amount_per_share, note, source, created_at
   - 新表 `dividend_confirmations`（or 同表 status 字段，二选一，spec §6.2 给方案）
   - 新增 `routers/calendar.py`，暴露 6 个端点（§6.1）
   - **修改** `routers/stocks.py`：新增 `fetch_all_dividend_records(code)` 函数（用现有缓存）
   - **修改** `routers/assets.py` 的 `refresh_price` 在成功刷新时调用 `invalidate_dividend_cache(code)`（**该调用其实已经存在**，确认不重复加）
2. 前端：
   - 加 `vue-router@4`（仅此一个新依赖） + 两个路由
   - header 改造：左侧 tab 切换 `<组合视图 | 收入日历>`；**组合选择器**移到内容区上方（两个视图共享）
   - `provide/inject` 共享 `selectedPortfolioId`（localStorage 持久化沿用现 `portfolio:selectedPortfolioId:v1` 风格，但 key 用新名 `portfolio.ui.selectedPortfolioId.v1`）
   - 新组件：`CalendarView.vue`、`YearGrid.vue`（年视图）、`MonthlyStackedBarChart.vue`（月视图）、`MonthSheet.vue`（月明细弹层）、`ManualDividendForm.vue`（补录表单）
   - 新 `utils/calendar.js` + `utils/calendar.test.mjs`：纯函数（分月分箱、月合计、状态映射、堆叠柱数据整理）—— **这些是 spec 里唯一能被单测锁定的口径点**
3. 测试：
   - 后端 `tests/test_calendar.py` 新增 14 例（§10.1）
   - 前端 `src/utils/calendar.test.mjs` 新增 8 组（§10.2）
4. 文档：README 补功能、API、目录结构

### 非目标（明确不做）
- ❌ 不做跨组合汇总 / 全局日历
- ❌ 不预测 akshare 收录之外的未来月份
- ❌ 不做价格刷新以外的定时刷新（弹层内手动补录不算刷新）
- ❌ 不做分红税率计算（不区分税前/税后）
- ❌ 不做"导入 CSV 历史分红"（仅手动补录）
- ❌ 不做分红趋势预测 / AI 预测
- ❌ 不做通知 / 提醒 / 日历订阅
- ❌ 不改现有 `PortfolioSummary` / `AssetResponse` 字段与数值
- ❌ 不修改 y2024/25/26_dividend 字段（保持只算"已实施"，与 status 解耦）
- ❌ 不做多用户、不引入 Alembic

---

## 5. 数据模型

### 5.1 新表 `manual_dividend_records`

```python
class ManualDividendRecord(Base):
    __tablename__ = "manual_dividend_records"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    code = Column(String, nullable=False)                  # 冗余存，避免 asset 删除后无法定位
    ex_date = Column(Date, nullable=False)                 # 派息日 / 入账日
    amount_per_share = Column(Float, nullable=False)       # 元/股
    # predicted / announced / paid / manual
    status = Column(String, nullable=False, default="manual")
    note = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

### 5.2 新表 `dividend_confirmations`（只记"用户确认"事件，幂等去重）

```python
class DividendConfirmation(Base):
    __tablename__ = "dividend_confirmations"
    # 用 (portfolio_id, code, ex_date) 唯一约束
    __table_args__ = (UniqueConstraint("portfolio_id", "code", "ex_date", name="uq_confirm"),)

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    code = Column(String, nullable=False)
    ex_date = Column(Date, nullable=False)
    confirmed_at = Column(DateTime(timezone=True), server_default=func.now())
    confirmed_status = Column(String, default="paid")     # 当前恒为 paid
```

`Asset` 不加新字段；与 `assets` 表的关系在删除时**不级联**（确认/补录记录与资产生命周期解耦，资产删除保留历史）。

### 5.3 迁移

- 新表由 `Base.metadata.create_all` 自动建；**不需要改 `run_lightweight_migrations`**
- 不加数据库级额外索引（除上述），EX_DATE 查询走 Python 端过滤即可
- `status` 校验：写库时白名单 `{"predicted","announced","paid","manual"}`，其他 422

---

## 6. 后端 API 契约

### 6.1 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portfolios/{pid}/calendar?year=2026` | 200 `CalendarResponse`：整年 12 个月合计 + 当年所有原始记录（去重后） |
| GET | `/api/portfolios/{pid}/calendar/{year}/{month}` | 200 `MonthDividendsResponse`：单月所有笔 + 该月合计（按组合/来源/状态分组） |
| POST | `/api/portfolios/{pid}/dividends/confirm` | body `{code, ex_date}`；200 `{code, ex_date, status, confirmed_at}` |
| POST | `/api/portfolios/{pid}/dividends/manual` | body `{code, ex_date, amount_per_share, note?}`；201 `ManualDividendResponse` |
| DELETE | `/api/portfolios/{pid}/dividends/manual/{record_id}` | 200（删除补录记录） |
| GET | `/api/portfolios/{pid}/dividends/manual` | 200 `[ManualDividendResponse]`（查询当前组合所有补录） |

错误：
- 组合 / 资产 / 记录不存在 → 404
- `ex_date` 缺 / 格式错 / `amount_per_share ≤ 0` → 422
- `status` 不在白名单 → 422
- `confirm` 时组合内找不到该 (code, ex_date) 匹配的**当年**记录 → 404 `找不到该笔分红记录`

### 6.2 响应模型

```python
class CalendarMonth(BaseModel):
    year: int
    month: int                                 # 1..12
    total_amount: float                        # 该月合计 = quantity * per_share 之和
    count: int                                 # 该月笔数
    by_status: dict[str, float]                # {"paid": 12.3, "announced": 5.0, "predicted": 0, "manual": 0}
    by_source: dict[str, float]                # 来源大类："stock"/"etf"/"bond"/"otc"/"reit"/"custom"/"self"/"manual"
    # 来源归类规则（写进 portfolio_math.py 新函数 classify_source(asset)）：
    #   - manual_dividend_records.source == "manual" → "manual"
    #   - is_etf(code) → "etf"
    #   - asset_type 包含"债" → "bond"；"OTC" → "otc"；"REITs"/"reit" → "reit"
    #   - asset_type 包含"自建" → "self"；"自定义"/"自定义" → "custom"
    #   - 其余 → "stock"
    #   缺字段或不识别 → "stock"（兜底）

class CalendarResponse(BaseModel):
    portfolio_id: int
    year: int
    months: list[CalendarMonth]
    total_year: float
    total_count: int
    asset_count: int                           # 组合内贡献分红的资产数

class MonthDividendItem(BaseModel):
    code: str
    name: str
    asset_id: int
    portfolio_id: int
    portfolio_name: str
    asset_type: str
    ex_date: date
    dividend_per_share: float
    quantity: float
    amount: float                              # = quantity * dividend_per_share
    status: Literal["predicted","announced","paid","manual"]
    source: str                                # 与 by_source 同分类
    is_confirmed: bool                         # 是否已被用户 confirm
    is_manual: bool                            # 是否手动补录
    note: str = ""

class MonthDividendsResponse(BaseModel):
    portfolio_id: int
    year: int
    month: int
    total_amount: float
    count: int
    items: list[MonthDividendItem]
```

### 6.3 计算口径（必须严格按此实现）

1. **遍历组合的所有 assets**（含 `is_confirmed` 已补录的）
2. 对每个 asset，按 `code` 一次性从 `fetch_dividend_from_eastmoney(code)` 拿全部历史分红（**走现有 in-memory 缓存**，不另起）
3. **聚合**：每条分红记录转成一个 `MonthDividendItem` 草稿：
   - `ex_date` → `(year, month)` 分箱
   - `dividend_per_share` = 原始 `DIVIDEND_PER_SHARE`（**不是** TTM/累计的 `asset.dividend_per_share`）
   - `quantity` = `asset.quantity`
   - `amount` = `quantity × dividend_per_share`
   - `status` 按 D5 规则从 `IMPLEMENT_STATUS` 推
4. **应用 `dividend_confirmations`**：若 `(portfolio_id, code, ex_date)` 已有确认记录，**status 强制改成 "paid"**，`is_confirmed = true`
5. **应用 `manual_dividend_records`**：合并为 `status="manual"`，`is_manual=true`，`note` 透传；相同 `(code, ex_date)` 时**manual 优先**（如 akshare 与 manual 同时收录，以 manual 为准）
6. **按年过滤**（GET calendar 时）；**按月过滤**（GET calendar/{year}/{month} 时）
7. **计算 `total_amount` / `count` / `by_status` / `by_source`** 在内存里做
8. **资产里 `cost_price` 字段与本功能无关**，不允许参与计算
9. **空集**：`total_amount = 0`，`months[*].total_amount = 0`，`count = 0`

### 6.4 关键边界

| 场景 | 行为 |
|---|---|
| 组合无任何资产 | 返回 `months[*] = 0`，`total_amount = 0`，`asset_count = 0` |
| 某资产 quantity=0 | 该资产所有历史分红的 `amount = 0`（仍出现在 items 中，**便于 debug**，但合计贡献 0） |
| 某资产 price_source="mock" | 计入汇总（与「收入与价格无关」一致），但在月 Sheet 该行右侧加 `mock` 提示 |
| `IMPLEMENT_STATUS` 缺失 | 默认 `predicted`（保守） |
| akshare 报错（网络失败） | 沿用现有 `fetch_dividend_from_eastmoney` 的「回退到 6h 旧缓存」逻辑；若**完全无缓存**，该 asset 跳过 + 日志 warn，**不要**让 500 阻断整个 calendar |
| `ex_date` 解析失败 | 该条记录 warn 跳过，不影响其他 |
| `ex_date` 在未来（如 2027 年的预告） | 仍计入对应月，**不**因为"未来"就丢弃 |
| 跨年（2025-12 vs 2026-01） | 按 ex_date 落月；前端左右翻年只需换 year 重新拉 |
| 同一资产同年同月两笔分红 | 视为两笔 items（实际常见：A股+港股通双重身份 / 转送后调整） |
| 用户 `confirm` 后 `ex_date` 已过 | 仍标记 paid，符合"补打标"语义 |

### 6.5 与现有刷新价格的关系

- 现有 `routers/assets.py:259 refresh_asset_price` 成功时**已经**调用 `invalidate_dividend_cache(code)`，**不要**重复
- 现有 `routers/assets.py:296 refresh_all_prices` 也**已经**调用，无需改动
- 因此日历天然能"刷新价格后即时反映新的分红数据"

---

## 7. 前端实现

### 7.1 路由

新增 `portfolio_web/src/router.js`：

```js
import { createRouter, createWebHashHistory } from 'vue-router'
import Portfolio from './views/Portfolio.vue'
import CalendarView from './views/CalendarView.vue'

const routes = [
  { path: '/', name: 'portfolio', component: Portfolio },
  { path: '/calendar', name: 'calendar', component: CalendarView },
]

export default createRouter({ history: createWebHashHistory(), routes })
```

`main.js` 改为：

```js
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles/main.css'
createApp(App).use(router).mount('#app')
```

`App.vue`：保留外层布局 + 注入 `selectedPortfolioId`（provide / inject 模式）；header 重写为 tab 切换。

### 7.2 全局状态

- `provide('selectedPortfolioId', ref(null))` 在 `App.vue`
- 读取 / 写回 localStorage key `portfolio.ui.selectedPortfolioId.v1`（**沿用上一期风格** `portfolio.<feature>.<field>.v1`），读写包 `try/catch` 静默降级
- `usePortfolioContext.js` 暴露 `usePortfolioContext()` 工具（inject + 写回逻辑），给两个视图共用

### 7.3 组件树

```
App.vue
├── header（tab + 组合选择器）
└── <router-view>
    ├── views/Portfolio.vue          （现有，重构 header 即可）
    └── views/CalendarView.vue
        ├── YearGrid.vue
        ├── MonthlyStackedBarChart.vue
        ├── MonthSheet.vue
        │   └── ManualDividendForm.vue
        └── （顶部「年/月」切换 + 翻年按钮）
```

### 7.4 视图规范

#### 7.4.1 顶部（`CalendarView.vue`）
- 标题区（图 1 顶部"收入日历"）
- 「年/月」切换 Segmented Control（仿图 1 顶部右侧：圆角 pill，选中态主色背景）
- 左右翻年按钮 `<` 标题年 `>`
- 选中资产下拉：**移到此处**（与 Portfolio 视图共享；放 tab 内容区顶部，避免重复）

#### 7.4.2 年视图（`YearGrid.vue`）
- 4 行（Q1-Q4）× 3 列网格
- 每格内容：
  - 主行：`¥{formatAmount(monthTotal)}`（用 `font-variant-numeric: tabular-nums`）
  - 副行：`{month}月`，左侧加一个**状态色**小圆点：
    - 全部 paid：绿
    - 含 announced：黄
    - 含 predicted：橙
    - 含 manual：蓝
    - 0 笔：灰
  - 选中态：2px 主色边框 + 浅主色背景
- 点击格 → `selectMonth(month)` + 切到月视图

#### 7.4.3 月视图（`MonthlyStackedBarChart.vue`）
- 12 根柱 = 当年 1–12 月
- **堆叠**（自下而上）顺序与图例：股 → ETF → 债 → OTC → REITs → 自定义 → 自建 → 补录
- 顶部一条折线 = 当年累计分红（每月累加）= `seriesLine`：y 轴右侧
- 颜色用 §3 D10 的固定值
- 鼠标悬浮柱 → tooltip：`YYYY-MM  共 ¥X  笔数 N  各类金额…`
- 鼠标悬浮线 → tooltip：`YYYY-MM  累计 ¥X`
- 柱子点击 → `selectMonth(month)` 切回年视图（也打开月 Sheet）—— **交互闭环**

#### 7.4.4 月 Sheet（`MonthSheet.vue`）
- 底部弹出层 70vh，圆角 16px 顶部
- 背景遮罩：黑/白 50% 透明 + 点击遮罩关闭
- 头部：`<` 上一月 / 标题 `2026年 10月` / `>` 下一月
- 标题副行：`预计 约 N 笔，合计 ¥XXX`
- 头部右上：`+ 补录` 按钮（点击 → 内嵌展开 `ManualDividendForm` 行内表单）
- 列表：每行布局对齐图 2：
  - 标的名称（粗）
  - 类型 chip：ETF / 股票 / 债 / 手动（颜色同 §3 D10）
  - 派息日 `MM月DD日`（如 10月16日）
  - 来源组合（**多组合时不显示**——本视图只显示当前组合；D3 范围内）
  - 金额：`+¥46.50`（用 `formatSigned`）
  - 状态 chip + 操作：
    - `predicted` / `announced`：「待到账 / 预测」+ 「点击确认」按钮（蓝）
    - `paid`：「已到账」灰色 chip
    - `manual`：「手动」+ 删除按钮（红）
- 加载 / 错误 / 空态与现有 Portfolio 视图保持一致

#### 7.4.5 `ManualDividendForm.vue`
- 行内展开（不是 Modal）
- 字段：标的（自动填当前组合内该 code）/ 派息日（date）/ 每股金额 / 备注
- 校验：金额 > 0，日期 ≤ 今天（**不能补未来**，若要"未来预告"应走 akshare）
- 提交后关闭行内 + 刷新当前月

### 7.5 视觉与可访问性

- 沿用 `styles/main.css` CSS 变量
- 颜色值直接 hardcode（柱状图配色）—— 抽到 `utils/calendarColors.js` 便于单测引用
- 字体：金额一律 `formatAmount`（千分位 + 2 位）；`font-variant-numeric: tabular-nums`；不出现"万/亿"
- 现有 `InfoTooltip.vue` 用于状态说明（如"待到账"hover 显示 akshare 字段解释）
- 空态：组合无资产 → "先添加资产"；月份 0 笔 → 显式"¥0 / 0 笔"（不要遮）

---

## 8. 边界与异常清单

| # | 场景 | 行为 |
|---|---|---|
| 1 | 组合无资产 | 整个日历空态："该组合暂无资产" |
| 2 | 资产有但 0 笔历史分红 | 整年 0，bar 全部 0；点月 → "0 笔，¥0" |
| 3 | akshare 拉取全部失败（无缓存） | 该资产 warn 跳过；不抛 500 |
| 4 | akshare 拉取部分失败 | 同 3 |
| 5 | `IMPLEMENT_STATUS` 中文含"实施"但同时有"未" | 保守按 predicted 处理（写 Python 解析函数时取 `not in {"已实施"}` 而非 `in {"已实施"}`） |
| 6 | 同一 (code, ex_date) 既有 akshare 又有 manual | manual 覆盖（**§6.3.5**） |
| 7 | 用户点击「确认」后再次点击 | 幂等，返回 200 不变；`confirmed_at` 不变 |
| 8 | 用户点击「确认」未实施（即 predicted/announced） | 允许，状态变 paid |
| 9 | 用户点击「确认」已经是 paid | 幂等 |
| 10 | 补录日期 > 今天 | 422 "不能补未来日期；预告请等待 akshare 收录" |
| 11 | 补录金额 ≤ 0 | 422 |
| 12 | 删除不存在的 manual 记录 | 404 |
| 13 | 删除另一组合的 manual 记录 | 404（**不**返回 403，避免泄露） |
| 14 | 跨年（同年 12 月 → 次年 1 月） | 翻年按钮切换；月 Sheet 上下月按钮遇边界自动切年 |
| 15 | 月 Sheet 选 11 月点下一月 | 切到 12 月，year 不变 |
| 16 | 月 Sheet 选 12 月点下一月 | 切到次年 1 月，year +1，**重新请求** calendar |
| 17 | 月 Sheet 选 1 月点上一月 | 切到上年 12 月，year −1 |
| 18 | 切换 tab 时 selectedPortfolioId 变化 | 月 Sheet 关闭，年/月视图重置 |
| 19 | 切换 tab 不影响 calendar 数据缓存（同 pid） | 切回时仍用 `vue` ref，不要重新打 API |
| 20 | 路由初始直接进入 `/calendar` | 组合选择器未选 → 跳回 `/` 或显示提示选组合（推荐跳回） |
| 21 | price_source=mock 的资产 | 月 Sheet 行尾加 mock chip（黄色，"模拟价，仅供参考"） |
| 22 | 负成本资产 | **不影响**本功能（成本不参与），但 UI 不变 |
| 23 | 大量历史数据（某只股 50 笔 × 组合 10 只） | 后端单接口最多 500ms；前端不卡；不需分页 |
| 24 | 资产有重复 code（两组合都买同一只） | 按 (portfolio_id, code) 分别缓存；切组合独立刷新；不跨组合去重 |
| 25 | 补录的 code 在组合内找不到 | 422（手动补录必须属于组合内某资产） |
| 26 | localStorage 不可用 | 选中组合不记忆，默认选第一个 |

---

## 9. 实现步骤拆解（write scopes）

| 步骤 | 内容 | 文件 |
|---|---|---|
| **S1** | `routers/stocks.py`：新增 `fetch_all_dividend_records(code)`（直接复用 `_dividend_cache`） | `portfolio_api/routers/stocks.py` |
| **S2** | `portfolio_math.py`：新增 `classify_source(asset, is_manual)` 与 `classify_status(impl_status)` 两个**纯函数** | `portfolio_api/portfolio_math.py` |
| **S3** | 新表 `ManualDividendRecord` / `DividendConfirmation` + `Portfolio` 关系 | `portfolio_api/models.py` |
| **S4** | Schemas（`CalendarMonth` / `CalendarResponse` / `MonthDividendItem` / `MonthDividendsResponse` / `ManualDividendResponse`） | `portfolio_api/schemas.py` |
| **S5** | `routers/calendar.py`（6 个端点 + 聚合逻辑） | `portfolio_api/routers/calendar.py`(新) |
| **S6** | `main.py` 注册 `calendar.router` | `portfolio_api/main.py` |
| **S7** | `tests/test_calendar.py`（§10.1 的 14 例），跑通 | `portfolio_api/tests/test_calendar.py`(新) |
| **S8** | `npm i vue-router@4`，加进 devDep | `portfolio_web/package.json` + `package-lock.json` |
| **S9** | `src/router.js` + `main.js` 改造 + `App.vue` 重写（tab + provide） | `portfolio_web/src/router.js`(新)、`main.js`、`App.vue` |
| **S10** | `usePortfolioContext.js`（共享 selectedPortfolioId + localStorage 读写） | `portfolio_web/src/composables/usePortfolioContext.js`(新) |
| **S11** | `utils/calendar.js` 纯函数（§11.2） + 单测 | `portfolio_web/src/utils/calendar.js`(新)、`calendar.test.mjs`(新) |
| **S12** | `utils/calendarColors.js`（8 种来源 + 折线 + 状态色） | `portfolio_web/src/utils/calendarColors.js`(新) |
| **S13** | `YearGrid.vue` 年视图 | `portfolio_web/src/components/YearGrid.vue`(新) |
| **S14** | `MonthlyStackedBarChart.vue` 月视图 ECharts | `portfolio_web/src/components/MonthlyStackedBarChart.vue`(新) |
| **S15** | `MonthSheet.vue` 月明细弹层 | `portfolio_web/src/components/MonthSheet.vue`(新) |
| **S16** | `ManualDividendForm.vue` 补录行内表单 | `portfolio_web/src/components/ManualDividendForm.vue`(新) |
| **S17** | `CalendarView.vue` 串起来（顶部 + 切年/月 + 选月 + 调 API） | `portfolio_web/src/views/CalendarView.vue`(新) |
| **S18** | `Portfolio.vue` 改用 `usePortfolioContext`（移除自己的 selectedPortfolioId + loadPortfolios 里读 localStorage 的逻辑） | `portfolio_web/src/views/Portfolio.vue` |
| **S19** | README 更新 | `README.md` |
| **S20** | 端到端自验（§11） | — |

---

## 10. 测试清单

### 10.1 后端 `tests/test_calendar.py`

复用 `client` / `db_session` / `mock_dividend`（已有，按需扩展）：

1. 组合无资产 → calendar 返回 12 个 month total=0 / count=0 / asset_count=0
2. 1 个资产 / 1 笔历史分红（mock_dividend 自带 `today-30d` 一期）→ 当年当月 total=quantity*0.5，by_status={"paid":...}，by_source={"stock":...}（假设 mock 是股票）
3. 2 个标的 → 各自贡献正确累加
4. ETF（5 开头）→ by_source["etf"]；个股 → by_source["stock"]
5. 资产 asset_type="债" → by_source["bond"]；"OTC" → "otc"；"REITs" → "reit"；"自建" → "self"；"自定义" → "custom"
6. 跨年（mock 返回 2024/2025/2026 三期）→ 不同年取月合计不同
7. `IMPLEMENT_STATUS` 含"已实施"→ status="paid"；"已发布"/"实施中"→ "announced"；"董事会预案"→ "predicted"；缺失 → "predicted"（保守）
8. 同一 `(code, ex_date)` 确认两次 → 第二次幂等，`confirmed_at` 不变
9. 确认后 is_confirmed=True，calendar 里 status="paid"（即使原始 IMPLEMENT_STATUS 是"已发布"）
10. 补录成功 → manual 出现 → calendar 出现 `status="manual"`，`by_source["manual"] += quantity*amount`；同 `(code, ex_date)` 覆盖 akshare 的数据
11. 补录日期 > 今天 → 422
12. 补录金额 ≤ 0 → 422
13. 删除补录 → 再次 calendar 该笔消失
14. 跨组合：组合 A 的确认 / 补录不影响组合 B
15. akshare 拉取失败（monkeypatch 让 `fetch_dividend_from_eastmoney` 抛异常 + 无缓存）→ 该 asset 跳过，calendar 仍 200；不抛 500
16. `quantity=0` 资产 → amount=0 出现在 items，**不**影响 by_source 的 0 值
17. `price_source="mock"` 的资产 → 月 Sheet 端点 items 含 `is_mock_price` 字段（**或后端单独给个 mock flag**）；该断言在 month endpoint 验证
18. 负成本资产 → 不影响本功能（回归锁定）
19. `dividend_confirmations` 唯一约束被违反（直接 SQL 插两条同 key）→ 启动 + 写入不会破坏，但 confirm API 永远幂等

### 10.2 前端 `src/utils/calendar.test.mjs`（`node --test`）

1. `bucketByMonth(records)`：3 笔不同日期 → 3 个月合计对、12 月 0 占位
2. `sumByStatus(items)`：4 类 status 累加对
3. `sumBySource(items)`：8 类 source 累加对
4. `classifyStatusFromImpl('已实施')` = 'paid'；'已发布' = 'announced'；'董事会预案' = 'predicted'；缺失/未知 = 'predicted'
5. `classifySource(asset, isManual)`：ETF code → 'etf'；asset_type 含"债"→ 'bond'；含"自建"→ 'self'；含"REITs"→ 'reit'；含"自定义"→ 'custom'；其余 → 'stock'；isManual=true → 'manual'
6. `cumulativeLine(monthTotals)`：返回 12 个累计值（最后一项 = total_year）
7. `stackedBarData(monthTotals, sources)`：返回 ECharts series 所需 `{name, data, stack: 'total', itemStyle: {color}}` 数组，顺序与图例一致
8. `formatMonthLabel(year, month)`：输出 `YYYY年MM月`

---

## 11. 验收（自验清单，必须逐条打勾）

```bash
cd portfolio_api && uv run pytest -q                          # 既有 112 + 新增 19 = 全绿
cd portfolio_api && uv run python -c "import main; print(len(main.app.routes))"
cd portfolio_web && npm test                                  # 既有 40 + 新增 8 组 = 全绿
cd portfolio_web && npm run build                             # 必须成功
# 浏览器手动：
# 1. /  旧功能一切如常（概览、饼图、资产表拖列/宽）
# 2. 点 header「收入日历」→ 跳 /calendar
# 3. 年视图：12 月合计与翻年正确
# 4. 切到月视图：12 根堆叠柱 + 顶部累计线
# 5. 点 10 月 → 月 Sheet 弹出，列出该月所有笔
# 6. 选「点击确认」→ 该笔状态变 paid，calendar 数字不变（amount 一致）
# 7. 选「+ 补录」→ 行内展开 → 填表 → 保存 → 月 Sheet 立即多出一行
# 8. 切回投资组合 tab → 数据无残留
# 9. 切换组合选择器 → 两个 tab 都刷新
# 10. 切组合到无资产的 → 显示"暂无资产"
```

**DoD**：
- §10 命令全绿；§11 手动 10 条全过
- 既有 112 后端 + 40 前端测试不回归、断言不改
- README 更新完毕
- 无新依赖（仅 `vue-router@4`）

---

## 12. 交付物清单

**新增**
- `docs/specs/002-income-calendar.md`（本文件）
- `docs/specs/002-income-calendar.prompt.md`（给实现模型的精简 Prompt）
- `portfolio_api/routers/calendar.py`
- `portfolio_api/tests/test_calendar.py`
- `portfolio_web/src/router.js`
- `portfolio_web/src/composables/usePortfolioContext.js`
- `portfolio_web/src/utils/calendar.js` + `calendar.test.mjs`
- `portfolio_web/src/utils/calendarColors.js`
- `portfolio_web/src/views/CalendarView.vue`
- `portfolio_web/src/components/YearGrid.vue`
- `portfolio_web/src/components/MonthlyStackedBarChart.vue`
- `portfolio_web/src/components/MonthSheet.vue`
- `portfolio_web/src/components/ManualDividendForm.vue`

**修改**
- `portfolio_api/models.py`（新增两表 + 关系）
- `portfolio_api/schemas.py`
- `portfolio_api/portfolio_math.py`（新增 `classify_source` / `classify_status`）
- `portfolio_api/routers/stocks.py`（新增 `fetch_all_dividend_records`）
- `portfolio_api/main.py`（注册 router）
- `portfolio_web/src/main.js`（启用 router）
- `portfolio_web/src/App.vue`（tab 头 + provide）
- `portfolio_web/src/views/Portfolio.vue`（用 usePortfolioContext）
- `portfolio_web/src/api/calendar.js`（新文件，跨 6 个端点；不 mock 兜底，错误透传 detail）
- `portfolio_web/package.json`（+`vue-router@4`）
- `README.md`

**不得修改**
- `routers/assets.py`（除确认 refresh-price 已正确 invalidate_dividend_cache 之外不动）
- `routers/portfolios.py` 现有 summary 计算
- 既有测试文件的断言
- `styles/main.css` 既有规则
- `ChartPie.vue` / `PortfolioCharts.vue` / `AssetList.vue` / `AssetItem.vue` / `AssetForm.vue`

---

## 13. 实现者自查

- [ ] 既有 112 后端 + 40 前端测试全绿且断言未改
- [ ] 既有 `refresh-price` 的 `invalidate_dividend_cache` 没被误删或重复
- [ ] `price_source=mock` 与 `cost_price` 不参与 calendar 计算
- [ ] 月 Sheet 点确认 / 补录 后 calendar 数字口径自洽（amount 不重复加）
- [ ] manual 覆盖 akshare 同 `(code, ex_date)` 的实现
- [ ] 跨年 / 跨月 / 切 tab 时不闪烁
- [ ] 仅 `vue-router@4` 一个新依赖
- [ ] README 已更新
- [ ] 没有外推 akshare 收录之外的未来月份
