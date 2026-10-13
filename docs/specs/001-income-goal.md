# 001 · 年收入目标设定（Income Goal）

> 本文件是可直接交付给实现模型的完整实现指令。所有口径、契约、边界都已定死；
> 实现时**不要**自行改口径，遇到本文件未覆盖的情况按「第 9 节 边界与异常」的兜底原则处理并在 PR 描述里说明。

---

## 1. 一句话需求

在每个投资组合下可维护多个「年收入目标」。选中某个目标后，展示：

- 当前：一年多少、月均多少
- 目标：一年多少、月均多少
- 缺口与进度（进度条 + 还差多少 + 超额多少）
- 两条达成路径：① 收益率不变，还需再投入多少本金 ② 本金不变，TTM 收益率需要达到多少

---

## 2. 背景与现状（已在代码中核对，实现时可直接引用）

### 后端 `portfolio_api/`

| 位置 | 现状 |
|---|---|
| `models.py:7` | `Portfolio` 1:N `Asset`，`assets` 关系带 `cascade="all, delete-orphan"` |
| `models.py:18` | `Asset`：`quantity` / `price` / `cost_price`(NULL=未填，**负成本合法**) / `dividend_per_share` / `ttm_yield` / `price_source` |
| `routers/portfolios.py:73` | `GET /api/portfolios/{id}/summary` 内联计算：`total_market_value = Σ(quantity×price)`；`weighted_ttm_yield = Σ(quantity×price×ttm_yield) / total_market_value`（市值为 0 时取 0.0）；`expected_annual_income = Σ(quantity×dividend_per_share)` |
| `routers/portfolios.py:88-98` | 成本口径：只统计 `cost_price is not None` 的标的；`total_profit_percent` 在总成本 ≤ 0 时为 `None`（**沿用这个「无意义则返回 null」的既有约定**） |
| `schemas.py:147` | `PortfolioSummary`，字段带中文注释说明口径 |
| `schemas.py:59` | `PriceSource = Literal["real","mock","stale"]` |
| `main.py:25` | `Base.metadata.create_all(bind=engine)` —— **新表会自动创建，无需 Alembic** |
| `main.py:28-64` | `run_lightweight_migrations()`：幂等轻量迁移，白名单元组 `(表, 列, DDL, 回填 SQL 或 None)` |
| `main.py:92-94` | `include_router` × 3：`portfolios` / `assets` / `stocks` |
| `tests/conftest.py:27` | 每个测试前 `drop_all` + `create_all` + `init_sample_data()`；`client` / `db_session` 两个 fixture 可直接用 |

**后端现有测试 112 例**（`uv run pytest -q`）：`test_api.py` / `test_cost.py` / `test_etf.py` / `test_refresh_freshness.py` / `test_ttm.py`。**改动后必须全绿，不许修改既有断言。**

### 前端 `portfolio_web/src/`

| 位置 | 现状 |
|---|---|
| `views/Portfolio.vue` | header 内 `.portfolio-selector` 组合下拉；`loadData()` = `Promise.all([loadSummary(), loadAssets()])`；`handleRefreshPrice` / `handleRefreshAllPrices` 刷新后只重算 summary |
| `components/PortfolioOverview.vue` | 5 张概览卡，**样式来自全局 `styles/main.css`（该组件没有 `<style>`）** |
| `styles/main.css:7-22` | CSS 变量：`--primary-color #2563eb`、`--success-color #22c55e`、`--danger-color #ef4444`、`--warning-color #f59e0b`、`--radius 8px`、`--shadow` |
| `styles/main.css:149-218` | `.overview-grid`（`repeat(auto-fit, minmax(220px,1fr))`）、`.overview-item`、`.overview-item.highlight`、`.overview-desc` |
| `styles/main.css:169-180` | **红涨绿跌**：`.profit-up` = 红，`.profit-down` = 绿（A 股惯例，勿改语义） |
| `api/portfolio.js` | 读接口 `fetchWithFallback` 有假数据兜底；写接口（`refreshAssetPrice`）失败直接 `throw` |
| `utils/format.js` | `formatAmount`（千分位 + 固定 2 位，**全站不用「万/亿」**）、`formatQuantity`、`formatCost`、`formatSigned` |
| `components/AssetForm.vue` | 弹窗表单的实现范例（600 行，含遮罩、校验、提交流程、scoped style）——`GoalForm.vue` 请对齐它的结构与类名风格 |
| `components/AssetList.vue:176-179` | localStorage key 命名规范：`portfolio.<feature>.<field>.v1` |
| `package.json` | `test` = `node --test src/utils/*.test.mjs`（纯函数单测，**新增 `utils/goal.test.mjs` 会被自动纳入**）；Node v24 |

**前端现有测试 40 例**（`npm test`）。注意：`node --test` 环境下**没有保证存在 `globalThis.localStorage`**，任何 localStorage 访问必须可注入、可降级（见 §7.4）。

---

## 3. 已锁定的口径决策（D1–D10，实现时不得更改）

