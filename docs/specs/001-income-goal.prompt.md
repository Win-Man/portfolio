# 交付给实现模型的 Prompt（与 docs/specs/001-income-goal.md 配套使用）

> 用法：把本文件全文 + `docs/specs/001-income-goal.md` 一起交给实现模型（或直接粘贴下面 `---` 之间的内容）。
> 两者冲突时，以 `001-income-goal.md` 为准。

---

你是一名全栈工程师，请在一个已有的 FastAPI + Vue 3 项目中实现「年收入目标设定」功能。

## 唯一事实来源

仓库里已有完整实现指令：`docs/specs/001-income-goal.md`。
**请先完整读这份文件，然后按它实现。** 下面是摘要，冲突时一律以该文件为准。

## 动手前必读的文件

后端：`portfolio_api/models.py`、`schemas.py`、`routers/portfolios.py`、`main.py`、`tests/conftest.py`
前端：`portfolio_web/src/views/Portfolio.vue`、`components/PortfolioOverview.vue`、`components/AssetForm.vue`、`api/portfolio.js`、`utils/format.js`、`styles/main.css`

## 需求

每个投资组合下可维护多个「年收入目标」（字段只有：目标名称 + 目标年收入，单位元）。
选中某个目标后展示：

- 当前：一年多少、月均多少
- 目标：一年多少、月均多少
- 缺口 + 进度条 + 达成/超额状态
- 两条达成路径：① 收益率不变，还需再投入多少本金 ② 本金不变，TTM 收益率需要达到多少（并给出当前收益率与还需提升的百分点）

## 锁定的口径（不得自行更改）

1. **D1** 目标绑定组合；切换组合只显示该组合的目标；删除组合级联删除其目标。
2. **D2** 「当前收入」= `Σ(quantity × dividend_per_share)`，必须与现有 `summary.expected_annual_income` 完全一致；月均 = 年 ÷ 12。
3. **D3** 收益率路径只用「收入 ÷ 持仓市值」这一套口径：`所需收益率 = 目标年收入 ÷ 市值 × 100`、`当前收益率 = 当前年收入 ÷ 市值 × 100`。**加权 TTM 收益率只作为「默认假设加仓收益率」的来源，绝不参与进度/缺口计算**，并在 UI 用 tooltip 说明两者差异。
4. **D4** 假设加仓收益率默认取组合加权 TTM，允许用户手填覆盖，**不持久化**（切换目标/组合、刷新页面后回到默认值）。
5. **D5** 金额单位一律「元」，展示用现有 `formatAmount`（千分位 + 2 位小数，**禁止「万/亿」**）；收益率 2 位小数 + `%`。
6. **D6** 同一组合内目标名不允许重复（按去首尾空白后比较）→ 409 `同名目标已存在`；名称为纯空白 → 422 `目标名称不能为空`；不同组合之间允许同名。
7. **D7** 所有「无意义」的数值字段返回 `null` 而不是 `0`（沿用 `summary.total_profit_percent` 的既有约定）。
8. **D8** 所有计算放后端（口径唯一、可测），前端只做展示与格式化。
9. **D9** 无持仓（市值 0）时收益率类字段全部 `null`；用户手填假设收益率后仍要能算出所需本金。
10. **D10** 不引入任何新依赖；不加 Alembic（新表由 `main.py` 里已有的 `Base.metadata.create_all` 自动创建，不需要 ALTER 既有表）。

## 计算口径（严格照此实现）

