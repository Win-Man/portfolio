# 数据处理与计算公式

本文档梳理系统中**每一个展示出来的数字**是怎么来的——是后端数据库存的、外部接口拉的、还是前端/后端算出来的。便于维护、审计、二次开发。

> **TL;DR**：DB 只存**原子字段**（数量、价格、每股分红、TTM 收益率）。所有"汇总值"都是**后端实时算**或**前端实时算**的，从来不存。

> **支持的标的类型**：个股（0/3/6/9 开头）与 **ETF / LOF / 封闭式基金**（5/1 开头）。
> 两类标的的**分红数据模型完全不同**，见 § 3.3 与 § 11。

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
│     - akshare.stock_info_a_code_name     → 全 A 股代码表        │
│     - akshare.fund_etf_category_sina     → ETF/LOF/封基清单     │
│     - akshare.stock_dividend_cninfo      → 个股历史分红 ★        │
│     - akshare.fund_etf_dividend_sina     → ETF 历史分红 ★        │
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

### 1.1 代码规则唯一来源：`portfolio_api/market.py`

所有"这个代码属于哪个市场 / 是不是 ETF"的判断都集中在这一个文件，**不要在别处再写一份**。

| 代码首位 | 市场 | 说明 |
|---|---|---|
| `5` | 沪市 | 上交所基金/ETF（510xxx、511xxx、512~517xxx、560~563xxx、588xxx） |
| `6` | 沪市 | 主板 + 科创板（688xxx） |
| `9` | 沪市 | B 股 |
| `0` | 深市 | 主板 |
| `1` | 深市 | 基金/ETF（15xxxx）、LOF（16xxxx）、封基（18xxxx）、债券（10~12xxxx） |
| `2` | 深市 | B 股 |
| `3` | 深市 | 创业板 |
| `4` `8` | 北交所 | 暂未支持 |

`is_etf(code)` 判定规则：`5` 开头 → ETF；深市 `15`/`16`/`18` 开头 → ETF；其余为个股。

> ⚠ **历史坑**：早期实现是"`6`/`9` → sh，其它 → sz"，会把 ETF `561580` 判成 `sz561580`，
> 腾讯行情返回 `pv_none_match`，导致价格永远拉不到（静默走 mock 价）。


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

1. 调 `fetch_ttm_dividend_per_share(code)` → 拉全部历史分红，按 **`_sum_trailing_12m()` 累加「已实施」且除权日落在 `[今天-365天, 今天]` 内的分红**。
   - 个股与 ETF **同一口径**，且与数据源返回顺序无关
   - ⚠ 曾用名 `fetch_latest_dividend_per_share`，对个股走的是"取列表第一条"（假设降序，实际 akshare 返回升序），会取到最老的一期分红 —— 详见 §12
2. 调 `fetch_real_time_price(code)` → 调腾讯 `qt.gtimg.cn`，解析 `v_sh600900="1~名称~代码~当前价~昨收~..."`，返回 `{price, yesterday_close, name, source: "real"}`
3. 失败时 `fetch_real_time_price` 返回**模拟价**（`source: "mock"`），公式：

   ```
   fluctuation = (random.random() - 0.5) × 0.06   # ±3%
   mock_price = current_price × (1 + fluctuation)
   mock_ttm = (dividend_per_share / mock_price × 100)
   ```

4. **写入策略**：
   - 若 `source == "real"`：覆盖 DB 的 `price` / `dividend_per_share` / `ttm_yield`（`ttm = div × 100 / price`），并写 `price_fetched_at = now`
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

## 8.1 饼图的数据来源

页面上的两张饼图**不调用任何额外接口**，纯前端对已有字段做聚合：

| 图 | 数据字段 | 说明 |
|---|---|---|
| 持仓市值分布 | `holding_value` | = `quantity × price`（§3.1） |
| 预计年收入分布 | `expected_annual_income` | = `quantity × dividend_per_share`（§3.1） |

处理流程（`src/utils/chartData.js`，纯函数、有单测）：