| 编号 | 决策 |
|---|---|
| **D1** | 目标**绑定组合**：每个组合有自己独立的目标列表。切换组合 → 目标下拉只列该组合的目标。删除组合 → 级联删除其目标 |
| **D2** | 「当前收入」口径 = 现有 `expected_annual_income` = `Σ(quantity × dividend_per_share)`（**不是** 市值 × 加权 TTM）。月均 = 年 ÷ 12 |
| **D3** | 「所需 TTM 收益率」= `目标年收入 ÷ 当前持仓市值 × 100`，单位**百分数**（4.5 表示 4.5%）。为防止与 D2 口径打架，同屏展示的「当前收益率」= `当前年收入 ÷ 当前持仓市值 × 100`，两者相减得到「还需提升的百分点」。**加权 TTM 收益率只作为默认假设值展示，不作为这两个数字的口径** |
| **D4** | 两条路径并列：① 收益率不变 → `还需再投入本金 = 缺口 ÷ 假设收益率`；② 本金不变 → `所需 TTM 收益率` 与 `当前收益率` 的差值（百分点） |
| **D5** | 假设收益率的默认值 = 组合加权 TTM 收益率；允许用户手动覆盖；**不持久化**（刷新 / 切换组合 / 切换目标后回到默认值） |
| **D6** | 目标字段只有两个：`name`（1–50 字，去首尾空白后非空）+ `annual_income_target`（元，> 0）。**同一组合内目标名不允许重复**（去空白后比较），重名返回 409 |
| **D7** | 金额一律以**元**为单位存取与展示，输入框用「元」，展示走 `formatAmount`（千分位 + 2 位小数，禁止「万/亿」） |
| **D8** | 「上次选中的目标」按组合记到 localStorage：key `portfolio.goals.selectedId.v1`，value `{"<portfolioId>": <goalId>}`。手填的假设收益率不记 |
| **D9** | 目标区块只展示**选中目标**的详情；目标列表本身不带进度徽标 |
| **D10** | 所有目标计算放在**后端**（口径唯一、可测），前端只渲染 + 格式化。前端只做与展示相关的纯函数（月均换算、进度条宽度、格式化） |

---

## 4. 目标 / 非目标

### 目标
1. 后端：`goals` 表 + CRUD API + 一个纯计算的进度 API
2. 后端：把 summary 里已有的两个口径抽成**共享纯函数**，goal 计算复用，避免两处公式漂移
3. 前端：组合 header 的「目标：」下拉 + 概览下方的「🎯 年收入目标」区块（含进度条、6 个数字、两条路径、假设收益率输入、空态/加载态/错误态）
4. 前端：目标的新增 / 编辑 / 删除弹窗
5. 测试：后端新增 `tests/test_goals.py`；前端新增 `src/utils/goal.test.mjs`；既有测试全绿
6. 文档：README 补功能、API 说明、测试数、目录结构

### 非目标（明确不做，避免实现模型扩大范围）
- ❌ 不做 Alembic / 数据库版本化（新表由 `create_all` 建，不 ALTER 既有表）
- ❌ 不做用户认证、多用户数据隔离
- ❌ 不做目标达成度的历史快照 / 趋势图 / 通知提醒
- ❌ 不做跨组合的合计目标（只按单个组合衡量）
- ❌ 不做第三种「同时加仓 + 提收益率」的双变量组合方案
- ❌ 不改 `PortfolioSummary` 的字段、语义与数值（只允许内部重构成调用共享函数）
- ❌ 不引入任何新依赖（前后端都不加）
- ❌ 不改红涨绿跌的盈亏配色语义
- ❌ 不做目标的排序 / 置顶 / 备注字段

---

## 5. 数据模型与迁移

### 5.1 表结构

```python
class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    # 目标年收入（元），恒 > 0，由 API 校验保证
    annual_income_target = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    portfolio = relationship("Portfolio", back_populates="goals")
```

`Portfolio` 增加：

```python
goals = relationship("Goal", back_populates="portfolio", cascade="all, delete-orphan")
```

### 5.2 迁移

- `create_all` 会自动建 `goals` 表，**无需 ALTER，无需改 `run_lightweight_migrations()`**。
- `portfolio_id` 上加 `index=True`；**不要**加数据库级唯一约束（重名校验走 API，便于返回可读的 409 文案）。
- SQLite 默认**不启用外键约束**，级联删除靠 SQLAlchemy 的 `cascade="all, delete-orphan"`（与 `Asset` 现有做法一致，勿引新机制）。

---

## 6. 后端 API 契约

### 6.1 共享纯计算模块（新增 `portfolio_api/portfolio_math.py`）

新增模块，放**无副作用纯函数**，供 `routers/portfolios.py` 与 `routers/goals.py` 共同调用：

```python
def total_market_value(assets) -> float            # Σ(quantity × price)
def expected_annual_income(assets) -> float        # Σ(quantity × dividend_per_share)
def weighted_ttm_yield(assets) -> float            # Σ(q×p×ttm_yield)/市值，市值<=0 → 0.0
def has_mock_price(assets) -> bool                 # 任一 price_source == "mock"
```

要求：
- 逐字保持 `routers/portfolios.py:81-89` 的现有数值行为（含「市值为 0 时 weighted_ttm_yield = 0.0」），重构后 summary 的返回必须**完全不变**
- 不许在这些函数里访问数据库或抛业务异常

### 6.2 Schemas（`schemas.py` 追加）

