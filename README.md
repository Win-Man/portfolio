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
- **持仓分布可视化**：持仓市值分布 + 预计年收入分布两张饼图（实心、无图例，悬浮查看明细），同一标的跨图同色，便于对照股息效率
- 历年分红展开表、按字段排序

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
├── market.py           # ★ 代码规则唯一来源（市场前缀 / 是否 ETF）
├── routers/
│   ├── portfolios.py   # 组合 CRUD + summary
│   ├── assets.py       # 资产 CRUD + 刷新价格 + TTM 计算
│   └── stocks.py       # 搜索 / 价格 / 分红（个股 + ETF 分派）
├── tests/
│   ├── conftest.py
│   ├── test_api.py     # 通用 API 测试
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
    │   ├── PortfolioCharts.vue   # 两张分布饼图（容器）
    │   ├── ChartPie.vue          # 可复用饼图组件（ECharts 封装）
    │   ├── AssetList.vue
    │   ├── AssetItem.vue
    │   ├── AssetForm.vue
    │   ├── Toast.vue
    │   └── InfoTooltip.vue
    ├── utils/
    │   ├── chartData.js          # 饼图数据整理（纯函数 + 单测）
    │   ├── chartData.test.mjs
    │   └── chartColors.js        # 跨图共享配色
    ├── composables/
    │   └── useToast.js
    ├── api/
    │   ├── portfolio.js
    │   └── stock.js
    └── styles/main.css
```

## 测试

```bash
# 后端：88 个用例（API / ETF / TTM 口径 / 刷新缓存时效）
cd portfolio_api && uv run pytest -q

# 前端：26 个用例（饼图数据整理、格式化、悬浮提示）
cd portfolio_web && npm test

# 浏览器端验证饼图悬浮提示（需本地安装 Chrome，可用 CHROME_PATH 覆盖路径）
cd portfolio_web && npm run verify:chart
```

## 待办

- 数据库迁移机制（Alembic）
- 测试覆盖（前端 vitest）
- 用户认证（多用户之前必须加）
- ETF 分红面板的「年均每股分红」对月度分红标的语义失真