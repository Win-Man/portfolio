<template>
  <div class="wl-page">
    <main class="container">
      <!-- 工具条 -->
      <div class="wl-toolbar">
        <div>
          <h1 class="wl-title">观察池</h1>
          <p class="wl-subtitle">候选标的 · 达标信号每日收盘后更新</p>
        </div>
        <div class="wl-toolbar-actions">
          <button
            type="button"
            class="btn btn-secondary btn-sm"
            :disabled="refreshingAll || loading"
            @click="handleRefreshAll"
          >
            <span v-if="refreshingAll" class="wl-spinner"></span>
            {{ refreshingAll ? '刷新中...' : '刷新全部' }}
          </button>
          <button type="button" class="btn btn-primary btn-sm" @click="openAddForm">
            ＋ 新增标的
          </button>
        </div>
      </div>

      <!-- 汇总卡 -->
      <div class="wl-summary">
        <div class="wl-summary-card">
          <div class="wl-summary-label">标的总数</div>
          <div class="wl-summary-value">{{ summary.total }}</div>
        </div>
        <div class="wl-summary-card">
          <div class="wl-summary-label">股息率达标</div>
          <div class="wl-summary-value wl-tone-hit">{{ summary.yieldHitCount }}</div>
          <div class="wl-summary-sub">共 {{ summary.total }} 项</div>
        </div>
        <div class="wl-summary-card">
          <div class="wl-summary-label">价格达标</div>
          <div class="wl-summary-value wl-tone-hit">{{ summary.priceHitCount }}</div>
          <div class="wl-summary-sub">
            已设目标价 {{ summary.priceTargetedCount }} 项
          </div>
        </div>
        <div class="wl-summary-card">
          <div class="wl-summary-label">数据日期</div>
          <div class="wl-summary-value wl-summary-date">
            {{ summary.latestSnapshotDate || '待抓取' }}
          </div>
          <div v-if="summary.staleCount > 0" class="wl-summary-sub wl-tone-near">
            含 {{ summary.staleCount }} 项陈旧
          </div>
          <div v-else class="wl-summary-sub">全部为最新快照</div>
        </div>
      </div>

      <!-- 错误态：不做假数据兜底 -->
      <div v-if="error" class="wl-error">
        <span>{{ error }}</span>
        <button type="button" class="btn btn-secondary btn-sm" @click="loadItems">重试</button>
      </div>

      <!-- 首屏骨架 -->
      <div v-else-if="loading" class="wl-card">
        <div v-for="n in 5" :key="n" class="wl-skeleton-row">
          <span class="wl-skeleton-block wl-skeleton-name"></span>
          <span class="wl-skeleton-block"></span>
          <span class="wl-skeleton-block"></span>
          <span class="wl-skeleton-block"></span>
        </div>
      </div>

      <!-- 空态 -->
      <div v-else-if="items.length === 0" class="wl-card wl-empty">
        <div class="wl-empty-icon">🎯</div>
        <p class="wl-empty-title">还没有观察标的</p>
        <p class="wl-empty-hint">先去搜索一个你想买的红利标的（代码或名称），设好目标股息率</p>
        <button type="button" class="btn btn-primary" @click="openAddForm">＋ 新增标的</button>
      </div>

      <!-- 表格 -->
      <div v-else class="wl-card wl-table-wrap">
        <table class="wl-table">
          <thead>
            <tr>
              <th>标的</th>
              <th class="wl-num">现价</th>
              <th class="wl-num">TTM 分红</th>
              <th class="wl-num">当前股息率</th>
              <th class="wl-num">目标股息率</th>
              <th class="wl-num">距目标</th>
              <th class="wl-num">触发价</th>
              <th class="wl-num">距触发价</th>
              <th class="wl-num">目标价</th>
              <th>信号</th>
              <th>数据日期</th>
              <th class="wl-actions-col">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in displayItems" :key="row.id">
              <tr :class="{ 'wl-row-stale': row.is_stale, 'wl-row-open': expandedId === row.id }">
                <td class="wl-name-cell">
                  <div class="wl-name-line">
                    <span class="wl-name">{{ row.name }}</span>
                    <span
                      v-if="row.is_held"
                      class="wl-chip-held"
                      :title="`已在组合：${row.held_portfolios.join('、')}`"
                    >已持有</span>
                  </div>
                  <div class="wl-code-line">
                    <span class="wl-code">{{ row.code }}</span>
                    <span class="wl-type">{{ row.asset_type }}</span>
                    <span v-if="row.refresh_error" class="wl-refresh-error" :title="row.refresh_error">
                      · {{ row.refresh_error }}
                    </span>
                  </div>
                </td>

                <td class="wl-num">
                  <template v-if="row.price !== null">
                    ¥{{ formatAmount(row.price) }}
                  </template>
                  <template v-else>
                    <span class="wl-muted">—</span>
                    <span class="wl-pending">待抓取</span>
                  </template>
                </td>

                <td class="wl-num">
                  <template v-if="row.ttm_dividend > 0">{{ formatCost(row.ttm_dividend) }}</template>
                  <span v-else class="wl-muted">无分红</span>
                </td>

                <td class="wl-num">{{ formatYield(row.current_yield) }}</td>
                <td class="wl-num">{{ formatYield(row.target_yield) }}</td>

                <td class="wl-num">{{ formatGapPercent(row.yield_gap_points, ' pct') }}</td>

                <td class="wl-num">
                  <template v-if="row.yield_trigger_price !== null">
                    {{ formatAmount(row.yield_trigger_price) }}
                  </template>
                  <span v-else class="wl-muted" title="无分红数据，无法按股息率定价">—</span>
                </td>

                <td class="wl-num">
                  {{ formatGapPercent(row.yield_trigger_gap_percent, '%') }}
                </td>

                <td class="wl-num">
                  <template v-if="row.target_price !== null">
                    {{ formatAmount(row.target_price) }}
                  </template>
                  <span v-else class="wl-muted">未设</span>
                </td>

                <td>
                  <div class="wl-signals">
                    <span class="wl-badge" :style="signalBadgeStyle(row.yield_signal)">
                      股息率 · {{ signalLabel(row.yield_signal) }}
                    </span>
                    <span class="wl-badge" :style="signalBadgeStyle(row.price_signal)">
                      价格 · {{ signalLabel(row.price_signal) }}
                    </span>
                  </div>
                </td>

                <td class="wl-date-cell">
                  <template v-if="row.snapshot_date">
                    {{ row.snapshot_date }}
                    <span
                      v-if="row.is_stale"
                      class="wl-stale-flag"
                      :title="`数据 ${row.stale_days} 天前`"
                    >⚠ 数据 {{ row.stale_days }} 天前</span>
                  </template>
                  <span v-else class="wl-muted">—</span>
                </td>

                <td class="wl-actions-col">
                  <div class="wl-row-actions">
                    <button type="button" class="wl-link" @click="toggleTrend(row)">
                      {{ expandedId === row.id ? '收起' : '走势' }}
                    </button>
                    <button
                      type="button"
                      class="wl-link"
                      :disabled="refreshingIds.has(row.id)"
                      @click="handleRefreshOne(row)"
                    >
                      {{ refreshingIds.has(row.id) ? '刷新中' : '刷新' }}
                    </button>
                    <button type="button" class="wl-link" @click="handleTransfer(row)">转入持仓</button>
                    <button type="button" class="wl-link" @click="openEditForm(row)">编辑</button>
                    <button type="button" class="wl-link wl-link-danger" @click="handleDelete(row)">
                      删除
                    </button>
                  </div>
                </td>
              </tr>

              <!-- 展开行：双轴走势图，一次只展开一个 -->
              <tr v-if="expandedId === row.id" class="wl-expand-row">
                <td :colspan="COLUMN_COUNT">
                  <div class="wl-expand">
                    <div class="wl-expand-head">
                      <span class="wl-expand-title">{{ row.name }} · 价格 / 股息率走势</span>
                      <span v-if="rangeSummary.label" class="wl-range-summary">
                        {{ rangeSummary.label }}
                      </span>
                      <span
                        v-if="exDividendsInWindow.length > 0"
                        class="wl-ex-hint"
                        title="除权日 TTM 滚动窗口会换入/换出某一期分红，股息率曲线当天出现台阶"
                      >
                        <i class="wl-ex-hint-line" :style="{ background: MARK_COLORS.exDividend }"></i>
                        浅灰竖线 = 除权日
                      </span>
                      <div class="wl-window-tabs">
                        <button
                          v-for="preset in RANGE_PRESETS"
                          :key="preset.key"
                          type="button"
                          class="wl-window-tab"
                          :class="{ active: rangeKey === preset.key }"
                          @click="changeRange(preset.key)"
                        >{{ preset.label }}</button>
                      </div>
                    </div>

                    <!-- 自定义起止区间：只在「自定义」档展开，避免常驻占位 -->
                    <div v-if="rangeKey === 'custom'" class="wl-range-picker">
                      <input
                        v-model="customStart"
                        type="date"
                        class="wl-range-input"
                        :min="historyRange.first_date || undefined"
                        :max="customEnd || historyRange.last_date || undefined"
                        aria-label="起始日期"
                      />
                      <span class="wl-range-sep">~</span>
                      <input
                        v-model="customEnd"
                        type="date"
                        class="wl-range-input"
                        :min="customStart || historyRange.first_date || undefined"
                        :max="historyRange.last_date || undefined"
                        aria-label="结束日期"
                      />
                      <button
                        type="button"
                        class="btn btn-secondary btn-sm"
                        :disabled="snapshotsLoading"
                        @click="applyCustomRange"
                      >应用</button>
                      <span v-if="historyRange.first_date" class="wl-range-hint">
                        可用 {{ historyRange.first_date }} ~ {{ historyRange.last_date }}
                      </span>
                    </div>

                    <WatchlistTrendChart
                      :snapshots="snapshots"
                      :target-yield="row.target_yield"
                      :ex-dividends="exDividends"
                      :loading="snapshotsLoading"
                    />
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </main>

    <WatchlistForm
      v-if="showForm"
      :item="editingItem"
      :submitting="formSubmitting"
      @close="closeForm"
      @submit="handleSubmit"
    />
  </div>
