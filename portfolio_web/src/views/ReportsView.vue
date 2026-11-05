<template>
  <main class="container">
    <!-- 顶部：类型切换 + 立即生成 -->
    <div class="report-toolbar">
      <div class="type-switch" role="tablist">
        <button
          v-for="opt in TYPE_OPTIONS"
          :key="opt.key"
          type="button"
          class="type-btn"
          :class="{ active: reportType === opt.key }"
          @click="switchType(opt.key)"
        >{{ opt.icon }} {{ opt.label }}</button>
      </div>

      <div class="toolbar-right">
        <span v-if="generating" class="toolbar-hint">正在生成…</span>
        <button
          type="button"
          class="btn btn-primary btn-sm"
          :disabled="generating || !canGenerate"
          :title="canGenerate ? '按当前行情生成今天的日报（会覆盖今天那一份）' : '先选择一个组合'"
          @click="handleGenerate"
        >立即生成</button>
      </div>
    </div>

    <div v-if="loadingList" class="loading">加载日报...</div>

    <div v-else-if="listError" class="empty-state">
      <p>{{ listError }}</p>
      <button type="button" class="btn btn-secondary btn-sm" @click="loadList()">重试</button>
    </div>

    <div v-else-if="reports.length === 0" class="empty-state">
      <p>{{ typeMeta.emptyHint }}</p>
      <p class="empty-hint">每天 15:40（组合）/ 15:45（观察池）自动生成；也可以现在点「立即生成」</p>
    </div>

    <div v-else class="report-layout">
      <!-- 左：历史列表 -->
      <aside class="report-list">
        <div class="list-title">历史日报（{{ total }}）</div>
        <button
          v-for="row in reports"
          :key="row.id"
          type="button"
          class="list-item"
          :class="{ active: row.id === selectedId }"
          @click="selectReport(row)"
        >
          <span class="list-date">{{ row.report_date }}</span>
          <span class="list-pnl" :class="pnlTone(row.total_pnl)">{{ signedAmount(row.total_pnl) }}</span>
          <span class="list-pct" :class="pnlTone(row.total_pnl_percent)">
            {{ signedPercent(row.total_pnl_percent) }}
          </span>
          <span class="list-meta">
            {{ row.item_count }} 只
            <template v-if="reportType === 'watchlist' && row.signal_count > 0">
              · <b class="signal-dot">信号 {{ row.signal_count }}</b>
            </template>
          </span>
        </button>
      </aside>

      <!-- 右：详情 -->
      <section class="report-detail">
        <div v-if="loadingDetail" class="loading">加载详情...</div>
        <div v-else-if="!detail || !payload" class="empty-state">
          <p>这份日报没有内容（可能已被删除），换一份看看</p>
        </div>

        <template v-else>
          <!-- 标题 -->
          <header class="detail-head">
            <div>
              <h2 class="detail-title">{{ detail.title || typeMeta.label }}</h2>
              <div class="detail-sub">
                生成于 {{ generatedAt }}
                <span v-if="payload.notes && payload.notes.length" class="detail-flag">· 有 {{ payload.notes.length }} 条提示</span>
              </div>
            </div>
            <button type="button" class="btn btn-ghost btn-sm" @click="handleDelete">删除</button>
          </header>

          <!-- KPI -->
          <div class="overview-grid">
            <div v-for="card in kpiCards" :key="card.label" class="overview-item" :class="card.tone">
              <div class="label">{{ card.label }}</div>
              <div class="value">{{ card.value }}</div>
              <div class="sub">{{ card.sub }}</div>
            </div>
          </div>

          <!-- 明细表 -->
          <div class="panel">
            <div class="panel-head">
              <span class="panel-title">{{ reportType === 'watchlist' ? '观察池明细' : '盈亏明细' }}</span>
              <span class="panel-note">{{ tableNote }}</span>
            </div>

            <div class="table-container">
              <!-- 组合明细 -->
              <table v-if="reportType === 'portfolio'" class="data-table">
                <thead>
                  <tr>
                    <th>标的</th>
                    <th class="text-right">现价</th>
                    <th class="text-right">昨收</th>
                    <th class="text-right">涨跌</th>
                    <th class="text-right">涨跌幅</th>
                    <th class="text-right">今日盈亏</th>
                    <th class="text-right">市值</th>
                    <th class="text-right">持仓盈亏</th>
                    <th class="text-right">股息率</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in portfolioItems" :key="item.id">
                    <td>
                      <span class="asset-name">{{ item.name }}</span>
                      <span class="asset-code">{{ item.code }}</span>
                      <span
                        v-if="item.price_source !== 'real'"
                        class="tag tag-warn"
                        :title="priceSourceMeta(item.price_source).label"
                      >{{ priceSourceMeta(item.price_source).label }}</span>
                    </td>
                    <td class="text-right num">{{ formatPrice(item.price, item.asset_type) }}</td>
                    <td class="text-right num muted">{{ formatPrice(item.prev_close, item.asset_type) }}</td>
                    <td class="text-right num" :class="pnlTone(item.change)">{{ signedPrice(item.change, item.asset_type) }}</td>
                    <td class="text-right num" :class="pnlTone(item.change_percent)">{{ signedPercent(item.change_percent) }}</td>
                    <td class="text-right num strong" :class="pnlTone(item.day_pnl)">{{ signedAmount(item.day_pnl) }}</td>
                    <td class="text-right num">{{ plainAmount(item.market_value) }}</td>
                    <td class="text-right num" :class="pnlTone(item.holding_pnl)">
                      {{ signedAmount(item.holding_pnl) }}
                      <span v-if="item.holding_pnl_percent !== null" class="muted small">
                        ({{ signedPercent(item.holding_pnl_percent) }})
                      </span>
                    </td>
                    <td class="text-right num">{{ plainPercent(item.ttm_yield) }}</td>
                  </tr>
                </tbody>
                <tfoot>
                  <tr>
                    <td>合计</td>
                    <td colspan="4"></td>
                    <td class="text-right num strong" :class="pnlTone(summary.day_pnl)">{{ signedAmount(summary.day_pnl) }}</td>
                    <td class="text-right num">{{ plainAmount(summary.market_value) }}</td>
                    <td class="text-right num" :class="pnlTone(summary.holding_pnl)">{{ signedAmount(summary.holding_pnl) }}</td>
                    <td class="text-right num">{{ plainPercent(summary.weighted_ttm_yield) }}</td>
                  </tr>
                </tfoot>
              </table>

              <!-- 观察池明细 -->
              <table v-else class="data-table">
                <thead>
                  <tr>
                    <th>标的</th>
                    <th class="text-right">现价</th>
                    <th class="text-right">昨收</th>
                    <th class="text-right">涨跌幅</th>
                    <th class="text-right">等权盈亏</th>
                    <th class="text-right">当前 / 目标股息率</th>
                    <th>股息率</th>
                    <th class="text-right">目标价</th>
                    <th>价格</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in watchlistItems" :key="item.id">
                    <td>
                      <span class="asset-name">{{ item.name }}</span>
                      <span class="asset-code">{{ item.code }}</span>
                      <span v-if="!item.is_today" class="tag tag-warn" :title="`快照日期 ${item.snapshot_date || '无'}`">非今日</span>
                      <span v-else-if="item.is_stale" class="tag tag-warn">陈旧</span>
                    </td>
                    <td class="text-right num">{{ formatPrice(item.price, item.asset_type) }}</td>
                    <td class="text-right num muted">{{ formatPrice(item.prev_close, item.asset_type) }}</td>
                    <td class="text-right num" :class="pnlTone(item.change_percent)">{{ signedPercent(item.change_percent) }}</td>
                    <td class="text-right num strong" :class="pnlTone(item.notional_pnl)">{{ signedAmount(item.notional_pnl) }}</td>
                    <td class="text-right num">
                      {{ plainPercent(item.current_yield) }}
                      <span class="muted small">/ {{ plainPercent(item.target_yield) }}</span>
                    </td>
                    <td>
                      <div class="signal-cell">
                        <span class="tag" :class="`tag-${yieldSignalMeta(item.yield_signal).tone}`">
                          {{ yieldSignalMeta(item.yield_signal).label }}
                        </span>
                        <span v-if="item.yield_gap_points !== null && item.yield_signal !== 'hit'" class="muted small">
                          差 {{ plainPercent(Math.abs(item.yield_gap_points)) }}
                        </span>
                      </div>
                    </td>
                    <td class="text-right num">
                      <template v-if="item.target_price">{{ formatPrice(item.target_price, item.asset_type) }}</template>
                      <span v-else class="muted">—</span>
                    </td>
                    <td>
                      <div class="signal-cell">
                        <span class="tag" :class="`tag-${priceSignalMeta(item.price_signal).tone}`">
                          {{ priceSignalMeta(item.price_signal).label }}
                        </span>
                        <span v-if="item.price_gap_percent !== null" class="muted small">
                          距目标 {{ signedPercent(item.price_gap_percent) }}
                        </span>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- 组合专属：涨跌榜 / 收入结构 / 目标进度 -->
          <template v-if="reportType === 'portfolio'">
            <div class="panel-grid">
              <div class="panel">
                <div class="panel-title">今日涨跌榜</div>
                <div class="mover-block">
                  <div class="mover-title">领涨</div>
                  <div v-if="payload.top_gainers && payload.top_gainers.length === 0" class="muted small">今天没有上涨的标的</div>
                  <div v-for="row in payload.top_gainers || []" :key="`g-${row.code}`" class="mover-row">
                    <span class="mover-name" :title="row.name">{{ row.name }}</span>
                    <span class="num" :class="pnlTone(row.day_pnl)">{{ signedAmount(row.day_pnl) }}</span>
                    <span class="num muted small">{{ signedPercent(row.change_percent) }}</span>
                  </div>
                </div>
                <div class="mover-block">
                  <div class="mover-title">领跌</div>
                  <div v-if="payload.top_losers && payload.top_losers.length === 0" class="muted small">今天没有下跌的标的</div>
                  <div v-for="row in payload.top_losers || []" :key="`l-${row.code}`" class="mover-row">
                    <span class="mover-name" :title="row.name">{{ row.name }}</span>
                    <span class="num" :class="pnlTone(row.day_pnl)">{{ signedAmount(row.day_pnl) }}</span>
                    <span class="num muted small">{{ signedPercent(row.change_percent) }}</span>
                  </div>
                </div>
              </div>

              <div class="panel">
                <div class="panel-title">收入结构与集中度</div>
                <div v-for="row in payload.income_top || []" :key="`i-${row.code}`" class="bar-row">
                  <span class="bar-name" :title="row.name">{{ row.name }}</span>
                  <span class="bar-track"><span class="bar-fill" :style="{ width: ratioWidth(row.share_percent, incomeMaxShare) }"></span></span>
                  <span class="bar-value num">{{ plainPercent(row.share_percent, 1) }}</span>
                </div>
                <div v-if="!(payload.income_top || []).length" class="muted small">还没有分红数据</div>

                <div class="concentration">
                  <template v-if="payload.concentration && payload.concentration.top1_name">
                    最大持仓 <b>{{ payload.concentration.top1_name }}</b>
                    占 <b>{{ plainPercent(payload.concentration.top1_share_percent, 1) }}</b>；
                    前三大合计 <b>{{ plainPercent(payload.concentration.top3_share_percent, 1) }}</b>
                  </template>
                  <span v-else class="muted small">暂无可统计的持仓市值</span>
                </div>
              </div>
            </div>

            <div v-if="(payload.goals || []).length" class="panel">
              <div class="panel-title">年收入目标进度</div>
              <div v-for="goal in payload.goals" :key="goal.id" class="goal-row">
                <span class="goal-name">{{ goal.name }}</span>
                <span class="goal-track"><span class="goal-fill" :class="{ done: goal.is_achieved }" :style="{ width: barWidth(goal.progress_percent) }"></span></span>
                <span class="num goal-pct" :class="{ done: goal.is_achieved }">
                  {{ plainPercent(goal.progress_percent) }}
                </span>
                <span class="goal-gap muted small">
                  {{ goal.is_achieved ? '已达成' : `还差 ¥${plainAmount(goal.gap_annual)}/年` }}
                  · 目标 ¥{{ plainAmount(goal.target_annual_income) }}
                </span>
              </div>
            </div>
          </template>

          <!-- 观察池专属：信号清单 -->
          <template v-else>
            <div class="panel-grid">
              <div class="panel">
                <div class="panel-title">
                  股息率达标
                  <span class="panel-badge">{{ (payload.yield_hits || []).length }}</span>
                </div>
                <div v-if="!(payload.yield_hits || []).length" class="muted small">今天没有股息率达到目标的标的</div>
                <div v-for="row in payload.yield_hits || []" :key="`yh-${row.code}`" class="signal-row">
                  <span class="signal-name">{{ row.name }}<span class="asset-code">{{ row.code }}</span></span>
                  <span class="num">
                    {{ plainPercent(row.current_yield) }}
                    <span class="muted small">/ {{ plainPercent(row.target_yield) }}</span>
                  </span>
                </div>
                <div v-if="(payload.yield_near || []).length" class="near-block">
                  <span class="muted small">接近达标：</span>
                  <span v-for="row in payload.yield_near" :key="`yn-${row.code}`" class="near-chip">
                    {{ row.name }} {{ plainPercent(row.current_yield) }}
                  </span>
                </div>
              </div>

              <div class="panel">
                <div class="panel-title">
                  目标价达标
                  <span class="panel-badge">{{ (payload.price_hits || []).length }}</span>
                </div>
                <div v-if="!(payload.price_hits || []).length" class="muted small">今天没有跌到目标价的标的</div>
                <div v-for="row in payload.price_hits || []" :key="`ph-${row.code}`" class="signal-row">
                  <span class="signal-name">{{ row.name }}<span class="asset-code">{{ row.code }}</span></span>
                  <span class="num">
                    {{ formatPrice(row.price, row.asset_type) }}
                    <span class="muted small">≤ {{ formatPrice(row.target_price, row.asset_type) }}</span>
                  </span>
                </div>
                <div v-if="(payload.price_near || []).length" class="near-block">
                  <span class="muted small">接近达标：</span>
                  <span v-for="row in payload.price_near" :key="`pn-${row.code}`" class="near-chip">
                    {{ row.name }} {{ formatPrice(row.price, row.asset_type) }}
                  </span>
                </div>
              </div>
            </div>
          </template>

          <!-- 提示 -->
          <div v-if="(payload.notes || []).length" class="notes">
            <div v-for="(note, i) in payload.notes" :key="i" class="note-row">· {{ note }}</div>
          </div>
        </template>
      </section>
    </div>
  </main>