```python
class GoalBase(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    annual_income_target: float = Field(gt=0, le=1_000_000_000)

class GoalCreate(GoalBase): pass

class GoalUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    annual_income_target: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)

class GoalResponse(GoalBase):
    id: int
    portfolio_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class GoalProgressResponse(BaseModel):
    goal_id: int
    goal_name: str
    portfolio_id: int

    # ── 当前（D2 口径）
    current_annual_income: float
    current_monthly_income: float

    # ── 目标
    target_annual_income: float
    target_monthly_income: float

    # ── 缺口 / 进度
    gap_annual: float            # max(0, 目标 − 当前)
    gap_monthly: float
    progress_percent: float      # 当前 / 目标 × 100，>100 时**保留原值**不做截断
    is_achieved: bool
    surplus_annual: float        # max(0, 当前 − 目标)
    surplus_monthly: float

    # ── 路径 ①：收益率不变，还需多少本金
    additional_capital: Optional[float]       # 缺口 ÷ (假设收益率/100)；假设收益率<=0 → null
    assumed_yield_percent: float              # 本次实际采用的假设收益率（百分数）
    assumed_yield_is_default: bool             # true = 未传 assumed_yield，用的组合加权 TTM

    # ── 路径 ②：本金不变，收益率需达到多少
    required_yield_percent: Optional[float]    # 目标年收入 ÷ 市值 × 100；市值<=0 → null
    current_yield_percent: Optional[float]     # 当前年收入 ÷ 市值 × 100；市值<=0 → null
    yield_gap_points: Optional[float]          # required − current；任一为 null → null

    # ── 参考与提示
    total_market_value: float
    weighted_ttm_yield: float                  # 仅供对照 / 说明默认值来源
    asset_count: int
    has_mock_price: bool
    calc_note: str                             # 无异常时为空串，见 §6.5
```

### 6.3 端点

| 方法 | 路径 | 请求体 / 查询参数 | 成功响应 |
|---|---|---|---|
| GET | `/api/portfolios/{pid}/goals` | — | `200 [GoalResponse]`，排序 `created_at ASC, id ASC` |
| POST | `/api/portfolios/{pid}/goals` | `GoalCreate` | `201 GoalResponse` |
| PUT | `/api/goals/{gid}` | `GoalUpdate`（**部分更新**语义，与 `PortfolioUpdate` 一致：字段为 `None` 则不改） | `200 GoalResponse` |
| DELETE | `/api/goals/{gid}` | — | `200 {"message": "Goal deleted"}` |
| GET | `/api/portfolios/{pid}/goals/{gid}/progress` | 查询参数 `assumed_yield`（可选，float，`ge=0, le=100`，**百分数**，如 `4.5`） | `200 GoalProgressResponse` |

错误约定（全部用 `HTTPException`，`detail` 为可直接给用户看的中文）：

| 场景 | 状态码 | `detail` |
|---|---|---|
| 组合不存在（GET 列表 / POST / progress） | 404 | `Portfolio not found` |
| 目标不存在（PUT / DELETE / progress） | 404 | `Goal not found` |
| `gid` 不属于 `pid`（progress） | 404 | `Goal not found`（**不要**返回 400，避免泄露跨组合存在性） |
| 名称去空白后为空 / 目标金额 ≤ 0 / `assumed_yield` 越界 | 422 | 由 Pydantic 生成（`name` 需在路由里 strip 后再判空，见下） |
| 同一组合内重名（POST，以及 PUT 改名时排除自身） | 409 | `同名目标已存在` |

实现细节：
- `name` 在 create/update 中先 `strip()`，strip 后为空 → 抛 422（用 `HTTPException(422, "目标名称不能为空")`，避免依赖 Pydantic 的空白校验）
- 路由文件新增 `routers/goals.py`，`main.py` 里 `app.include_router(goals.router)`；router 前缀保持 `/api` 风格（例如 `APIRouter(prefix="/api", tags=["goals"])`，两个子路径分别写全）

### 6.4 计算口径（唯一实现）

```
current_annual  = Σ(quantity × dividend_per_share)          # 与 summary.expected_annual_income 完全一致
current_monthly = current_annual / 12
target_annual   = goal.annual_income_target
target_monthly  = target_annual / 12
gap_annual      = max(0, target_annual − current_annual)
gap_monthly     = gap_annual / 12
progress_percent= current_annual / target_annual × 100       # target > 0 由校验保证，无除零
is_achieved     = current_annual >= target_annual
surplus_annual  = max(0, current_annual − target_annual)
surplus_monthly = surplus_annual / 12

mv = total_market_value
assumed_yield_percent = 传入的 assumed_yield if 非 None else weighted_ttm_yield(assets)
assumed_yield_is_default = (传入的 assumed_yield is None)

additional_capital    = gap_annual / (assumed_yield_percent/100)   if assumed_yield_percent > 0 else None
required_yield_percent= target_annual  / mv × 100                  if mv > 0 else None
current_yield_percent = current_annual / mv × 100                  if mv > 0 else None
yield_gap_points      = required_yield_percent − current_yield_percent   # 任一为 None → None
```

小数不做四舍五入（原始 float 直接返回，格式化交给前端）。

### 6.5 `calc_note` 文案规则（按顺序取第一条命中的）

