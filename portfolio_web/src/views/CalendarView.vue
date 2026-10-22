<template>
  <div class="container">
    <section class="card calendar-section">
      <div class="calendar-head">
        <div class="calendar-title-wrap">
          <h2 class="calendar-title">收入日历</h2>
          <span v-if="currentPortfolio" class="calendar-portfolio">{{ currentPortfolio.name }}</span>
        </div>

        <div class="calendar-controls">
          <div class="year-switch">
            <button type="button" class="year-btn" title="上一年" @click="shiftYear(-1)">&lt;</button>
            <span class="year-label">{{ year }}年</span>
            <button type="button" class="year-btn" title="下一年" @click="shiftYear(1)">&gt;</button>
          </div>

          <div class="segmented" role="group" aria-label="视图切换">
            <button
              type="button"
              class="segmented-item"
              :class="{ active: viewMode === 'year' }"
              @click="setView('year')"
            >年</button>
            <button
              type="button"
              class="segmented-item"
              :class="{ active: viewMode === 'month' }"
              @click="setView('month')"
            >月</button>
          </div>
        </div>
      </div>

      <!-- 口径对照：自然年「已收录」+「去年同期预估」 vs 概览卡「预计年收入」同口径的「近 12 个月」 -->
      <div v-if="calendar && !isEmptyPortfolio" class="calendar-summary">
        <span class="summary-item">
          <span class="summary-label">{{ year }}年合计</span>
          <span class="summary-value">¥{{ formatAmount(totalYear) }}</span>
        </span>
        <span class="summary-item summary-breakdown">
          已收录 ¥{{ formatAmount(recordedTotal) }} · 预估 ¥{{ formatAmount(estimatedTotal) }}
        </span>
        <span class="summary-item">
          <span class="summary-label">近 12 个月</span>
          <span class="summary-value">¥{{ formatAmount(rolling12mTotal) }}</span>
          <InfoTooltip :text="ROLLING_TOOLTIP" />
        </span>
        <span v-if="estimatedTotal > 0" class="summary-badge summary-badge-estimated">
          {{ estimatedMonthsText }} 为去年同期预估（akshare 尚未收录）
        </span>
        <span v-if="pendingMonths.length > 0" class="summary-badge">
          {{ pendingMonthText }} 无去年同期参照，显示 ¥0.00（不外推）
        </span>
      </div>

      <!-- 组合列表为空（正常会被 router 守卫挡回 /，这里只是兜底） -->
      <div v-if="!selectedPortfolioId" class="empty-state">
        <p>还没有投资组合，先创建第一个组合再查看收入日历</p>
        <router-link class="btn btn-primary" to="/">去创建组合</router-link>
      </div>

      <template v-else>
        <div v-if="yearLoading" class="loading">加载日历...</div>

        <div v-else-if="yearError" class="calendar-error">
          <span>{{ yearError }}</span>
          <button type="button" class="btn btn-secondary btn-sm" @click="loadYear({ force: true })">
            重试
          </button>
        </div>

        <!-- 组合无资产：整个日历空态（spec §8-1，asset_count 由后端给） -->
        <div v-else-if="isEmptyPortfolio" class="empty-state">
          <p>该组合暂无资产</p>
        </div>

        <YearGrid
          v-else-if="viewMode === 'year'"
          :months="months"
          :selected-month="selectedMonth"
          @select-month="selectMonth"
        />

        <!-- echarts 走异步组件，避免被打进主包 -->
        <component
          :is="MonthChart"
          v-else
          :year="year"
          :months="months"
          @select-month="selectMonthFromChart"
        />
      </template>
    </section>

    <MonthSheet
      v-if="sheetOpen && selectedMonth"
      :year="year"
      :month="selectedMonth"
      :data="monthData"
      :loading="monthLoading"
      :error="monthError"
      :assets="assets"
      :submitting="manualSubmitting"
      :confirming-key="confirmingKey"
      :manual-form-open="manualFormOpen"
      @close="closeSheet"
      @prev-month="shiftMonth(-1)"
      @next-month="shiftMonth(1)"
      @confirm="handleConfirm"
      @delete-manual="handleDeleteManual"
      @submit-manual="handleManualSubmit"
      @update:manual-form-open="manualFormOpen = $event"
      @retry="loadMonth({ force: true })"
    />
  </div>