</template>

<script setup>
import { computed, ref, watch, onActivated } from 'vue'

import {
  deleteReport,
  generateReport,
  getReport,
  getReports,
} from '../api/reports.js'
import { usePortfolioContext } from '../composables/usePortfolioContext.js'
import { toast } from '../composables/useToast.js'
import { formatPrice } from '../utils/format.js'
import {
  plainAmount,
  plainPercent,
  pnlTone,
  priceSignalMeta,
  priceSourceMeta,
  reportTypeMeta,
  signedAmount,
  signedPercent,
  signedPrice,
  sortItemsByPnl,
  yieldSignalMeta,
} from '../utils/report.js'

const TYPE_OPTIONS = [
  { key: 'portfolio', label: '组合日报', icon: '📈' },
  { key: 'watchlist', label: '观察池日报', icon: '👀' },
]

const { selectedPortfolioId } = usePortfolioContext()

const reportType = ref('portfolio')
const reports = ref([])
const total = ref(0)
const selectedId = ref(null)
const detail = ref(null)

const loadingList = ref(false)
const loadingDetail = ref(false)
const listError = ref('')
const generating = ref(false)

const typeMeta = computed(() => reportTypeMeta(reportType.value))
const payload = computed(() => detail.value?.payload || null)
const summary = computed(() => payload.value?.summary || {})