| 条件 | `calc_note` |
|---|---|
| `is_achieved` | `目标已达成` |
| `mv <= 0` | `当前持仓市值为 0，无法推算所需收益率；填写「假设加仓收益率」可估算所需本金` |
| `assumed_yield_percent <= 0` | `假设加仓收益率为 0，无法推算所需本金；填写一个大于 0 的收益率` |
| 其余 | `""` |

---

## 7. 前端实现

### 7.1 API 客户端（新增 `src/api/goal.js`）

```js
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export async function getGoals(portfolioId)                 // GET /api/portfolios/{id}/goals
export async function createGoal(portfolioId, payload)      // POST  → 201 body
export async function updateGoal(goalId, payload)           // PUT   → body
export async function deleteGoal(goalId)                    // DELETE→ body
export async function getGoalProgress(portfolioId, goalId, assumedYield)
// GET /api/portfolios/{pid}/goals/{gid}/progress[?assumed_yield=<number>]
// assumedYield 为 null/undefined/NaN 时**不拼**该查询参数（交给后端用默认值）
```

要求：
- **不做 mock 假数据兜底**（目标是用户数据，编造假目标比报错更糟），失败直接 `throw`
- 统一错误处理：非 2xx 时尝试解析响应体 `detail`，`throw new Error(detail || \`HTTP ${status}\`)`，让 409「同名目标已存在」能直接 toast 给用户
- 沿用 `portfolio.js` 的 `API_BASE` 写法

### 7.2 纯函数工具（新增 `src/utils/goal.js` + `src/utils/goal.test.mjs`）

只放展示相关逻辑，计算口径不在前端：

```js
export function toMonthly(annual)                     // 年 ÷ 12，非有限数 → 0
export function progressPercentWidth(percent)         // clamp 到 [0, 100]，非有限数 → 0（进度条宽度，D: 超额封顶）
export function formatYieldPercent(v)                 // 数值 → "4.50%"，null/undefined → "—"
export function pickInitialGoalId(goals, rememberedId) // remembered 命中列表则用它；否则首个目标 id；空列表 → null
export function readRememberedGoalId(portfolioId, storage)   // storage 见下
export function persistGoalId(portfolioId, goalId, storage)
```

`storage` 约定（**重要，保证可在 node --test 下单测**）：
- 两个读写函数的第三个参数默认为 `globalThis.localStorage`，调用方（组件）不传
- 内部所有访问包 `try/catch`：`storage` 为 `undefined`/`null`、抛异常、JSON 解析失败 → 静默降级（读返回 `null`，写直接忽略），**绝不抛出**
- key：`portfolio.goals.selectedId.v1`，value：`{"<portfolioId>": <goalId>}` 的 JSON
- `goalId` 为 `null` 时从对象里删除该组合的键（而不是写 `null`）

### 7.3 弹窗表单（新增 `src/components/GoalForm.vue`）

- 复刻 `AssetForm.vue` 的弹窗结构、遮罩层、类名风格与 scoped style
- 字段：
  - 目标名称（文本，必填，≤ 50 字，去首尾空白后非空）
  - 目标年收入（数字，必填，> 0，单位标注「元」）
- 实时预览（提升可用性，纯前端）：输入金额后在表单下方显示 `≈ ¥X.XX / 月`（用 `formatAmount(toMonthly(v))`）
- props：`goal`（编辑时传入，新增时 `null`）、`submitting`
- emits：`close`、`submit({ name, annual_income_target })`
- 提交中禁用按钮并显示「保存中...」，与 `AssetForm.vue` 一致
- 校验失败在字段下方显示红字提示，不发起请求

### 7.4 目标区块（新增 `src/components/PortfolioGoals.vue`）

**props**

| prop | 类型 | 说明 |
|---|---|---|
| `goals` | `Array` | 当前组合的目标列表 |
| `selectedGoalId` | `Number \| null` | 当前选中 |
| `progress` | `Object \| null` | `GoalProgressResponse` |
| `loading` | `Boolean` | 目标列表加载中 |
| `progressLoading` | `Boolean` | 进度重算中 |
| `error` | `String` | 加载失败的提示文案（可为空串） |

**emits**：`update:selectedGoalId`、`add`、`edit(goal)`、`remove(goal)`、`recalc(assumedYieldOrNull)`

**渲染结构（自上而下）**

1. 区块标题栏：`🎯 年收入目标` + 右侧「＋ 新增目标」「编辑」「删除」三个按钮
   - 无选中目标时，编辑/删除禁用
   - 删除前 `confirm('确定要删除目标「<name>」吗？')`（与 `AssetList` 删资产的交互一致）
2. 目标下拉（**只此一处**）：`.goal-selector`，`label` = `目标：`；选项为 `goals`；无目标时禁用并显示占位 `暂无目标`
3. 进度条：track + fill，`fill.style.width = progressPercentWidth(progress.progress_percent) + '%'`
   - 未达成：fill 用 `linear-gradient(90deg, var(--primary-color), var(--primary-light))`，右侧文字 `已完成 72.35%`
   - 已达成：fill 用 `linear-gradient(90deg, #059669, #34d399)`，右侧文字 `✅ 已达成`（绿色表示「达成」，与红涨绿跌的**盈亏**配色属于不同语义域，文档里注明即可）
   - `progressLoading` 时给进度条加 `.is-loading`（透明度 0.6 + 不改变宽度），不要整块闪