</template>

<script setup>
/**
 * 收入日历视图（spec §7.4）：年视图 / 月视图切换 + 月 Sheet + 点击确认 + 补录。
 *
 * 数据流：
 *   - `getCalendar(pid, year)` → 12 个月合计（年视图网格 / 月视图堆叠柱共用）
 *   - `getMonthDividends(pid, year, month)` → 月 Sheet 明细
 *   - confirm / manual 后会**失效本地缓存并强制重拉**，保证两个视图的数字自洽
 *
 * 缓存（spec §8-19）：年度与月度数据都存在组件内 ref 里，同一组合 + 同一年/月不重复打接口；
 * App.vue 用 `<keep-alive>` 包 router-view，所以切 tab 再切回来也不会重新请求。
 */
import { computed, defineAsyncComponent, ref, watch } from 'vue'

import YearGrid from '../components/YearGrid.vue'
import MonthSheet from '../components/MonthSheet.vue'
import InfoTooltip from '../components/InfoTooltip.vue'
// 图表依赖 echarts（约 460KB），异步加载，保证 echarts 独立分包
const MonthChart = defineAsyncComponent(() => import('../components/MonthlyStackedBarChart.vue'))

import { usePortfolioContext } from '../composables/usePortfolioContext.js'
import { toast } from '../composables/useToast.js'
import { getAssets } from '../api/portfolio.js'
import { formatAmount } from '../utils/format.js'
import { pendingMonthsOfYear } from '../utils/calendar.js'
import {
  confirmDividend,
  createManualDividend,
  deleteManualDividend,
  getCalendar,
  getManualDividends,
  getMonthDividends,
} from '../api/calendar.js'

const { portfolios, selectedPortfolioId } = usePortfolioContext()

const THIS_YEAR = new Date().getFullYear()

const year = ref(THIS_YEAR)
/** 'year' | 'month' */
const viewMode = ref('year')
const selectedMonth = ref(null)

const calendar = ref(null)
const yearLoading = ref(false)
const yearError = ref('')

const monthData = ref(null)
const monthLoading = ref(false)
const monthError = ref('')

const sheetOpen = ref(false)
const manualFormOpen = ref(false)
const manualSubmitting = ref(false)
const confirmingKey = ref('')

/** 补录表单的标的选项（当前组合的资产） */
const assets = ref([])

// ---------- 缓存 + 并发保护 ----------
const yearCache = ref(null) // { key, data }
const monthCache = ref({}) // key → MonthDividendsResponse
let yearSeq = 0
let monthSeq = 0

const months = computed(() => (Array.isArray(calendar.value?.months) ? calendar.value.months : []))
const currentPortfolio = computed(
  () => portfolios.value.find((p) => p.id === selectedPortfolioId.value) || null
)
const isEmptyPortfolio = computed(
  () => !!calendar.value && Number(calendar.value.asset_count ?? 0) === 0
)

/** 自然年（1–12 月）合计（含「去年同期预估」部分） */
const totalYear = computed(() => Number(calendar.value?.total_year ?? 0))
/** 当年「已收录」部分（不含预估） */
const recordedTotal = computed(() => Number(calendar.value?.recorded_total ?? 0))
/** 当年「去年同期预估」部分 */
const estimatedTotal = computed(() => Number(calendar.value?.estimated_total ?? 0))
/** 近 12 个月已到账合计：口径与「投资组合」页概览卡「预计年收入」一致 */
const rolling12mTotal = computed(() => Number(calendar.value?.rolling_12m_total ?? 0))

