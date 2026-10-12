# 数据处理与计算公式

本文档梳理系统中**每一个展示出来的数字**是怎么来的——是后端数据库存的、外部接口拉的、还是前端/后端算出来的。便于维护、审计、二次开发。

> **TL;DR**：DB 只存**原子字段**（数量、价格、每股分红、TTM 收益率）。所有"汇总值"都是**后端实时算**或**前端实时算**的，从来不存。

---

## 1. 数据来源总览

```
┌──────────────────────────────────────────────────────────────┐
│                       数据源 4 类                              │
├──────────────────────────────────────────────────────────────┤
│  ① DB 存的（用户填的 / 历史抓的）                              │
│     - 组合：name, created_at, updated_at                       │
│     - 资产：name, code, asset_type, quantity, price,           │
│              dividend_per_share, ttm_yield, static_yield,      │
│              note, price_source, price_fetched_at, created_at │
│                                                                │
│  ② 外部 API 拉取的                                              │
│     - 腾讯行情 qt.gtimg.cn        → 实时价、名称、昨收          │
│     - 东方财富（akshare.stock_dividend_cninfo）→ 历史分红      │
│     - akshare.stock_info_a_code_name → 全 A 股代码表           │
│                                                                │
│  ③ 后端实时算的（每次请求都重算，不存）                          │
│     - holding_value, expected_annual_income,                   │
│       weighted_ttm_yield, total_market_value,                  │
│       y2025_dividend, y2024_dividend,                          │
│       price_source_label, price_source_hint                    │
│                                                                │
│  ④ 前端实时算的（仅做展示格式化）                                │
│     - 万元单位格式化、年均每股分红、累计分红、                    │
│       平均 TTM 收益率展示、badge 文字                           │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Asset 表字段一览

| 字段 | 类型 | 来源 | 含义 |
|---|---|---|---|
| `id` | int | DB 自增 | 主键 |
| `portfolio_id` | int | DB 外键 | 所属组合 |
| `name` | str | 用户填 | 股票名称（"长江电力"） |
| `code` | str | 用户填或搜出来的 | 股票代码（"600900"） |
| `asset_type` | str | 用户填，默认"股票" | 资产类型（目前只有股票，预留扩展） |
| `quantity` | float | 用户填 | 持股数量 |
| `price` | float | 用户填 / 刷新时覆盖 | **最新价**（元/股） |
| `dividend_per_share` | float | 用户填 / 刷新时覆盖 | 每股最近一次分红（**元/股**） |
| `ttm_yield` | float | 用户填 / 刷新时覆盖 | TTM 股息率（**百分比数值**，如 3.38 表示 3.38%） |
| `static_yield` | float | 用户填 | 静态股息率（同上） |
| `note` | str | 用户填，默认 "" | 备注 |
| `price_source` | str | 刷新时设 | `real` / `mock` / `stale` |
| `price_fetched_at` | datetime | 刷新时设 | 价格拉取时间（real 才有） |
| `created_at` | datetime | DB 自动 | 创建时间 |

> **关键约定**：
> - `price` / `dividend_per_share` / `ttm_yield` 三个字段是**用户可能手动改的"权威值"**——也是**刷新接口会覆盖**的字段
> - 价格刷新时，**只有 `source="real"` 才会覆盖 DB 里的 `price`**；`source="mock"` 只更新标记，不污染数据

---

## 3. 单只资产展示的数字（资产列表行 + 行展开）

### 3.1 资产列表行（每行一个资产）

| 列 | 来源 | 公式 / 处理 |
|---|---|---|
| 名称 | DB | 直接显示 `asset.name` |
| 代码 | DB | 直接显示 `asset.code` |
| 持股数量 | DB | 直接显示 `asset.quantity`（前端 `formatNumber` 加千分位） |
| **最新价** + 价格 badge | DB | 直接显示 `asset.price`，后端给 `price_source_label` 渲染成 badge：实时/模拟/未刷新 |
| **持仓市值** | **后端算** | `holding_value = quantity × price`（见 `schemas.py:79`） |
| 25年每股分红 | **后端算** | 见 § 3.3 |
| 24年每股分红 | **后端算** | 见 § 3.3 |
| **TTM 收益率** | DB | 直接显示 `asset.ttm_yield`（已经是百分比数值） |
| 操作按钮 | — | UI 元素 |

#### 价格 badge 的生成（`_source_to_label_hint`，`schemas.py:106`）

| `price_source` 字段值 | badge 文字 | badge 颜色（前端） | tooltip |
|---|---|---|---|
| `real` + 有 `price_fetched_at` | 实时 | 绿色 | "腾讯行情接口返回的真实价（YYYY-MM-DD HH:MM）" |
| `real` 但无 fetched_at | 实时 | 绿色 | "腾讯行情接口返回的真实价（时间未知）" |
| `mock` | 模拟 | 橙色 | "网络拉取失败，使用 ±3% 随机波动价，不可用于决策" |
| `stale`（默认） | 未刷新 | 灰色 | "该价格尚未从行情接口刷新过" |

### 3.2 行展开（分红明细 + 累计）

点击行触发 `getDividendHistory(code)` → `GET /api/stocks/{code}/dividend-history`，后端调 akshare 拉**完整历史分红**（多条记录）。

#### 后端返回的字段（`/api/stocks/{code}/dividend-history`，见 `stocks.py:178-216`）

| 字段 | 来源 | 处理 |
|---|---|---|
| `year` | 算的 | 取 `ex_dividend_date[:4]` |
| `ex_dividend_date` | akshare 原始 | date 对象 → `YYYY-MM-DD` 字符串 |
| `dividend_per_share` | akshare 原始 | **已是每股**（akshare 返回的是"每 10 股"，后端 ÷10 处理，见 `stocks.py:90`） |
| `bonus_shares` | akshare 原始 | 每股红股数 |
| `transfer_shares` | akshare 原始 | 每股转增数 |
| `status` | akshare 原始 | "已实施" / "待实施" |

#### 前端算的（`AssetItem.vue`）

| 展示 | 公式 |
|---|---|
| 每行"持股合计分红" | `asset.quantity × item.dividend_per_share` |
| **累计分红合计**（表脚） | `Σ (item.dividend_per_share × asset.quantity)` |
| **年均每股分红** | `(Σ item.dividend_per_share) / dividendHistory.length` |
| **按持股计算累计** | `asset.quantity × averageDividend` |

> ⚠ **当前实现的局限**：`averageDividend` 把所有历史记录（包括 "待实施" 的）都算进分母，意味着如果一只股票没分过红但有 1 条"待实施"记录，年均会被稀释。**理论上应该只算已实施 + 同年合并。** 这是一个已知 bug 计划修。

### 3.3 列表行的 y2024 / y2025 字段

`/api/portfolios/{id}/assets` 端点（`assets.py:107-139`）：

- 调 akshare 拉**完整历史分红**
- **按除权除息日的年份**过滤：
  - `year == "2025"` → 累加进 `y2025_dividend`
  - `year == "2024"` → 累加进 `y2024_dividend`
- 累加 `dividend_per_share`（同一年可能多次分红，例如中期+末期）

**举例**：假设长江电力 2024 年有 0.5（中期）+ 0.4（末期）= 0.9，那 24年每股分红 = 0.9

---

## 4. 组合概览（顶部 4 卡片：`PortfolioOverview.vue`）

来自 `GET /api/portfolios/{id}/summary`（`portfolios.py:63-86`）：

| 卡片 | 公式 | 备注 |
|---|---|---|
| **总市值** | `Σ (a.quantity × a.price)` | 完全 DB 字段，无外部依赖 |
| **资产数量** | `len(assets)` | — |
| **加权平均 TTM 收益率** | `Σ (a.quantity × a.price × a.ttm_yield) / Σ (a.quantity × a.price)` | 分子：每个资产的"市值×TTM"，分母：总市值。本质是按市值加权。**注意：DB 里的 ttm_yield 已经是百分比数值（3.38 = 3.38%），所以结果也是百分比数值**，前端再 `.toFixed(2) + '%'`。 |
| **预计年收入** | `Σ (a.quantity × a.dividend_per_share)` | — |

> **重要边界**：如果组合内没有任何资产，`weighted_ttm_yield = 0.0`（避免除零）。

---

## 5. 刷新价格时发生的计算

### 5.1 `POST /api/assets/{id}/refresh-price`（单只）

`assets.py:189-219`：

1. 调 `fetch_latest_dividend_per_share(code)` → **后端函数**从 akshare 拉所有分红记录，**取最近一次"已实施"**的 `dividend_per_share`（见 `assets.py:38-45`）；若没有"已实施"则取第一条
2. 调 `fetch_real_time_price(code)` → 调腾讯 `qt.gtimg.cn`，解析 `v_sh600900="1~名称~代码~当前价~昨收~..."`，返回 `{price, yesterday_close, name, source: "real"}`
3. 失败时 `fetch_real_time_price` 返回**模拟价**（`source: "mock"`），公式：

   ```
   fluctuation = (random.random() - 0.5) × 0.06   # ±3%
   mock_price = current_price × (1 + fluctuation)
   mock_ttm = (dividend_per_share / mock_price × 100)
   ```

4. **写入策略**（`assets.py:209-216`）：
   - 若 `source == "real"`：覆盖 DB 的 `price` / `dividend_per_share` / `ttm_yield`（ttm = div × 100 / price），并写 `price_fetched_at = now`
   - 若 `source == "mock"`：**只更新 `price_source` 标记，DB 里的价格不变**（避免假数据污染）
5. 任何情况下都 `invalidate_dividend_cache(code)` 让下次列表请求重新算 y2024/y2025

### 5.2 `POST /api/portfolios/{id}/assets/refresh-all-prices`（批量）

`assets.py:223-271`：

- **5 路并发**（`asyncio.Semaphore(5)`），100 只股票不会串行 100 次网络
- 每只的内部流程与 5.1 一致
- 失败的资产不抛错，结果里 `success: false` + `error` 字段
- 任何一只 `real` 价更新后都让其对应股票的 dividend cache 失效

---

## 6. 表单里"自动算"逻辑（`AssetForm.vue`）

新增/编辑资产时，前端做了两个**实时联动**（不调后端）：

| 用户改 | 另一个字段自动算 | 公式 |
|---|---|---|
| `dividend_per_share`（每股分红） | `ttm_yield` | `ttm_yield = dividend_per_share / price × 100`（见 `AssetForm.vue:215-220`） |
| `ttm_yield` | `dividend_per_share` | `dividend_per_share = ttm_yield × price / 100`（见 `AssetForm.vue:223-228`） |

> 公式来源：TTM 股息率标准定义 = "过去 12 个月每股分红 / 当前股价"

---

## 7. 数字精度与格式化

| 显示位置 | 函数 | 规则 |
|---|---|---|
| 资产列表价格 | `formatPrice()` | `null` 显示 `—`；否则 `toFixed(2)` |
| 资产列表数量/市值/分红 | `formatNumber()` | `>= 10000` 时 `(n/10000).toFixed(2) + "万"`，否则 `toLocaleString()`（加千分位） |
| 组合概览数字 | `formatNumber()` | `>= 10000` 显示"万"单位，否则 2 位小数 |
| 收益率 | `.toFixed(2) + '%'` | 固定 2 位小数 + % |
| 持仓市值（API 返回） | 原始 float | 不在 API 层 round，前端控制 |

---

## 8. 端点速查：哪个端点产生哪些"算出来的"字段

| 端点 | 算的字段 |
|---|---|
| `GET /api/portfolios` | —（只返回 DB 原值） |
| `GET /api/portfolios/{id}/summary` | `total_market_value` / `asset_count` / `weighted_ttm_yield` / `expected_annual_income` |
| `GET /api/portfolios/{id}/assets` | `holding_value` / `expected_annual_income` / `y2025_dividend` / `y2024_dividend` / `price_source_label` / `price_source_hint` |
| `GET /api/assets/{id}` | 同上 |
| `POST /api/portfolios/{id}/assets` | 同上 |
| `PUT /api/assets/{id}` | 同上 |
| `POST /api/assets/{id}/refresh-price` | 同上 + 实际改了 DB 的 `price` / `dividend_per_share` / `ttm_yield` / `price_fetched_at` |
| `POST /api/portfolios/{id}/assets/refresh-all-prices` | 同上，逐只处理 |
| `GET /api/stocks/{code}/price` | `change` / `change_percent`（算的） |
| `GET /api/stocks/{code}/dividend-history` | `year`（算的） |

---

## 9. 已知偏差 / 限制

| 现象 | 原因 | 影响 |
|---|---|---|
| TTM 收益率是后端刷新时算的、写进 DB 的 | 不是每次显示都重算 | 如果股价大涨大跌，DB 里的 TTM 数字会"过时"——必须点"刷新价格"才会同步 |
| "未实施"分红会被算进 `averageDividend` 的分母 | `AssetItem.vue:159` 取的是 `dividendHistory.length` | 平均值偏低。已在代码中过滤 status，但前端没生效（待修） |
| `static_yield` 字段在 DB 里有，但前端**从未展示** | 历史遗留 | 用户填了也看不到 |
| 模拟价（mock）会被写入 `price_source='mock'`，但 `price` 字段保留旧值 | B1 任务的设计 | "假数据不污染真价"是 feature，但用户可能疑惑"为什么点了刷新价格没变" |
| 腾讯行情源**没有官方文档**，解析格式靠经验 | `v_sh600900="1~名称~代码~当前价~..."` | 腾讯改格式后可能解析失败，已 fallback 到 mock |
| akshare 字段名硬编码中文（"派息比例"、"除权日"） | `stocks.py:79-98` | akshare 升级可能 break，搜索结果空 |

---

## 10. 一句话回答"数字哪来的"

> **DB 存原子值（quantity / price / dividend_per_share / ttm_yield），后端在响应序列化时算汇总（holding_value / summary / y2024_y2025），前端在展示时做单位换算（万 / 百分号 / 千分位）。没有任何"计算结果"被持久化**。
