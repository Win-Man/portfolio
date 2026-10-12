# Portfolio · 投资组合管理

一个轻量的 A 股投资组合与分红透视工具。后端用 FastAPI + SQLite，前端是 Vue 3 + Vite。

```
portfolio/
├── portfolio_api/         # 后端 FastAPI
└── portfolio_web/         # 前端 Vue 3
```

## 功能

- 多个「投资组合」管理（增删改）
- 每个组合下挂若干资产（股票）：持仓数量、当前价、每股分红、TTM 收益率等
- 一键拉取实时行情（腾讯）+ 历史分红（东方财富 via akshare）
- 加权 TTM 收益率、预计年收入、资产明细展示
- 历年分红展开表、按字段排序

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
| 股票代码表（搜索用） | akshare | 全量缓存到 `stock_cache.json` |
| 实时价格 | 腾讯行情 `qt.gtimg.cn` | 失败时返回模拟价格并标记 `source=mock` |
| 历史分红 | akshare `stock_dividend_cninfo` | 字段名按 akshare 当前版本硬编码 |

`source` 字段约定（参见 `schemas.AssetResponse`）：

- `real`：腾讯行情返回的真实价
- `mock`：网络失败时的兜底价（前端应高亮提示）
- `stale`：> 30 分钟未刷新（计划中）

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
├── main.py             # FastAPI 入口 + 种子数据
├── database.py         # SQLAlchemy
├── models.py           # ORM
├── schemas.py          # Pydantic
├── routers/
│   ├── portfolios.py   # 组合 CRUD + summary
│   ├── assets.py       # 资产 CRUD + 刷新价格
│   └── stocks.py       # 搜索 / 价格 / 分红
└── pyproject.toml      # uv 依赖

portfolio_web/
├── vite.config.js      # 含 /api proxy
└── src/
    ├── App.vue
    ├── views/Portfolio.vue
    ├── components/
    │   ├── PortfolioOverview.vue
    │   ├── AssetList.vue
    │   ├── AssetItem.vue
    │   ├── AssetForm.vue
    │   └── InfoTooltip.vue
    ├── api/
    │   ├── portfolio.js
    │   └── stock.js
    └── styles/main.css
```

## 待办

详见每次代码评审记录。优先项：

- 数据库迁移机制（Alembic）
- 真假价格视觉区分（已加 `source` 字段，待前端完善）
- 测试覆盖（pytest + vitest）
- 用户认证（多用户之前必须加）