4. 数字网格（4 张卡，复用 `.overview-grid` / `.overview-item` 的视觉语言，用自己的 `.goal-grid` / `.goal-item` 类名 + scoped style）
   - 当前年收入：`¥{formatAmount(current_annual_income)}`，副行 `月均 ¥{formatAmount(current_monthly_income)}`
   - 目标年收入：`¥{formatAmount(target_annual_income)}`，副行 `月均 ¥{formatAmount(target_monthly_income)}`
   - 缺口：未达成 → `¥{formatAmount(gap_annual)}` + 副行 `月均 ¥{formatAmount(gap_monthly)}`；已达成 → `—`
   - 完成度：`{progress.progress_percent.toFixed(2)}%`
5. 两条达成路径（`is_achieved` 为 true 时**整块替换**为一行绿色文案：`已超出目标 ¥{formatAmount(surplus_annual)}/年（月均 ¥{formatAmount(surplus_monthly)}）`）
   - 路径 ①：`收益率不变，还需再投入本金` → `additional_capital` 为 null 时显示 `—` + `calc_note` 里的原因；否则 `¥{formatAmount(additional_capital)}`
   - 路径 ②：`本金不变，TTM 收益率需达到` → `required_yield_percent` 为 null 时显示 `—`；否则 `{formatYieldPercent(required_yield_percent)}`，副行 `当前 {formatYieldPercent(current_yield_percent)}，还需提升 {yield_gap_points.toFixed(2)} 个百分点`（差值 ≤ 0 时副行改为 `当前已不低于该水平`）
6. 假设收益率输入行：`label` = `假设加仓收益率`，数字输入框（`step="0.01"`，后缀 `%`），`InfoTooltip` 文案：`默认取当前组合的加权平均 TTM 收益率；改这里可以按你打算买入标的的收益率重算所需本金`
7. 提示区：
   - `progress.has_mock_price` → 黄色提示 `当前组合含模拟价格（网络拉取失败兜底），目标进度仅供参考`
   - `progress.calc_note` 非空且未用于路径 ① 的占位说明 → 灰色提示整行展示
8. 空态（`goals.length === 0`，非 loading）：`还没有目标，点击「＋ 新增目标」设置你的第一个年收入目标`
9. 错误态（`error` 非空）：红字提示 + 「重试」按钮，emit `recalc(null)`
10. 加载态（`loading`）：`加载目标...`（与 `Portfolio.vue` 现有 `加载汇总数据...` 的写法一致）

**假设收益率输入框行为（严格按此实现）**
- 初始值 = `progress.assumed_yield_percent`（保留最多 2 位小数显示），仅在 `dirty === false` 时随 `progress` 变化同步
- 用户改动 → `dirty = true`，**300ms 防抖**后 `emit('recalc', Number(value))`
- 用户清空输入框 → `dirty = false`，立即 `emit('recalc', null)`（表示用后端默认值）
- 非法输入（非数字 / < 0 / > 100）→ 输入框描红 + 本地提示，**不发请求**
- 切换目标或切换组合时，`Portfolio.vue` 会把覆盖值重置为 `null`，父组件需通过改变 `progress` 触发输入框回到默认值 —— 因此输入框必须监听 `progress.goal_id` 变化并把 `dirty` 复位为 `false`

### 7.5 接入 `views/Portfolio.vue`

新增状态：

```js
const goals = ref([])
const selectedGoalId = ref(null)
const goalProgress = ref(null)
const loadingGoals = ref(false)
const progressLoading = ref(false)
const goalError = ref('')
const assumedYieldOverride = ref(null)   // null = 用后端默认（组合加权 TTM）
```

数据流：
1. `loadData()` → `Promise.all([loadSummary(), loadAssets(), loadGoals()])`
2. `loadGoals()`：
   - `getGoals(pid)` → `goals.value`
   - `selectedGoalId.value = pickInitialGoalId(goals.value, readRememberedGoalId(pid))`
   - 列表为空 → 清掉该组合的记忆值，`goalProgress.value = null`
   - 失败 → `goals.value = []`，`goalError.value = '目标加载失败，请稍后重试'`，`toast.error(...)`
   - 结束后：若有选中目标 → `loadGoalProgress()`
3. `loadGoalProgress()`：
   - 无选中目标 → `goalProgress.value = null`，直接返回
   - `progressLoading = true` → `getGoalProgress(pid, gid, assumedYieldOverride.value)`
   - 失败 → `toast.error('目标进度计算失败，请稍后重试')`，保留上一次的 progress（不清空，避免数字闪没）
4. `watch(selectedGoalId)` → `persistGoalId(pid, id)` + `assumedYieldOverride.value = null` + `loadGoalProgress()`
5. `@recalc="(y) => { assumedYieldOverride.value = y; loadGoalProgress() }"`
6. `@add` → 打开 `GoalForm`（`editingGoal = null`）
7. `@edit(goal)` → 打开 `GoalForm`（`editingGoal = goal`）
8. 表单 `@submit`：
   - 新增 → `createGoal(pid, payload)`；成功后 `await loadGoals()`，并把 `selectedGoalId` 指向**新建目标**（`await nextTick()` 或直接按返回的 id 赋值后 `loadGoalProgress()`）
   - 编辑 → `updateGoal(gid, payload)`；成功后 `await loadGoals()` + `loadGoalProgress()`
   - 失败 → `toast.error(e.message)`（409 时用户会看到「同名目标已存在」），弹窗**不关闭**