/**
 * 所选年份里「还没到、且 akshare 没有记录」的月份。
 * 这些月份显示 ¥0.00 是 D2 的既定口径（不外推未来），必须在界面上说清楚，
 * 否则用户会以为「这个月真的没有分红」或「日历算漏了」。
 */
const pendingMonths = computed(() => {
  const list = months.value
  return pendingMonthsOfYear(year.value).filter((m) => {
    const hit = list.find((x) => Number(x.month) === m)
    return !hit || (Number(hit.count ?? 0) === 0 && Number(hit.estimated_amount ?? 0) === 0)
  })
})
const pendingMonthText = computed(() => `${pendingMonths.value.join('、')} 月`)

/** 有「去年同期预估」的月份（用于徽标文案） */
const estimatedMonths = computed(() =>
  months.value.filter((m) => Number(m?.estimated_amount ?? 0) > 0).map((m) => Number(m.month))
)
const estimatedMonthsText = computed(() => `${estimatedMonths.value.join('、')} 月`)

const ROLLING_TOOLTIP = computed(
  () => `「近 12 个月」= 今天往前 365 天**已到账**的分红合计，与「投资组合」页概览卡的「预计年收入」同口径（Σ 持股数 × 每股 TTM 分红），正常情况下两个数字相等。`
    + `「${year.value}年合计」= 自然年 1–12 月，含「去年同期预估」：akshare 只收录已发生的分红，未来月份用去年同月实际收到的那笔预估（标「预估」），`
    + `因此年合计通常略小于/接近「近 12 个月」，两者窗口不同，不相等是正常的。`
)

function yearKey() {
  return `${selectedPortfolioId.value}|${year.value}`
}

function monthKey() {
  return `${selectedPortfolioId.value}|${year.value}|${selectedMonth.value}`
}

// ---------- 年度日历 ----------
async function loadYear({ force = false } = {}) {
  const pid = selectedPortfolioId.value
  if (!pid) return

  // 先自增序号：即使下面走缓存直接返回，也要作废在途的旧请求，避免旧年份的结果写回来
  const seq = ++yearSeq
  const key = yearKey()

  if (!force && yearCache.value && yearCache.value.key === key) {
    calendar.value = yearCache.value.data
    yearError.value = ''
    yearLoading.value = false
    return
  }

  yearLoading.value = true
  yearError.value = ''
  try {
    const data = await getCalendar(pid, year.value)
    if (seq !== yearSeq) return
    calendar.value = data
    yearCache.value = { key, data }
  } catch (e) {
    if (seq !== yearSeq) return
    console.error('加载收入日历失败:', e)
    calendar.value = null
    yearError.value = e.message || '日历加载失败，请稍后重试'
  } finally {
    if (seq === yearSeq) yearLoading.value = false
  }
}

// ---------- 月度明细 ----------
async function loadMonth({ force = false } = {}) {
  const pid = selectedPortfolioId.value
  const month = selectedMonth.value
  if (!pid || !month) {
    monthSeq++
    monthLoading.value = false
    monthData.value = null
    return
  }

  // 同理：换月时先作废在途请求（否则「新月份命中缓存 + 旧月份请求在飞」会写错数据）
  const seq = ++monthSeq
  const key = monthKey()

  if (!force && monthCache.value[key]) {
    monthData.value = monthCache.value[key]
    monthError.value = ''
    monthLoading.value = false
    return
  }

  monthLoading.value = true
  monthError.value = ''
  try {
    const data = await getMonthDividends(pid, year.value, month)
    if (seq !== monthSeq) return
    monthData.value = data
    monthCache.value = { ...monthCache.value, [key]: data }
  } catch (e) {
    if (seq !== monthSeq) return
    console.error('加载月度分红明细失败:', e)
    monthData.value = null
    monthError.value = e.message || '月度明细加载失败，请稍后重试'
  } finally {
    if (seq === monthSeq) monthLoading.value = false
  }
}