</template>

<script setup>
/**
 * 观察池页面（`#/watchlist`）。
 *
 * - 全局清单，不绑定组合（D1）：header 里的组合选择器在该 tab 下隐藏；
 * - 所有派生数值与两条信号都来自后端（D9），这里只做「信号 → 颜色/文案」映射、
 *   排序与序列构建（utils/watchlist.js）；
 * - 信号语义是「是否达标」，与红涨绿跌无关；
 * - 刷新失败（行情降级 mock）时后端不写快照、回 `refresh_error`，本页只展示文案。
 */
import { computed, defineAsyncComponent, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import WatchlistForm from '../components/WatchlistForm.vue'
import { toast } from '../composables/useToast.js'
import { formatAmount, formatCost } from '../utils/format.js'
import {
  alignExDividends,
  defaultCustomRange,
  formatGapPercent,
  formatYield,
  signalLabel,
  sortForDisplay,
  summarize,
  summarizeRange,
} from '../utils/watchlist.js'
import { signalBadgeStyle, MARK_COLORS } from '../utils/watchlistColors.js'
import {
  createWatchlistItem,
  deleteWatchlistItem,
  getWatchlist,
  getWatchlistDividends,
  getWatchlistHistoryRange,
  getWatchlistSnapshots,
  refreshAllWatchlist,
  refreshWatchlistItem,
  updateWatchlistItem,
} from '../api/watchlist.js'

// echarts 走异步：只有展开走势时才加载，避免拖慢首屏
const WatchlistTrendChart = defineAsyncComponent(
  () => import('../components/WatchlistTrendChart.vue'),
)

/**
 * 走势图的时间维度。
 *
 * 30 / 90 / 180 是日常最常用的三个窗口；「全部」直接取上市日至今
 * （后端回填已补到上市日）；「自定义」给一个起止日期区间选择器。
 */
const RANGE_PRESETS = [
  { key: '30', label: '30 天' },
  { key: '90', label: '90 天' },
  { key: '180', label: '180 天' },
  { key: 'all', label: '全部' },
  { key: 'custom', label: '自定义' },
]
const COLUMN_COUNT = 12

const router = useRouter()

const items = ref([])
const loading = ref(true)
const error = ref('')
const refreshingAll = ref(false)
const refreshingIds = reactive(new Set())

const expandedId = ref(null)
const snapshots = ref([])
const snapshotsLoading = ref(false)
const rangeKey = ref('90')
// 快照请求令牌：连续切档位时只认最后一次响应
let snapshotToken = 0

// 自定义区间的输入值（v-model）与**已应用**的区间分开：
// 用户改日期但没点「应用」时，图不该偷偷变。
const customStart = ref('')
const customEnd = ref('')
const appliedRange = ref({ start: '', end: '' })

// 该标的在库里已有的快照区间：给日期选择器设 min/max，也让用户知道能选到多早
const historyRange = ref({ first_date: null, last_date: null, count: 0 })

// 除权日：只在第一次展开某标的时拉一次（后端有 6h 分红缓存），
// 按 itemId 缓存，切区间时前端本地裁剪，不再请求
const exDividends = ref([])
const exDividendsCache = new Map()

/** 当前曲线实际覆盖的范围（首尾日期 + 点数），长区间时最能说明「数据取到哪」 */
const rangeSummary = computed(() =>
  summarizeRange(snapshots.value.map((row) => row?.trade_date)),
)

/**
 * 当前窗口内是否真的会画出除权日竖线。
 *
 * 除权日一次拉的是**全部历史**，但 30 天窗口里很可能一期都没有 ——
 * 这时不该显示「浅灰竖线 = 除权日」，否则用户在图上找不到任何浅灰竖线。
 * 复用同一个纯函数，保证「提示」和「实际画线」永远同一套判定。
 */
const exDividendsInWindow = computed(() =>
  alignExDividends(
    snapshots.value.map((row) => row.trade_date),
    exDividends.value,
  ),
)

const showForm = ref(false)
const editingItem = ref(null)
const formSubmitting = ref(false)

/** 已达标优先，同档按距目标降序（排序在前端做，不在后端） */
const displayItems = computed(() => sortForDisplay(items.value))
const summary = computed(() => summarize(items.value))

// ---------- 加载 ----------

async function loadItems() {
  loading.value = true
  error.value = ''
  try {
    const data = await getWatchlist()
    items.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.error('加载观察池失败:', e)
    error.value = e.message || '加载观察池失败，请稍后重试'
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(loadItems)

// ---------- 刷新 ----------

async function handleRefreshAll() {
  if (refreshingAll.value) return
  refreshingAll.value = true
  try {
    const data = await refreshAllWatchlist()
    items.value = Array.isArray(data) ? data : []
    const failed = items.value.filter((row) => row.refresh_error)
    if (failed.length === 0) {
      toast.success(`刷新完成：${items.value.length} 个标的已更新`)
    } else {
      toast.warning(`${items.value.length - failed.length} 个更新成功，${failed.length} 个行情获取失败（已保留上次数据）`)
    }
    await reloadExpandedSnapshots()
  } catch (e) {
    console.error('刷新观察池失败:', e)
    toast.error(e.message || '刷新失败，请稍后重试')
  } finally {
    refreshingAll.value = false
  }
}

async function handleRefreshOne(row) {
  if (refreshingIds.has(row.id)) return
  refreshingIds.add(row.id)
  try {
    const updated = await refreshWatchlistItem(row.id)
    const index = items.value.findIndex((item) => item.id === row.id)
    if (index !== -1) items.value[index] = updated
    if (updated.refresh_error) {
      toast.warning(`${row.name}：${updated.refresh_error}`)
    } else {
      toast.success(`${row.name} 已刷新`)
    }
    if (expandedId.value === row.id) await reloadExpandedSnapshots()
  } catch (e) {
    console.error('刷新单个标的失败:', e)
    toast.error(e.message || '刷新失败，请稍后重试')
  } finally {
    refreshingIds.delete(row.id)
  }
}

// ---------- 走势 ----------

/** 当前时间维度 → 请求参数（`{days}` / `{all}` / `{start,end}`，见 api/watchlist.js） */
function rangeRequest() {
  if (rangeKey.value === 'all') return { all: true }
  if (rangeKey.value === 'custom') {
    return {
      start: appliedRange.value.start || customStart.value,
      end: appliedRange.value.end || customEnd.value,
    }
  }
  return { days: Number(rangeKey.value) }
}

async function toggleTrend(row) {
  if (expandedId.value === row.id) {
    expandedId.value = null
    snapshots.value = []
    exDividends.value = []
    return
  }
  expandedId.value = row.id
  snapshots.value = []
  exDividends.value = exDividendsCache.get(row.id) ?? []
  rangeKey.value = '90'
  customStart.value = ''
  customEnd.value = ''
  appliedRange.value = { start: '', end: '' }
  historyRange.value = { first_date: null, last_date: null, count: 0 }
  await Promise.all([
    loadSnapshots(row.id, { days: 90 }),
    loadExDividends(row.id),
    loadHistoryRange(row.id),
  ])
}

/**
 * 除权日拉取：**失败也不打断走势图**。
 *
 * 竖线只是用来解释「股息率为什么在这里台阶」，属于辅助标注；
 * 分红接口挂了不该让整张图变成错误态，所以只记 warn、给空数组（图照画，只是没竖线）。
 * 一次拉**全部历史**：切 30 天 / 全部 / 自定义区间时前端本地裁剪，不再重复请求。
 */
async function loadExDividends(itemId) {
  if (exDividendsCache.has(itemId)) {
    if (expandedId.value === itemId) exDividends.value = exDividendsCache.get(itemId)
    return
  }
  try {
    const rows = await getWatchlistDividends(itemId, { all: true })
    const list = Array.isArray(rows) ? rows : []
    exDividendsCache.set(itemId, list)
    if (expandedId.value === itemId) exDividends.value = list
  } catch (e) {
    console.warn('加载除权日失败（竖线不显示，走势图不受影响）:', e)
    exDividendsCache.set(itemId, [])
    if (expandedId.value === itemId) exDividends.value = []
  }
}

/** 该标的已有快照的区间（只读库）：失败不影响画图，日期选择器退化到不设边界 */
async function loadHistoryRange(itemId) {
  try {
    const data = await getWatchlistHistoryRange(itemId)
    if (expandedId.value !== itemId) return
    historyRange.value = {
      first_date: data?.first_date ?? null,
      last_date: data?.last_date ?? null,
      count: Number(data?.count ?? 0) || 0,
    }
  } catch (e) {
    console.warn('加载历史区间失败（自定义区间仍可使用）:', e)
  }
}

async function changeRange(key) {
  if (rangeKey.value === key) return
  rangeKey.value = key
  if (expandedId.value === null) return

  if (key === 'custom') {
    // 首次进自定义：默认给「最近 3 年」（历史不足 3 年就给全部），并立刻出一版图
    if (!customStart.value || !customEnd.value) {
      const preset = defaultCustomRange(
        historyRange.value.first_date,
        historyRange.value.last_date,
      )
      customStart.value = preset.start
      customEnd.value = preset.end
      appliedRange.value = { ...preset }
    }
    if (!customStart.value || !customEnd.value) return
    await loadSnapshots(expandedId.value, {
      start: customStart.value,
      end: customEnd.value,
    })
    return
  }

  await loadSnapshots(expandedId.value, rangeRequest())
}

/** 点「应用」才真正按输入的区间取数（改日期不动图） */
async function applyCustomRange() {
  if (expandedId.value === null) return
  const start = String(customStart.value || '')
  const end = String(customEnd.value || '')
  if (!start || !end) {
    toast.warning('请选择起始与结束日期')
    return
  }
  if (start > end) {
    toast.warning('起始日期不能晚于结束日期')
    return
  }
  appliedRange.value = { start, end }
  await loadSnapshots(expandedId.value, { start, end })
}

async function loadSnapshots(itemId, request) {
  // 令牌防抖：连续切档位时，慢的那次响应不能覆盖后发的请求
  const token = ++snapshotToken
  snapshotsLoading.value = true
  try {
    const data = await getWatchlistSnapshots(itemId, request)
    if (token !== snapshotToken || expandedId.value !== itemId) return
    snapshots.value = Array.isArray(data) ? data : []
  } catch (e) {
    if (token !== snapshotToken) return
    console.error('加载快照失败:', e)
    snapshots.value = []
    toast.error(e.message || '加载走势数据失败')
  } finally {
    if (token === snapshotToken) snapshotsLoading.value = false
  }
}

async function reloadExpandedSnapshots() {
  if (expandedId.value === null) return
  // 刷新可能补进了更早的历史（回填到上市日）→ 区间元信息要一起更新
  await Promise.all([
    loadSnapshots(expandedId.value, rangeRequest()),
    loadHistoryRange(expandedId.value),
  ])
}

// ---------- 新增 / 编辑 / 删除 ----------

function openAddForm() {
  editingItem.value = null
  showForm.value = true
}

function openEditForm(row) {
  editingItem.value = { ...row }
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  editingItem.value = null
}

async function handleSubmit(payload) {
  formSubmitting.value = true
  try {
    if (editingItem.value) {
      await updateWatchlistItem(editingItem.value.id, payload)
      toast.success('观察标的已更新')
    } else {
      await createWatchlistItem(payload)
      toast.success('已加入观察池')
    }
    closeForm()
    await loadItems()
  } catch (e) {
    // 409「该标的已在观察池中」等后端文案直接透传，弹窗保持打开以便修改
    console.error('保存观察标的失败:', e)
    toast.error(e.message || '保存失败，请稍后重试')
  } finally {
    formSubmitting.value = false
  }
}

async function handleDelete(row) {
  if (!confirm(`确定要把「${row.name}（${row.code}）」从观察池删除吗？其历史快照会一并删除。`)) {
    return
  }
  try {
    await deleteWatchlistItem(row.id)
    exDividendsCache.delete(row.id)
    if (expandedId.value === row.id) {
      expandedId.value = null
      snapshots.value = []
      exDividends.value = []
    }
    await loadItems()
    toast.success('已删除')
  } catch (e) {
    console.error('删除观察标的失败:', e)
    toast.error(e.message || '删除失败，请稍后重试')
  }
}

// ---------- 转入持仓（D12：复用既有 AssetForm 与既有写入接口） ----------

function handleTransfer(row) {
  router.push({
    path: '/',
    query: {
      addCode: row.code,
      addName: row.name,
      addPrice: row.price ?? '',
      from: 'watchlist',
    },
  })
}
</script>

<style scoped>
.wl-page {
  width: 100%;
}

.wl-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}

.wl-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.wl-subtitle {
  margin-top: 2px;
  font-size: 13px;
  color: var(--text-secondary);
}

.wl-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.wl-toolbar-actions .btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.wl-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--border-color);
  border-top-color: var(--primary-color);
  border-radius: 50%;
  animation: wl-spin 0.8s linear infinite;
}