9. `@remove(goal)` → `deleteGoal(gid)`；成功后 `await loadGoals()`；若删除的是当前选中目标，`loadGoals()` 内会重新选（记忆值失效 → 落到第一个目标）；列表空了 → 清记忆 + 空态
10. **刷新价格后必须重算目标**：`handleRefreshPrice` 与 `handleRefreshAllPrices` 里，除现有 `loadSummary()` / `loadData()` 外追加 `loadGoalProgress()`（市值与年收入都会变）
11. 组合切换（现有 `@change="loadData"`）→ 除 `loadData()` 外把 `assumedYieldOverride.value = null`、`goalProgress.value = null`、`goalError.value = ''`

模板改动：
- header 的 `.header-actions` 内在组合选择器之后新增 `.goal-selector`：`<label>目标：</label>` + `<select v-model="selectedGoalId" :disabled="goals.length === 0">`，空白选项 `暂无目标`
- `<PortfolioOverview>` 之后、`<PortfolioCharts>` 之前插入 `<PortfolioGoals ... />`
- 新增 `<GoalForm v-if="showGoalModal" ... />`

**注意**：`PortfolioCharts` 是 `defineAsyncComponent`（echarts 460KB 延迟加载），`PortfolioGoals` 是轻量组件，**同步 import**，不要加进异步链。

### 7.6 视觉与文案约定

- 金额一律 `formatAmount`；收益率一律 2 位小数 + `%`；百分点差值用 `toFixed(2)`
- 中文文案、不出现英文缩写以外的术语；tooltip 用现有 `InfoTooltip.vue`
- 卡片圆角 `var(--radius)`，主色 `var(--primary-color)`，达成绿 `#059669 / #34d399`（与 `.overview-item.highlight` 同色系），警告 `var(--warning-color)`
- 响应式沿用 `repeat(auto-fit, minmax(220px, 1fr))` 的网格思路
- 组件用 **scoped style**（与大多数组件一致）；**不要**改 `styles/main.css` 里既有的 `.overview-*` / `.profit-*` 规则

---

## 8. 边界与异常清单（逐条必须有测试覆盖或明确实现）

| # | 场景 | 期望行为 |
|---|---|---|
| 1 | 组合无任何资产（市值 0，年收入 0） | `required_yield_percent` / `current_yield_percent` / `yield_gap_points` = `null`；`additional_capital` 走假设收益率（默认加权 TTM = 0 → `null`）；`calc_note` 提示填写假设收益率 |
| 2 | 用户手填假设收益率 4.5，市值 0 | `additional_capital = gap_annual / 0.045`，正常给出金额 |
| 3 | 假设收益率传 0 | `additional_capital = null`，`assumed_yield_is_default = false` |
| 4 | 假设收益率传负数或 > 100 | 422（`assumed_yield` 加 `ge=0, le=100`） |
| 5 | 已达成（当前 ≥ 目标） | `is_achieved = true`，`gap_*` = 0，`surplus_*` > 0，`additional_capital = 0`，`calc_note = "目标已达成"`；前端两条路径替换为超额文案 |
| 6 | 当前正好等于目标 | `is_achieved = true`，`surplus_*` = 0（显示「已超出 ¥0.00」可接受，但更优：文案改为`已达成目标`） |
| 7 | 目标年收入输入 0 / 负数 / 非数字 | 后端 422；前端字段级校验拦截，不发请求 |
| 8 | 目标名为纯空格 | 后端 422「目标名称不能为空」；前端拦截 |
| 9 | 同一组合重名（新增 / 改名撞已有） | 409「同名目标已存在」；不同组合之间允许同名 |
| 10 | 组合 A 的目标 id 拿去请求组合 B 的 progress | 404 `Goal not found` |
| 11 | 删除组合 | 其目标随 `cascade` 一起删除，不留孤儿行（测试直接查 `goals` 表） |
| 12 | 组合内存在 `price_source == "mock"` 的标的 | `has_mock_price = true`，前端黄色提示 |
| 13 | 标的成本为负 / 成本为 NULL | **不影响**收入与目标计算（口径只依赖 `quantity` / `price` / `dividend_per_share`）——必须有测试锁死，防止实现模型误用成本口径 |
| 14 | `weighted_ttm_yield` 与「当前收益率」数值不一致 | 正常现象（D3 口径差异）；前端不得用加权 TTM 参与进度/缺口计算，仅在 tooltip 中说明它是默认值来源 |
| 15 | 后端不可用 | 前端读接口 throw → `goals = []` + 错误态 + toast；已在页面上的旧进度保留不闪 |
| 16 | localStorage 不可用（隐私模式 / node 测试环境） | 静默降级为「不记忆」，功能仍可用 |
| 17 | 记忆的目标已被删除 | `pickInitialGoalId` 回退到列表第一个；列表为空则 `null` 并清理记忆键 |
| 18 | 金额很大（如 1 亿） | `formatAmount` 给出完整千分位数字；卡片字号按容器宽度缩放（沿用 `.overview-item .value` 的 `clamp(15px, 8.5cqw, 24px)` 思路） |
| 19 | 快速连打假设收益率输入框 | 300ms 防抖，仅最后一次发请求；请求期间不清空已显示数字 |

