# Portfolio · 投资组合管理

一个轻量的 A 股投资组合与分红透视工具。后端用 FastAPI + SQLite，前端是 Vue 3 + Vite。

```
portfolio/
├── portfolio_api/         # 后端 FastAPI
└── portfolio_web/         # 前端 Vue 3
```

## 功能

- 多个「投资组合」管理（增删改）
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
| GET / POST | `/api/portfolios` | 组合列表 / 新增 |
| PUT / DELETE | `/api/portfolios/{pid}` | 组合改名 / 删除（级联删除其资产与目标） |
| GET | `/api/portfolios/{pid}/summary` | 组合汇总（总市值、加权 TTM 收益率、预计年收入、成本盈亏） |
| GET / POST | `/api/portfolios/{pid}/assets` | 资产列表 / 新增 |
| PUT / DELETE | `/api/assets/{aid}` | 资产更新 / 删除 |
| POST | `/api/assets/{aid}/refresh-price`、`/api/portfolios/{pid}/assets/refresh-all-prices` | 刷新实时价格 |
| **GET / POST** | **`/api/portfolios/{pid}/goals`** | **年收入目标列表（created_at ASC, id ASC）/ 新增（201）；同组合重名 409** |
| **PUT / DELETE** | **`/api/goals/{gid}`** | **目标更新（部分更新语义）/ 删除** |
| **GET** | **`/api/portfolios/{pid}/goals/{gid}/progress?assumed_yield=4.5`** | **目标进度：当前 / 目标年收入与月均、缺口、进度、两条达成路径；`assumed_yield` 为可选假设加仓收益率（百分数，`0–100`，省略则取组合加权 TTM）** |

目标接口的错误约定：组合不存在 → `404 Portfolio not found`；目标不存在或不属于该组合 → `404 Goal not found`；名称为纯空白 / 金额 ≤ 0 / `assumed_yield` 越界 → `422`；同组合重名 → `409 同名目标已存在`。
所有「无意义」的数值（如市值为 0 时的所需收益率）返回 `null` 而不是 `0`，前端显示「—」。

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
├── portfolio_math.py   # ★ 组合口径唯一实现（市值 / 年收入 / 加权 TTM / mock 标记），summary 与目标进度共用
├── market.py           # ★ 代码规则唯一来源（市场前缀 / 是否 ETF）
├── routers/
│   ├── portfolios.py   # 组合 CRUD + summary
│   ├── assets.py       # 资产 CRUD + 刷新价格 + TTM 计算
│   ├── goals.py        # 年收入目标 CRUD + 进度计算
│   └── stocks.py       # 搜索 / 价格 / 分红（个股 + ETF 分派）
├── tests/
│   ├── conftest.py
│   ├── test_api.py     # 通用 API 测试
│   ├── test_goals.py   # 年收入目标：CRUD / 校验 / 归属 / 级联 / 进度口径
│   └── test_etf.py     # ETF 支持专项测试
└── pyproject.toml      # uv 依赖

portfolio_web/
├── vite.config.js      # 含 /api proxy
├── scripts/
│   └── verify-chart-tooltip.mjs  # 用 CDP 验证饼图悬浮提示
└── src/
    ├── App.vue
    ├── views/Portfolio.vue
    ├── components/
    │   ├── PortfolioOverview.vue
    │   ├── PortfolioGoals.vue    # ★ 年收入目标区块（进度条 / 两条路径 / 假设收益率）
    │   ├── PortfolioCharts.vue   # 两张分布饼图（容器）
    │   ├── ChartPie.vue          # 可复用饼图组件（ECharts 封装）
    │   ├── AssetList.vue
    │   ├── AssetItem.vue
    │   ├── AssetForm.vue
    │   ├── GoalForm.vue          # 目标的新增 / 编辑弹窗
    │   ├── Toast.vue
    │   └── InfoTooltip.vue
    ├── utils/
    │   ├── chartData.js          # 饼图数据整理（纯函数 + 单测）
    │   ├── chartData.test.mjs
    │   ├── goal.js               # 目标展示纯函数（月均 / 进度宽度 / 记忆选中）
    │   ├── goal.test.mjs
    │   └── chartColors.js        # 跨图共享配色
    ├── composables/
    │   └── useToast.js
    ├── api/
    │   ├── portfolio.js
    │   ├── goal.js               # 目标接口（无假数据兜底，透传后端 detail）
    │   └── stock.js
    └── styles/main.css
```

## 测试

```bash
# 后端：131 个用例（API / ETF / 历年分红口径 / 成本盈亏含负成本 / 刷新缓存时效 / 年收入目标 19 例）
cd portfolio_api && uv run pytest -q

# 前端：58 个用例（数字格式化、饼图数据整理、TOP N 取数、悬浮提示、目标展示纯函数 18 例）
cd portfolio_web && npm test

# 浏览器端验证饼图悬浮提示（需本地安装 Chrome，可用 CHROME_PATH 覆盖路径）
cd portfolio_web && npm run verify:chart
```

## 待办

- 数据库迁移机制（Alembic）
- 测试覆盖（前端 vitest）
- 用户认证（多用户之前必须加）
- ETF 分红面板的「年均每股分红」对月度分红标的语义失真