@keyframes wl-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 汇总卡 */
.wl-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.wl-summary-card {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 14px 16px;
}

.wl-summary-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.wl-summary-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  line-height: 1.3;
}

.wl-summary-date {
  font-size: 17px;
}

.wl-summary-sub {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.wl-tone-hit {
  color: #059669;
}

.wl-tone-near {
  color: #b45309;
}

/* 卡片容器 */
.wl-card {
  background: var(--card-bg);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 4px 0;
}

/* 错误条 */
.wl-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  margin-bottom: 16px;
  border-radius: var(--radius);
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #b91c1c;
  font-size: 14px;
}

/* 骨架 */
.wl-skeleton-row {
  display: flex;
  gap: 24px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border-color);
}

.wl-skeleton-row:last-child {
  border-bottom: none;
}

.wl-skeleton-block {
  height: 14px;
  flex: 1;
  border-radius: 4px;
  background: linear-gradient(90deg, #f1f5f9, #e2e8f0, #f1f5f9);
  background-size: 200% 100%;
  animation: wl-shimmer 1.2s ease-in-out infinite;
}

.wl-skeleton-name {
  flex: 2;
}

@keyframes wl-shimmer {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
}

/* 空态 */
.wl-empty {
  padding: 56px 20px;
  text-align: center;
}

.wl-empty-icon {
  font-size: 34px;
  opacity: 0.6;
}

.wl-empty-title {
  margin-top: 8px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.wl-empty-hint {
  margin: 6px 0 18px;
  font-size: 13px;
  color: var(--text-secondary);
}

/* 表格 */
.wl-table-wrap {
  overflow-x: auto;
  padding: 0;
}

.wl-table {
  width: 100%;
  min-width: 1220px;
  border-collapse: collapse;
  font-size: 13px;
}

.wl-table th,
.wl-table td {
  padding: 11px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
  vertical-align: middle;
}

.wl-table th {
  background: var(--bg-color);
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
}

.wl-table tbody tr:hover {
  background: #f8fafc;
}

.wl-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 陈旧行：左侧 3px 黄条 */
.wl-row-stale > td:first-child {
  box-shadow: inset 3px 0 0 #f59e0b;
}

.wl-row-open {
  background: #f8fafc;
}

.wl-name-cell {
  min-width: 190px;
}

.wl-name-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.wl-name {
  font-weight: 600;
  color: var(--text-primary);
}

.wl-code-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-secondary);
}