```
current_annual   = Σ(quantity × dividend_per_share)
current_monthly  = current_annual / 12
target_annual    = goal.annual_income_target
target_monthly   = target_annual / 12
gap_annual       = max(0, target_annual − current_annual)
gap_monthly      = gap_annual / 12
progress_percent = current_annual / target_annual × 100      # >100 保留原值，不截断
is_achieved      = current_annual >= target_annual
surplus_annual   = max(0, current_annual − target_annual)
surplus_monthly  = surplus_annual / 12

mv = 总持仓市值
assumed_yield_percent = 传入的 assumed_yield（百分数）若为 None 则取组合加权 TTM
additional_capital    = gap_annual / (assumed_yield_percent/100)   # 假设收益率 <=0 → null
required_yield_percent= target_annual  / mv × 100                  # mv <=0 → null
current_yield_percent = current_annual / mv × 100                  # mv <=0 → null
yield_gap_points      = required_yield_percent − current_yield_percent  # 任一 null → null
```

小数不做四舍五入，原样返回，格式化交给前端。

`calc_note` 按顺序取第一条命中：已达成 → `目标已达成`；市值 ≤0 → `当前持仓市值为 0，无法推算所需收益率；填写「假设加仓收益率」可估算所需本金`；假设收益率 ≤0 → `假设加仓收益率为 0，无法推算所需本金；填写一个大于 0 的收益率`；其余 → 空串。

## API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portfolios/{pid}/goals` | 200 `[GoalResponse]`，排序 `created_at ASC, id ASC` |
| POST | `/api/portfolios/{pid}/goals` | 201 `GoalResponse` |
| PUT | `/api/goals/{gid}` | 200，**部分更新**语义（字段为 null 则不改） |
| DELETE | `/api/goals/{gid}` | 200 `{"message": "Goal deleted"}` |
| GET | `/api/portfolios/{pid}/goals/{gid}/progress?assumed_yield=4.5` | 200 `GoalProgressResponse`；`assumed_yield` 为**百分数**，`ge=0, le=100`，省略时用组合加权 TTM |

错误：组合不存在 → 404 `Portfolio not found`；目标不存在、或 `gid` 不属于 `pid` → 404 `Goal not found`（不要返回 400）；参数非法 → 422；同组合重名 → 409 `同名目标已存在`。

`GoalProgressResponse` 完整字段与 `GoalResponse`/`GoalCreate`/`GoalUpdate` 定义见 spec §6.2。核心字段：
`goal_id, goal_name, portfolio_id, current_annual_income, current_monthly_income, target_annual_income, target_monthly_income, gap_annual, gap_monthly, progress_percent, is_achieved, surplus_annual, surplus_monthly, additional_capital, assumed_yield_percent, assumed_yield_is_default, required_yield_percent, current_yield_percent, yield_gap_points, total_market_value, weighted_ttm_yield, asset_count, has_mock_price, calc_note`。

## 数据模型

新表 `goals`：`id`、`portfolio_id`(FK→portfolios.id，nullable=False，index=True)、`name`(String, nullable=False)、`annual_income_target`(Float, nullable=False)、`created_at`、`updated_at`。
`Portfolio` 增加 `goals = relationship("Goal", back_populates="portfolio", cascade="all, delete-orphan")`。
**不要**加数据库级唯一约束（重名校验走 API，便于返回可读的 409 文案）。

## 新增 / 修改文件

新增：
- `portfolio_api/portfolio_math.py` —— 纯函数 `total_market_value(assets)` / `expected_annual_income(assets)` / `weighted_ttm_yield(assets)` / `has_mock_price(assets)`
- `portfolio_api/routers/goals.py`
- `portfolio_api/tests/test_goals.py`
- `portfolio_web/src/api/goal.js` —— **不做 mock 假数据兜底**（目标是用户数据，编造假目标比报错更糟）；非 2xx 时解析响应体 `detail` 并 `throw new Error(detail || \`HTTP ${status}\`)`
- `portfolio_web/src/utils/goal.js` + `portfolio_web/src/utils/goal.test.mjs`
- `portfolio_web/src/components/GoalForm.vue`
- `portfolio_web/src/components/PortfolioGoals.vue`