const canGenerate = computed(
  () => reportType.value === 'watchlist' || !!selectedPortfolioId.value
)

/** 生成时间：payload 里存的是「YYYY-MM-DD HH:MM:SS」字符串，直接展示，不做时区换算 */
const generatedAt = computed(() => payload.value?.generated_at || '时间未知')

const portfolioItems = computed(() => sortItemsByPnl(payload.value?.items))
const watchlistItems = computed(() => sortItemsByPnl(payload.value?.items, 'notional_pnl'))

/** 收入结构里占比最大的那一项：条形按它做相对刻度，否则 21.5% 只能画出一小截 */
const incomeMaxShare = computed(() =>
  (payload.value?.income_top || []).reduce(
    (max, row) => Math.max(max, Number(row.share_percent) || 0),
    0
  )
)

const tableNote = computed(() => {
  if (reportType.value === 'watchlist') {
    const s = summary.value
    return s.notional_per_item
      ? `按每只 ¥${plainAmount(s.notional_per_item, 0)} 等权测算盈亏`
      : '按等权测算盈亏'
  }
  return '涨跌额 = 现价 − 昨收；今日盈亏 = 涨跌额 × 持股数'
})

/** KPI 卡片：两种日报各一套（组合看市值/成本，观察池看信号） */
const kpiCards = computed(() => {
  const s = summary.value
  if (reportType.value === 'watchlist') {
    return [
      {
        label: '等权今日盈亏',
        value: signedAmount(s.day_pnl),
        sub: `平均涨跌 ${signedPercent(s.avg_change_percent)}`,
        tone: toneClass(s.day_pnl),
      },
      {
        label: '涨跌分布',
        value: `${s.up_count || 0} / ${s.down_count || 0} / ${s.flat_count || 0}`,
        sub: `涨 / 跌 / 平（共 ${s.item_count || 0} 只）`,
        tone: '',
      },
      {
        label: '股息率达标',
        value: String(s.yield_hit_count || 0),
        sub: `接近 ${s.yield_near_count || 0} 只`,
        tone: '',
      },
      {
        label: '目标价达标',
        value: String(s.price_hit_count || 0),
        sub: `接近 ${s.price_near_count || 0} 只`,
        tone: '',
      },
      {
        label: '计入今日统计',
        value: `${s.today_count || 0} 只`,
        sub: s.today_count === s.item_count ? '全部有今日快照' : `${(s.item_count || 0) - (s.today_count || 0)} 只无今日快照`,
        tone: '',
      },
    ]
  }

  return [
    {
      label: '今日盈亏',
      value: signedAmount(s.day_pnl),
      sub: `涨跌幅 ${signedPercent(s.day_pnl_percent)}`,
      tone: toneClass(s.day_pnl),
    },
    {
      label: '持仓市值',
      value: `¥${plainAmount(s.market_value)}`,
      sub: `昨日 ¥${plainAmount(s.prev_market_value)}`,
      tone: '',
    },
    {
      label: '持仓盈亏',
      value: signedAmount(s.holding_pnl),
      sub: `成本 ¥${plainAmount(s.cost_amount)}`,
      tone: toneClass(s.holding_pnl),
    },
    {
      label: '预计年收入',
      value: `¥${plainAmount(s.annual_income)}`,
      sub: `月均 ¥${plainAmount((s.annual_income || 0) / 12)}`,
      tone: '',
    },
    {
      label: '加权 TTM 收益率',
      value: plainPercent(s.weighted_ttm_yield),
      sub: `按市值加权 · ${s.asset_count || 0} 只标的`,
      tone: '',
    },
  ]
})