.wl-code {
  font-variant-numeric: tabular-nums;
}

.wl-type {
  padding: 0 5px;
  border-radius: 3px;
  background: var(--bg-color);
  font-size: 11px;
}

.wl-chip-held {
  padding: 1px 6px;
  border-radius: 999px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
  font-size: 11px;
  cursor: help;
  white-space: nowrap;
}

.wl-refresh-error {
  color: #b45309;
  cursor: help;
}

.wl-muted {
  color: var(--text-secondary);
}

.wl-pending {
  margin-left: 4px;
  font-size: 11px;
  color: #94a3b8;
}

.wl-signals {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.wl-badge {
  display: inline-block;
  padding: 2px 7px;
  border: 1px solid transparent;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.wl-date-cell {
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.wl-stale-flag {
  display: block;
  margin-top: 2px;
  color: #b45309;
  font-size: 11px;
  cursor: help;
}

.wl-actions-col {
  white-space: nowrap;
}

.wl-row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.wl-link {
  padding: 0;
  border: none;
  background: none;
  color: var(--primary-color);
  font-size: 12px;
  cursor: pointer;
}

.wl-link:hover {
  text-decoration: underline;
}

.wl-link:disabled {
  color: var(--text-secondary);
  cursor: not-allowed;
  text-decoration: none;
}

.wl-link-danger {
  color: var(--danger-color);
}

/* 展开行 */
.wl-expand-row > td {
  padding: 0;
  background: #fbfdff;
}

.wl-expand {
  padding: 12px 16px 16px;
  border-bottom: 1px solid var(--border-color);
}

.wl-expand-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.wl-expand-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

/* 除权日竖线的图例说明：靠左紧跟标题，右侧留给时间维度切换 */
.wl-ex-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  cursor: help;
}

.wl-ex-hint-line {
  display: inline-block;
  width: 2px;
  height: 12px;
  border-radius: 1px;
}

.wl-window-tabs {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  /* 靠右：左侧是标题 / 区间概要 / 除权日图例 */
  margin-left: auto;
  padding: 2px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  background: var(--card-bg);
}

.wl-window-tab {
  padding: 3px 10px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.wl-window-tab.active {
  background: var(--primary-color);
  color: #fff;
}

/* 曲线覆盖范围：首尾日期 + 点数，切换长区间时一眼确认取到哪一天 */
.wl-range-summary {
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 自定义区间选择器 */
.wl-range-picker {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: -2px 0 10px;
  font-size: 12px;
  color: var(--text-secondary);
}

.wl-range-input {
  padding: 3px 6px;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  background: var(--card-bg);
  color: var(--text-primary);
  font-size: 12px;
  font-family: inherit;
  font-variant-numeric: tabular-nums;
}

.wl-range-input:focus {
  outline: none;
  border-color: var(--primary-color);
}

.wl-range-sep {
  color: var(--text-secondary);
}

.wl-range-hint {
  margin-left: 2px;
  font-size: 11px;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}
</style>