1. 同名标的先合并（防用户重复录入同一只）
2. 过滤掉 `value <= 0` 的项（饼图画不出 0 值扇区）
3. 按金额降序排序
4. **仅当扇区数 > 8 时**，把占比 < 2% 的合并为「其他 (N 项)」；
   若合并后「其他」只剩 1 项被吞并，则放弃合并
5. 计算每项 `percent`（合计恒为 100）

**配色规则**：两张图共用同一份「名称 → 颜色」映射，顺序以**持仓市值降序**为基准
（`src/utils/chartColors.js`）。因此同一标的在两图中颜色一致，
可直接对照「市值占比 vs 收入占比」判断股息效率 ——
例如某标的市值占比 9.9% 但收入占比 14.8%，说明其股息率高于组合平均。

**展示形态**：实心饼图，**不显示图例**。合计值放在卡片标题右侧，
扇区识别完全依赖**鼠标悬浮提示**（名称 + 精确金额 + 两位小数占比）。

**渲染**：ECharts 6 按需引入（仅 `echarts/core` + `PieChart` + `Tooltip`，
不含 `Legend`），通过 `defineAsyncComponent` 异步加载 →
首屏主包保持约 103 KB，图表 chunk（约 431 KB）按需拉取。

**验证**：`npm run verify:chart` 用 Chrome DevTools Protocol 派发真实鼠标事件，
断言提示 DOM 确实生成且内容完整（图例移除后这是唯一识别扇区的途径，
不能只靠"看起来渲染了"来验收）。



---

## 9. 已知偏差 / 限制

| 现象 | 原因 | 影响 |
|---|---|---|
| TTM 收益率是后端刷新时算的、写进 DB 的 | 不是每次显示都重算 | 如果股价大涨大跌，DB 里的 TTM 数字会"过时"——必须点"刷新价格"才会同步 |
| "未实施"分红会被算进 `averageDividend` 的分母 | `AssetItem.vue:159` 取的是 `dividendHistory.length` | 平均值偏低。已在代码中过滤 status，但前端没生效（待修） |
| `static_yield` 字段在 DB 里有，但前端**从未展示** | 历史遗留 | 用户填了也看不到 |
| 模拟价（mock）会被写入 `price_source='mock'`，但 `price` 字段保留旧值 | B1 任务的设计 | "假数据不污染真价"是 feature，但用户可能疑惑"为什么点了刷新价格没变" |
| 腾讯行情源**没有官方文档**，解析格式靠经验 | `v_sh600900="1~名称~代码~当前价~..."` | 腾讯改格式后可能解析失败，已 fallback 到 mock |
| akshare 字段名硬编码中文（"派息比例"、"除权日"） | `stocks.py` | akshare 升级可能 break，搜索结果空 |
| `averageDividend` 对**月度分红 ETF** 语义失真 | 前端按"期"平均，非按"年" | 561580 会显示约 0.009/期，看起来像"年均分红很低" |
| ETF 分红面板仍展示"每股红股/每股转增"两列 | ETF 恒为 0 | 无害但冗余 |

---

## 10. 一句话回答"数字哪来的"

> **DB 存原子值（quantity / price / dividend_per_share / ttm_yield），后端在响应序列化时算汇总（holding_value / summary / y2024_y2025），前端在展示时做单位换算（万 / 百分号 / 千分位）。没有任何"计算结果"被持久化**。

---

## 11. ETF / 基金支持（与个股的关键差异）

系统同时支持**个股**与 **ETF / LOF / 封闭式基金**。两者在「怎么查代码、怎么拿分红、怎么算股息率」三个环节都不同。

### 11.1 对标差异总表