---

## 9. 实现步骤拆解（含建议 write scope）

> 步骤间有依赖，按序执行。每步结束跑一次对应测试，不要攒到最后。

| 步骤 | 内容 | 文件 / write scope |
|---|---|---|
| **S1** | 新增 `portfolio_math.py` 纯函数；把 `routers/portfolios.py` 的 summary 改为调用它 | `portfolio_api/portfolio_math.py`(新)、`portfolio_api/routers/portfolios.py` |
| **S2** | 跑 `uv run pytest -q` 确认既有 112 例仍全绿（**此步不绿不许继续**） | — |
| **S3** | `Goal` 模型 + `Portfolio.goals` 关系 | `portfolio_api/models.py` |
| **S4** | Goal 相关 schemas | `portfolio_api/schemas.py` |
| **S5** | 新增 `routers/goals.py`（CRUD + progress + 重名/归属/校验），`main.py` 注册 router | `portfolio_api/routers/goals.py`(新)、`portfolio_api/main.py` |
| **S6** | 后端测试 `tests/test_goals.py`（用例清单见 §11.1），跑通 | `portfolio_api/tests/test_goals.py`(新) |
| **S7** | 前端 API 客户端（无 mock 兜底 + detail 错误透传） | `portfolio_web/src/api/goal.js`(新) |
| **S8** | 前端纯函数 + 单测（`node --test` 可跑） | `portfolio_web/src/utils/goal.js`(新)、`portfolio_web/src/utils/goal.test.mjs`(新) |
| **S9** | `GoalForm.vue` 弹窗 | `portfolio_web/src/components/GoalForm.vue`(新) |
| **S10** | `PortfolioGoals.vue` 区块（含进度条、防抖输入、空/加载/错误态） | `portfolio_web/src/components/PortfolioGoals.vue`(新) |
| **S11** | 接入 `Portfolio.vue`（header 下拉、状态流、刷新价格后重算） | `portfolio_web/src/views/Portfolio.vue` |
| **S12** | README 更新（功能列表 / API 表 / 目录结构 / 测试数） | `README.md` |
| **S13** | 端到端自验（见 §10） | — |

---

## 10. 验收标准（可执行，必须逐条跑过）

```bash
# 1) 后端既有测试不回归 + 新增目标测试全绿
cd portfolio_api && uv run pytest -q

# 2) 静态导入校验（README 里的既有做法）
cd portfolio_api && uv run python -c "import main; print(len(main.app.routes))"

# 3) 前端纯函数测试
cd portfolio_web && npm test

# 4) 前端生产构建必须通过（构建失败 = 未完成）
cd portfolio_web && npm run build
```

手动验收（后端 `uv run uvicorn main:app --reload --port 8000` + 前端 `npm run dev`，浏览器 http://localhost:5173）：

1. 选中组合 → header 出现「目标：暂无目标」下拉（禁用），概览下方显示空态引导
2. 点「＋ 新增目标」→ 填 `生活自由线` / `120000` → 保存 → 下拉自动选中新目标
3. 看板显示：当前年收入（等于概览卡「预计年收入」）、当前月均、目标 `¥120,000.00`、目标月均 `¥10,000.00`、缺口、进度 xx%
4. 两条路径都给出金额与百分数；把假设收益率从默认值改成 `3` → 防抖后仅一次请求，本金数字变大
5. 再加一个目标 `加菜线` / `60000` → 切换下拉，数字随之变化；刷新页面 → 仍停在最后选中的目标（localStorage）
6. 把目标金额改到低于当前年收入 → 进度条封顶 100% 变绿 + ✅ + 显示超额金额，两条路径替换为超额文案
7. 新增一个与已有目标同名的目标 → toast 显示「同名目标已存在」，弹窗不关闭
8. 删除组合 → 其目标一并消失（`sqlite3 portfolio.db "select * from goals"` 无残留）
9. 点「刷新全部价格」→ 概览与目标看板的数字同时更新
10. 组合里放一个 mock 价格的标的（断网刷新）→ 目标区块出现黄色「含模拟价格」提示

**DoD：以上 4 条命令全绿 + 10 条手动验收通过 + 既有 UI 无回归（概览、两张饼图、资产列表的列设置/排序/宽度拖拽仍正常）。**

---

## 11. 测试清单

### 11.1 后端 `portfolio_api/tests/test_goals.py`（用 `client` / `db_session` fixture）