function toneClass(value) {
  const tone = pnlTone(value)
  if (tone === 'is-up') return 'profit-up'
  if (tone === 'is-down') return 'profit-down'
  return ''
}

/** 进度条宽度：clamp 到 0~100，超额封顶（数字仍显示真实值） */
function barWidth(percent) {
  const n = Number(percent)
  if (!Number.isFinite(n) || n <= 0) return '0%'
  return `${Math.min(100, n)}%`
}

/** 相对刻度条：以列表内最大值为满格，最小留 3% 让「占比很小」也看得见 */
function ratioWidth(percent, maxPercent) {
  const n = Number(percent)
  const max = Number(maxPercent)
  if (!Number.isFinite(n) || !Number.isFinite(max) || max <= 0 || n <= 0) return '0%'
  return `${Math.max(3, Math.min(100, (n / max) * 100))}%`
}

// ---------- 数据加载 ----------

async function loadList({ keepSelection = false } = {}) {
  if (reportType.value === 'portfolio' && !selectedPortfolioId.value) {
    reports.value = []
    total.value = 0
    selectedId.value = null
    detail.value = null
    return
  }

  loadingList.value = true
  listError.value = ''
  try {
    const data = await getReports({
      reportType: reportType.value,
      portfolioId: selectedPortfolioId.value,
    })
    reports.value = data.items || []
    total.value = data.total || 0

    const stillThere = reports.value.some((r) => r.id === selectedId.value)
    if (!keepSelection || !stillThere) {
      selectedId.value = reports.value[0]?.id ?? null
    }
    await loadDetail()
  } catch (e) {
    console.error('加载日报列表失败:', e)
    listError.value = e.message || '加载失败，请稍后重试'
    reports.value = []
    total.value = 0
  } finally {
    loadingList.value = false
  }
}