| 环节 | 个股（股票） | ETF / 基金 |
|---|---|---|
| 代码特征 | `0`/`3`/`6`/`9` 开头 | `5` 开头（沪）、`15`/`16`/`18` 开头（深） |
| 搜索清单来源 | `ak.stock_info_a_code_name()`（约 5 573 只） | `ak.fund_etf_category_sina()` ETF/LOF/封基（约 2 144 只） |
| **分红接口** | `ak.stock_dividend_cninfo(symbol='600900')` | `ak.fund_etf_dividend_sina(symbol='sh561580')` ⚠ 需带市场前缀 |
| 分红返回结构 | 每期一条，字段 `派息比例`（每 10 股） | **累计分红**（`日期`, `累计分红`）→ 需差分 |
| 分红频率 | 1–2 次/年 | 红利 ETF 多为**月度**（561580 一年 12 期） |
| 送股/转增 | 可能有 | 恒为 0 |
| 分红状态 | `已实施` / `待实施` | 一律 `已实施`（sina 只记录已发生的） |
| **TTM 股息率口径** | **近 12 个月合计** ÷ 现价 | **近 12 个月合计** ÷ 现价（已统一） |
| 行情前缀 | `sh` / `sz` | `sh` / `sz`（同一套规则） |

### 11.2 ETF 分红「累计 → 每期」转换

`fund_etf_dividend_sina` 只给**累计分红**，必须做**相邻差分**才能得到每期金额：

```
日期          累计分红     差分后（每期）
2025-05-19    0.005    →   0.005        （首期 = 累计值本身）
2025-06-16    0.010    →   0.005
2025-07-14    0.015    →   0.005
2025-08-18    0.035    →   0.020
```

实现见 `routers/stocks.py` 的 `_fetch_etf_dividend_blocking()`；转换后**统一成与个股相同的字段结构**，因此下游（`y2024`/`y2025` 汇总、`dividend-history`、`refresh-price`）无需感知类型差异。

### 11.3 TTM 必须用滚动 12 个月（个股与 ETF 统一）

`_sum_trailing_12m()`（`routers/assets.py`）是**唯一**的 TTM 口径实现，个股与 ETF 共用，**按日期过滤、与数据源返回顺序无关**。

两类标的都会因"取单期"而失真，只是原因不同：

**ETF —— 因为按月分红：**

| 口径 | 每股分红 | 按 1.286 元计 | 偏差 |
|---|---|---|---|
| 最近一期（错误） | 0.005 | **0.39 %** | 低估约 19 倍 |
| 近 12 个月合计（正确） | 0.095 | **7.39 %** | — |

**个股 —— 因为 akshare 返回顺序是升序（最老在前），而代码曾假设降序：**

| 口径 | 长江电力 600900 每股分红 | 按 29.38 元计 | 偏差 |
|---|---|---|---|
| 列表第一条（错误，实为 2004 年） | 0.0900 | **0.31 %** | 低估 11 倍 |
| 近 12 个月合计（正确） | 1.0000 | **3.40 %** | — |

> 明细见 §12。


### 11.4 `y2024` / `y2025` 对 ETF 的语义

仍然按**除权除息日所属自然年**累加每期金额。以 561580（数据截至 2026-10）为例：

- `y2025_dividend` = 0.095（2025 年 8 期）
- `y2026` 合计 = 0.055（当前前端只展示 2025/2024 两年，2026 年数据不展示）

### 11.5 分红缓存的失败语义（重要）

> **只缓存成功结果。拉取失败绝不写缓存。**

线上实录：第一次刷新 561580 时 sina 抛 `SSLError(SSL: UNEXPECTED_EOF_WHILE_READING)`，
若把这次的空结果当成"该 ETF 无分红"缓存 6 小时，会导致股息率**长时间显示 0**。

现在的行为：

| 情况 | 是否缓存 | 返回 |
|---|---|---|
| 拉取成功且有数据 | ✅ 缓存 | 数据 |
| 拉取成功但确实无分红记录 | ✅ 缓存（合法空结果） | `[]` |
| **拉取失败**（网络/SSL/解析异常） | ❌ **不缓存** | 有旧缓存 → 返回旧数据（stale-while-error）；无 → `[]` |

因此瞬时故障后，下一次请求即可恢复，无需等 TTL 过期。

### 11.6 资产类型如何确定

优先级：**用户显式指定 > 搜索结果携带的类型 > 按代码推断**。