修改：
- `portfolio_api/models.py`、`schemas.py`、`main.py`（注册 router）、`routers/portfolios.py`（summary 改为调用 `portfolio_math`，**返回值必须逐字段保持不变**）
- `portfolio_web/src/views/Portfolio.vue`、`README.md`

不得修改：`portfolio_web/src/styles/main.css` 既有的 `.overview-*` / `.profit-*` 规则；既有测试文件的断言；`AssetList.vue`、`ChartPie.vue`、`PortfolioCharts.vue`、`AssetItem.vue`、`AssetForm.vue`（新表单要独立成 `GoalForm.vue`）。

## 前端要点

- header 的组合选择器旁新增「目标：」下拉，作为**唯一的目标选择入口**；目标列表为空时禁用并显示占位「暂无目标」。
- 在 `PortfolioOverview` 与 `PortfolioCharts` 之间插入 `PortfolioGoals` 区块。`PortfolioCharts` 是 `defineAsyncComponent`（echarts 460KB），`PortfolioGoals` 必须**同步 import**，不要挂进异步链。
- 区块内容：下拉 + 进度条 + 4 张数字卡（当前年收入/目标年收入/缺口/完成度）+ 两条达成路径 + 假设收益率输入框 + mock 价格提示 + 空态/加载态/错误态。
- 进度条：宽度用 `clamp(percent, 0, 100)`；未达成用主色渐变，达成后填充变绿（`#059669 → #34d399`）+ `✅ 已达成`，并把两条路径整块替换成绿色超额文案 `已超出目标 ¥X/年（月均 ¥Y）`。
- 假设收益率输入框：初始值取 `progress.assumed_yield_percent`，仅在用户未改动时随 progress 同步；改动后 **300ms 防抖**只发一次请求；**清空输入 = 使用后端默认值**（不拼查询参数）；非法值（非数字 / <0 / >100）描红且不发请求；`progress.goal_id` 变化时把「已改动」标记复位。
- localStorage：key `portfolio.goals.selectedId.v1`，value `{"<portfolioId>": <goalId>}`；只记「上次选中的目标」，**不记**手填的假设收益率。读写函数签名 `readRememberedGoalId(portfolioId, storage = globalThis.localStorage)` / `persistGoalId(portfolioId, goalId, storage)`，内部全部包 `try/catch` 静默降级，绝不抛异常（`node --test` 环境下可能没有 localStorage，测试会注入假 storage）。
- 刷新单个/全部价格后，除现有的 `loadSummary()` / `loadData()` 外**必须追加 `loadGoalProgress()`**（市值与年收入都变了）。
- 增删改目标后：`await loadGoals()` 重新选目标（记忆值失效则落到第一个；新增后选中新目标；删除当前选中后自动改选），再 `loadGoalProgress()`。提交失败时 toast 显示后端 `detail`（如「同名目标已存在」）且**弹窗不关闭**。
- 金额一律 `formatAmount`；收益率 `toFixed(2) + '%'`；百分点差值 `toFixed(2)`。中文文案，tooltip 复用现有 `InfoTooltip.vue`。组件用 scoped style，不要改 `main.css` 既有规则。

## 测试与验收（必须全部通过，并在回复里逐条报告）

```bash
cd portfolio_api && uv run pytest -q
cd portfolio_api && uv run python -c "import main; print(len(main.app.routes))"
cd portfolio_web && npm test
cd portfolio_web && npm run build
```

- 后端既有 112 例不许修改断言，另按 spec §11.1 新增 19 个用例（CRUD、404/409/422、级联删除、progress 基础计算、假设收益率覆盖与越界、市值 0、已达成、跨组合归属、mock 标记、**负成本/NULL 成本不参与收入口径**、summary 回归锁定）
- 前端按 spec §11.2 新增 6 组用例（月均换算、进度宽度封顶、收益率格式化、初始目标选择、localStorage 读写、storage 降级）
- 最后按 spec §10 的 10 条手动验收自查，并说明任何偏离 spec 的地方