// ---------- 组合资产（补录表单选项） ----------
async function loadAssets() {
  const pid = selectedPortfolioId.value
  if (!pid) {
    assets.value = []
    return
  }
  try {
    const data = await getAssets(pid)
    assets.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.error('加载资产列表失败:', e)
    assets.value = []
  }
}

// ---------- 视图交互 ----------
function setView(mode) {
  viewMode.value = mode === 'month' ? 'month' : 'year'
  // 第一次切到月视图时给一个合理默认月（今年 → 当前月；否则 1 月）
  if (viewMode.value === 'month' && !selectedMonth.value) {
    selectedMonth.value = year.value === THIS_YEAR ? new Date().getMonth() + 1 : 1
  }
}

/** 年视图点格：选中该月 → 切到月视图 + 打开月 Sheet（spec §7.4.2） */
function selectMonth(month) {
  const value = Number(month)
  if (!Number.isFinite(value) || value < 1 || value > 12) return
  selectedMonth.value = value
  viewMode.value = 'month'
  sheetOpen.value = true
  manualFormOpen.value = false
  loadMonth()
}

/**
 * 月视图点柱：`selectMonth(month)` + 切回年视图（spec §7.4.3）——
 * 与年视图点格构成「年 → 月 → 年」的交互闭环，两个视图都能 drill-down 到月 Sheet。
 */
function selectMonthFromChart(month) {
  selectMonth(month)
  viewMode.value = 'year'
}

function closeSheet() {
  sheetOpen.value = false
  manualFormOpen.value = false
}

async function shiftYear(delta) {
  const next = Number(year.value) + Number(delta)
  if (!Number.isFinite(next)) return
  year.value = next
  closeSheet()
  await loadYear()
}

/** 月 Sheet 上/下一月：跨年时 year ±1 并**重新请求**年度日历（spec §8-16/17） */
async function shiftMonth(delta) {
  if (!selectedMonth.value) return
  let month = Number(selectedMonth.value) + Number(delta)
  let targetYear = Number(year.value)
  if (month < 1) {
    month = 12
    targetYear -= 1
  } else if (month > 12) {
    month = 1
    targetYear += 1
  }

  if (targetYear !== Number(year.value)) {
    year.value = targetYear
    await loadYear()
  }
  selectedMonth.value = month
  await loadMonth()
}

// ---------- 写操作：确认 / 补录 / 删除 ----------
function invalidateCaches() {
  const prefix = `${selectedPortfolioId.value}|`
  if (yearCache.value && String(yearCache.value.key).startsWith(prefix)) yearCache.value = null
  const next = {}
  for (const [key, value] of Object.entries(monthCache.value)) {
    if (!key.startsWith(prefix)) next[key] = value
  }
  monthCache.value = next
}

async function refreshAfterMutation() {
  invalidateCaches()
  await Promise.all([loadYear({ force: true }), loadMonth({ force: true })])
}

/** 「点击确认」：predicted / announced → paid（幂等，不重复加金额） */
async function handleConfirm(item) {
  const pid = selectedPortfolioId.value
  if (!pid || !item) return
  const key = `${item.code}|${item.ex_date}`
  if (confirmingKey.value) return
  confirmingKey.value = key
  try {
    await confirmDividend(pid, item.code, item.ex_date)
    toast.success('已确认，该笔分红标记为已到账')
    await refreshAfterMutation()
  } catch (e) {
    console.error('确认分红失败:', e)
    toast.error(e.message || '确认失败，请稍后重试')
  } finally {
    confirmingKey.value = ''
  }
}

/** 「＋ 补录」提交：成功后收起表单并刷新当月 + 当年 */
async function handleManualSubmit(payload) {
  const pid = selectedPortfolioId.value
  if (!pid) return
  manualSubmitting.value = true
  try {
    await createManualDividend(pid, payload)
    manualFormOpen.value = false
    toast.success('补录成功')
    await refreshAfterMutation()
  } catch (e) {
    // 后端文案（422「不能补未来日期；预告请等待 akshare 收录」/「组合内不存在该标的」）直接透传，
    // 表单保持展开，用户改完可以直接重试
    console.error('补录分红失败:', e)
    toast.error(e.message || '补录失败，请稍后重试')
  } finally {
    manualSubmitting.value = false
  }
}