async function loadDetail() {
  if (selectedId.value === null || selectedId.value === undefined) {
    detail.value = null
    return
  }
  loadingDetail.value = true
  try {
    detail.value = await getReport(selectedId.value)
  } catch (e) {
    console.error('加载日报详情失败:', e)
    // 报告可能刚被删掉：保留列表、清空详情，让用户换一份看
    detail.value = null
    toast.error(e.message || '加载日报详情失败')
  } finally {
    loadingDetail.value = false
  }
}

function selectReport(row) {
  if (row.id === selectedId.value) return
  selectedId.value = row.id
  loadDetail()
}

function switchType(key) {
  if (key === reportType.value) return
  reportType.value = key
  selectedId.value = null
  detail.value = null
  loadList()
}

async function handleGenerate() {
  if (!canGenerate.value) return
  generating.value = true
  try {
    await generateReport({
      reportType: reportType.value,
      portfolioId: selectedPortfolioId.value,
    })
    toast.success('今天的日报已生成')
    // 生成后停在「今天」这一份：新报告是最新日期，loadList 会默认选中第一条
    await loadList()
  } catch (e) {
    console.error('生成日报失败:', e)
    toast.error(e.message || '生成失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

async function handleDelete() {
  if (!detail.value) return
  if (!confirm(`确定删除「${detail.value.title || '这份日报'}」吗？只删这份记录，不影响资产数据。`)) {
    return
  }
  try {
    await deleteReport(detail.value.id)
    toast.success('日报已删除')
    selectedId.value = null
    await loadList()
  } catch (e) {
    console.error('删除日报失败:', e)
    toast.error(e.message || '删除失败，请稍后重试')
  }
}

// 切换组合：日报是按组合存的，必须重新拉列表
watch(selectedPortfolioId, () => {
  selectedId.value = null
  loadList()
})

// keep-alive：首次进入由 onActivated 触发加载，避免重复请求
onActivated(() => {
  if (reports.value.length === 0 && !listError.value) loadList()
})
</script>

<style scoped>
/* ---------- 顶部工具条 ---------- */
.report-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}

.type-switch {
  display: inline-flex;
  padding: 2px;
  background: var(--bg-color);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}

.type-btn {
  padding: 6px 14px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.type-btn:hover {
  color: var(--primary-color);
}

.type-btn.active {
  background: var(--card-bg);
  color: var(--primary-color);
  font-weight: 600;
  box-shadow: var(--shadow);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.toolbar-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ---------- 布局：左列表 + 右详情 ---------- */
.report-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.report-list {
  position: sticky;
  top: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
  padding: 8px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}

.list-title {
  padding: 2px 4px 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.list-item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0 8px;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.list-item:hover {
  background: var(--bg-color);
}

.list-item.active {
  background: rgba(37, 99, 235, 0.08);
  border-color: rgba(37, 99, 235, 0.35);
}

.list-date {
  grid-column: 1;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.list-pnl {
  grid-column: 2;
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.list-pct {
  grid-column: 1 / -1;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.list-meta {
  grid-column: 1 / -1;
  font-size: 11px;
  color: var(--text-secondary);
}

.signal-dot {
  color: var(--warning-color);
}

/* 红涨绿跌（A 股惯例） */
.is-up {
  color: #be123c;
}

.is-down {
  color: #15803d;
}

.is-flat {
  color: var(--text-secondary);
}

/* ---------- 详情 ---------- */
.report-detail {
  min-width: 0;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.detail-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--text-primary);
}

.detail-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-secondary);
}

.detail-flag {
  color: var(--warning-color);
}

/* KPI 卡片沿用全局 .overview-grid / .overview-item，只补上下间距 */
.report-detail :deep(.overview-grid) {
  margin-bottom: 12px;
  /*
    日报固定 5 张卡：全局的 180px 下限在 900~1100px 会掉成「4 + 1」，
    第 5 张孤零零占 1/4 宽。收到 150px 让 900px 也能一行放完。
  */
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}

/* ---------- 面板 ---------- */
.panel {
  padding: 12px 14px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  margin-bottom: 12px;
}

.panel-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.panel-note {
  font-size: 11px;
  color: var(--text-secondary);
}

.panel-badge {
  margin-left: 4px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--bg-color);
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

/* 表格里的数字列 */
.data-table .num {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.data-table .strong {
  font-weight: 700;
}

.data-table .muted {
  color: var(--text-secondary);
}

.data-table .small {
  font-size: 11px;
}

.data-table tfoot td {
  border-bottom: none;
  border-top: 1px solid var(--border-color);
  background: var(--bg-color);
  font-size: 13px;
}

/* 明细表都是 9 列，全局 12px 的单元格内边距在窄屏会吃掉 200 多像素，
   把内边距收到 8px，再给个宽度下限兜底，避免信号标签被挤成两行 */
.table-container :deep(.data-table th),
.table-container :deep(.data-table td) {
  padding: 10px 8px;
}

.table-container .data-table {
  min-width: 780px;
}

.asset-name {
  font-weight: 500;
  white-space: nowrap;
  color: var(--text-primary);
}

.asset-code {
  margin-left: 6px;
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

/* 信号单元格：标签一行、「差 x%」/「距目标 x%」另起一行，避免被列宽拆开 */
.signal-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  white-space: nowrap;
}

.signal-cell .tag {
  margin-left: 0;
}

/* 标签：信号 / 价格来源 / 快照状态 */
.tag {
  display: inline-block;
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 11px;
  line-height: 16px;
  font-weight: 600;
  white-space: nowrap;
}

.tag-hit {
  background: rgba(5, 150, 105, 0.12);
  color: #059669;
}

.tag-near {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.tag-watch {
  background: var(--bg-color);
  color: var(--text-secondary);
}

.tag-unknown {
  background: var(--bg-color);
  color: #94a3b8;
}

.tag-warn {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

/* ---------- 涨跌榜 ---------- */
.mover-block + .mover-block {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border-color);
}

.mover-title {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.mover-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 10px;
  align-items: baseline;
  padding: 2px 0;
  font-size: 13px;
}

.mover-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

/* ---------- 收入结构 / 集中度 ---------- */
.bar-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(70px, 130px) auto;
  gap: 8px;
  align-items: center;
  padding: 3px 0;
  font-size: 13px;
}

.bar-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.bar-track {
  height: 6px;
  border-radius: 999px;
  background: var(--bg-color);
  overflow: hidden;
}

.bar-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--primary-color), var(--primary-light));
}

.bar-value {
  font-size: 12px;
  color: var(--text-secondary);
  min-width: 40px;
  text-align: right;
}

.concentration {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border-color);
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.concentration b {
  color: var(--text-primary);
}

/* ---------- 目标进度 ---------- */
.goal-row {
  display: grid;
  grid-template-columns: minmax(0, 120px) minmax(80px, 1fr) 60px minmax(0, auto);
  gap: 10px;
  align-items: center;
  padding: 4px 0;
  font-size: 13px;
}

.goal-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.goal-track {
  height: 6px;
  border-radius: 999px;
  background: var(--bg-color);
  overflow: hidden;
}

.goal-fill {
  display: block;
  height: 100%;
  min-width: 4px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--primary-color), var(--primary-light));
}

.goal-fill.done {
  background: linear-gradient(90deg, #059669, #34d399);
}

.goal-pct {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--text-primary);
}

.goal-pct.done {
  color: #059669;
}

/* ---------- 观察池信号 ---------- */
.signal-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 4px 0;
  font-size: 13px;
}

.signal-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.signal-name .asset-code {
  margin-left: 5px;
}

.near-block {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color);
  line-height: 1.9;
}

.near-chip {
  display: inline-block;
  margin-right: 6px;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(245, 158, 11, 0.12);
  color: #b45309;
  font-size: 11px;
}

/* ---------- 提示 ---------- */
.notes {
  padding: 10px 14px;
  border-radius: var(--radius);
  background: var(--bg-color);
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.8;
}

.note-row + .note-row {
  border-top: 1px dashed var(--border-color);
  padding-top: 2px;
}

/* ---------- 响应式 ---------- */
@media (max-width: 1100px) {
  .report-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .report-list {
    position: static;
    max-height: 220px;
  }
}

@media (max-width: 900px) {
  .panel-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  /* 窄屏：名称 + 百分比一行，进度条次行，缺口说明末行 */
  .goal-row {
    grid-template-columns: minmax(0, 1fr) 60px;
    row-gap: 2px;
  }

  .goal-track {
    grid-column: 1 / -1;
    order: 1;
  }

  .goal-gap {
    grid-column: 1 / -1;
    order: 2;
  }
}
</style>