1. `GET /api/portfolios/{pid}/goals` 空列表返回 `[]`
2. `POST` 创建成功 → 201、字段回显、`GET` 列表能查到
3. `POST` 到不存在的组合 → 404
4. `POST` `annual_income_target = 0` / 负数 → 422
5. `POST` `name = "   "` → 422「目标名称不能为空」
6. `POST` 同组合重名 → 409；**不同组合**用同名 → 201
7. `PUT` 改名 + 改金额成功；`PUT` 改成与同组另一目标同名 → 409；`PUT` 只传 `name` 时金额不变（部分更新语义）
8. `PUT` / `DELETE` 不存在的目标 → 404
9. `DELETE` 目标成功 → 再 `GET` 列表已不含；`DELETE` 组合后直接查 `goals` 表无该组合残留（级联）
10. `progress` 基础计算（`db_session` 直接插资产：如 quantity=1000 / price=10 / dividend_per_share=1.2 → 市值 10000、年收入 1200；目标 12000）→ 断言 `current_annual_income=1200`、`current_monthly_income=100`、`gap_annual=10800`、`gap_monthly=900`、`progress_percent=10`、`required_yield_percent=120`、`current_yield_percent=12`、`yield_gap_points=108`、`is_achieved=False`
11. `progress` 默认假设收益率 = 组合加权 TTM（构造两个标的验证加权值），`assumed_yield_is_default=True`，`additional_capital = gap / (加权/100)`
12. `progress` 传 `assumed_yield=5` → `assumed_yield_is_default=False`，`additional_capital` 按 5% 算
13. `progress` 传 `assumed_yield=0` → `additional_capital=None`；传 `-1` / `200` → 422
14. `progress` 市值 0（组合无资产）→ `required_yield_percent` / `current_yield_percent` / `yield_gap_points` 全 `None`，`calc_note` 非空；手填 `assumed_yield=4` 时 `additional_capital` 有值
15. `progress` 已达成 → `is_achieved=True`、`gap_annual=0`、`surplus_annual` 正确、`additional_capital=0`、`calc_note="目标已达成"`
16. `progress` 目标不属于该组合 / 目标不存在 → 404
17. `progress` 含 `price_source="mock"` 的标的 → `has_mock_price=True`；全 real → `False`
18. 成本口径隔离：资产 `cost_price=-5`（负成本）与 `cost_price=None` → 收入/进度数字与不设成本时**完全一致**
19. **回归锁定**：`GET /api/portfolios/{pid}/summary` 的既有字段在重构后数值不变（尤其市值为 0 时 `weighted_ttm_yield == 0.0`）

### 11.2 前端 `portfolio_web/src/utils/goal.test.mjs`（`node --test`）

1. `toMonthly`：正常值 / `null` / 字符串数字 / `NaN` → 0
2. `progressPercentWidth`：`72.35 → 72.35`；`0 → 0`；`-5 → 0`；`120 → 100`；`null/NaN → 0`
3. `formatYieldPercent`：`4.5 → "4.50%"`；`null → "—"`；`0 → "0.00%"`
4. `pickInitialGoalId`：记忆命中；记忆失效 → 首个；空列表 → `null`；`goals` 为 `undefined` → `null`
5. `readRememberedGoalId` / `persistGoalId`：用**假 storage 对象**（`getItem`/`setItem` 用 Map 实现）验证写入/读回/`goalId=null` 时删键
6. storage 降级：传 `undefined`、传会抛异常的假 storage、存了非法 JSON → 读返回 `null`，写不抛异常

---

## 12. 交付物清单

**新增**

- `docs/specs/001-income-goal.md`（本文件）
- `portfolio_api/portfolio_math.py`
- `portfolio_api/routers/goals.py`
- `portfolio_api/tests/test_goals.py`
- `portfolio_web/src/api/goal.js`
- `portfolio_web/src/utils/goal.js`
- `portfolio_web/src/utils/goal.test.mjs`
- `portfolio_web/src/components/GoalForm.vue`
- `portfolio_web/src/components/PortfolioGoals.vue`

**修改**

- `portfolio_api/models.py`、`portfolio_api/schemas.py`、`portfolio_api/main.py`、`portfolio_api/routers/portfolios.py`
- `portfolio_web/src/views/Portfolio.vue`
- `README.md`

**不得修改**

- `portfolio_web/src/styles/main.css` 既有规则（可新增 `goal` 相关规则，但不得改动 `.overview-*` / `.profit-*`）
- 既有测试文件的断言
- `portfolio_web/src/components/AssetList.vue`、`ChartPie.vue`、`PortfolioCharts.vue`、`AssetItem.vue`、`AssetForm.vue`（新增表单要独立成 `GoalForm.vue`，不要往 `AssetForm.vue` 里塞）

---

## 13. 实现者自查清单（提交前逐条打勾）

- [ ] `uv run pytest -q` 全绿，既有 112 例断言未被修改
- [ ] `npm test` 全绿（原 40 例 + 新增 goal 用例）
- [ ] `npm run build` 成功
- [ ] summary 的响应字段与重构前逐一对比一致（含市值为 0 的边界）
- [ ] 所有「无意义」的数值字段返回 `null` 而不是 `0`（沿用项目既有约定）
- [ ] 目标进度**没有**用到成本 `cost_price` 口径
- [ ] 「当前收益率」与「加权 TTM 收益率」的差异在 UI 上有 tooltip 解释
- [ ] 假设收益率输入有 300ms 防抖，快速连打只发一次请求
- [ ] 切换目标 / 组合后，假设收益率回到默认值（不残留上次手填值）
- [ ] localStorage 不可用时功能不崩、不报错
- [ ] 删除组合后 `goals` 表无孤儿行
- [ ] 未引入任何新依赖
- [ ] README 已更新（功能、API、目录结构、测试数）