/**
 * 删除补录记录。
 * 月明细（MonthDividendItem）里没有补录记录 id，先用 GET /dividends/manual
 * 按 (code, ex_date) 反查 record_id，再 DELETE。
 */
async function handleDeleteManual(item) {
  const pid = selectedPortfolioId.value
  if (!pid || !item) return
  if (!confirm(`确定要删除补录记录「${item.name || item.code} ${item.ex_date}」吗？`)) return

  try {
    const records = await getManualDividends(pid)
    const hit = (Array.isArray(records) ? records : []).find(
      (r) => String(r.code) === String(item.code) && String(r.ex_date) === String(item.ex_date)
    )
    if (!hit) {
      toast.error('补录记录不存在')
      await refreshAfterMutation()
      return
    }
    await deleteManualDividend(pid, hit.id)
    toast.success('补录记录已删除')
    await refreshAfterMutation()
  } catch (e) {
    console.error('删除补录记录失败:', e)
    toast.error(e.message || '删除失败，请稍后重试')
  }
}

// ---------- 组合切换：关闭 Sheet、重置视图与缓存，再拉新组合的数据（spec §8-18） ----------
watch(
  selectedPortfolioId,
  async (pid) => {
    yearSeq++
    monthSeq++
    yearLoading.value = false
    monthLoading.value = false

    viewMode.value = 'year'
    selectedMonth.value = null
    sheetOpen.value = false
    manualFormOpen.value = false
    confirmingKey.value = ''
    yearCache.value = null
    monthCache.value = {}
    calendar.value = null
    monthData.value = null
    yearError.value = ''
    monthError.value = ''

    if (!pid) {
      assets.value = []
      return
    }
    await Promise.all([loadYear(), loadAssets()])
  },
  { immediate: true }
)
</script>

<style scoped>
.calendar-section {
  margin-bottom: 24px;
}

.calendar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}

.calendar-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.calendar-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.calendar-portfolio {
  font-size: 13px;
  color: var(--text-secondary);
  max-width: 16em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.calendar-controls {
  display: flex;
  align-items: center;
  gap: 16px;
}

/* 口径对照行：自然年已收录 + 近 12 个月（= 概览「预计年收入」口径） */
.calendar-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 20px;
  padding: 10px 14px;
  margin-bottom: 16px;
  background: var(--bg-color);
  border-radius: var(--radius);
  font-size: 13px;
  color: var(--text-secondary);
}

.summary-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.summary-label {
  color: var(--text-secondary);
}

.summary-value {
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.summary-breakdown {
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.summary-badge {
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.35);
  color: #b45309;
}

/* 「预估」是中性提示（不是警告），用灰蓝区别于「无参照」的黄色 */
.summary-badge-estimated {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #475569;
}

.year-switch {
  display: flex;
  align-items: center;
  gap: 10px;
}

.year-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  background: var(--card-bg);
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.year-btn:hover {
  border-color: var(--primary-color);
  color: var(--primary-color);
}

.year-label {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  min-width: 4.5em;
  text-align: center;
}

/* 「年 / 月」分段控件：圆角 pill，选中态主色底 */
.segmented {
  display: inline-flex;
  padding: 3px;
  gap: 2px;
  background: var(--bg-color);
  border: 1px solid var(--border-color);
  border-radius: 999px;
}

.segmented-item {
  padding: 5px 18px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.segmented-item:hover {
  color: var(--primary-color);
}

.segmented-item.active {
  background: var(--primary-color);
  color: #fff;
}

.calendar-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px 20px;
  color: var(--danger-color);
}

@media (max-width: 640px) {
  .calendar-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