- 前端搜索下拉会显示 `ETF` / `股票` 徽标，选中后写入表单
- 表单 `watch(form.code)` 会在手动输入代码时自动校正类型（输入 `561580` 即变 `ETF`）
- 后端 `create_asset` 仅在 `asset_type` **未显式传入**时才按 `guess_asset_type(code)` 推断
- 代码入库前统一 `normalize_code()`，`sh561580` → `561580`

### 11.7 快速验收命令

```bash
# 1. 刷新标的清单（首次需要，会拉 4 个数据源，约 10s）
curl -X POST http://localhost:8000/api/stocks/search/refresh

# 2. 搜 ETF
curl "http://localhost:8000/api/stocks/search?keyword=561580"
# → [{"code":"561580","name":"央企红利ETF华泰柏瑞","market":"沪市","type":"ETF"}]

# 3. 看 ETF 分红历史（17 期月度分红）
curl http://localhost:8000/api/stocks/561580/dividend-history

# 4. 建资产并刷新价格（TTM 应约 7.39%，而非 0.39%）
curl -X POST http://localhost:8000/api/portfolios/1/assets \
  -H 'Content-Type: application/json' \
  -d '{"name":"央企红利ETF华泰柏瑞","code":"561580","quantity":10000,"price":1.286}'
curl -X POST http://localhost:8000/api/assets/<id>/refresh-price
```

---

## 12. 事故记录：个股 TTM 收益率被低估 5~50 倍

**性质**：数据正确性 bug（已修）。影响所有**点过「刷新价格」的个股**。

### 现象

长江电力 600900 的 `dividend_per_share` 显示 **0.09**，而其 2026-07-17 实际分红为 **0.79**。
`0.09` 恰好是 **2004-06-01** 那一期。

### 根因

`fetch_latest_dividend_per_share()` 的实现与注释不符：

```python
# 注释声称：按 EX_DIVIDEND_DATE 降序，第一条即最新
for item in dividend_data:
    if item.get("IMPLEMENT_STATUS") not in ("已实施", "实施"):
        continue
    ...
    return float(dividend)      # ← 取列表第一条
```

但实测 `akshare.stock_dividend_cninfo` 返回的是**升序**（最老在前）：

```
2004-06-01  0.0900   ← 代码以为是"最新"
2005-05-24  0.2100
...
2026-07-17  0.7900   ← 真正的最近一期
```

### 影响面（修复前的实测值）

| 代码 | 名称 | 修复前 | 修复后 | 低估倍数 |
|---|---|---|---|---|
| 000538 | 云南白药 | 0.10 % | 5.03 % | 50× |
| 600036 | 招商银行 | 0.28 % | 4.71 % | 17× |
| 600900 | 长江电力 | 0.31 % | 3.40 % | 11× |
| 002714 | 牧原股份 | 0.56 % | 3.24 % | 6× |

ETF 未受影响 —— 其 TTM 走的是按日期过滤的 `_sum_trailing_12m()`。

### 修复

1. `fetch_latest_dividend_per_share` → 重命名为 `fetch_ttm_dividend_per_share`，
   个股与 ETF **统一**走 `_sum_trailing_12m()`（按日期过滤，与返回顺序无关）
2. 新增 `tests/test_ttm.py`（21 个用例），用**真实的 600900 26 期数据**做夹具，
   锁死"必须取到 2026 年而不是 2004 年"，并覆盖升降序/乱序一致性、
   12 个月边界、待实施、未来日期、无日期、空历史
3. 修复后需**重新刷新一遍资产**，DB 里的旧值才会更新

### 顺带修掉的测试脆弱性

`conftest.py` 的 `mock_dividend` 原先用写死的 `date(2025,6,15)` / `date(2024,6,15)`，
一旦系统日期推进到 2026-10 之后，两期都掉出 12 个月窗口，TTM 会算成 0 —— 夹具本身
有时间脆弱性。现改为"相对今天"构造数据。

### 教训

> **不要假设第三方接口的返回顺序。** 任何"取最新 / 取第一条"的写法都必须显式排序，
> 或者像本修复一样改成**按业务条件（日期区间）过滤**，让逻辑与顺序解耦。

