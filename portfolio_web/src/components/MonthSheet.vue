<template>
  <div class="sheet-backdrop" @click.self="$emit('close')">
    <section class="sheet" role="dialog" aria-modal="true" :aria-label="`${title} 分红明细`">
      <header class="sheet-header">
        <div class="sheet-nav">
          <button type="button" class="sheet-nav-btn" title="上一月" @click="$emit('prev-month')">
            &lt;
          </button>
          <h3 class="sheet-title">{{ title }}</h3>
          <button type="button" class="sheet-nav-btn" title="下一月" @click="$emit('next-month')">
            &gt;
          </button>
        </div>

        <button type="button" class="btn btn-primary btn-sm" @click="toggleManual">
          ＋ 补录
        </button>
      </header>

      <div class="sheet-sub">
        <span class="sheet-sub-text">
          预计 约 {{ recordCount }} 笔，合计 ¥{{ formatAmount(totalAmount) }}
        </span>
        <InfoTooltip :text="STATUS_TOOLTIP" />
      </div>

      <ManualDividendForm
        v-if="manualFormOpen"
        :assets="assets"
        :submitting="submitting"
        @submit="$emit('submit-manual', $event)"
        @cancel="$emit('update:manualFormOpen', false)"
      />

      <div class="sheet-body">
        <div v-if="loading" class="loading">加载分红明细...</div>

        <div v-else-if="error" class="sheet-error">
          <span>{{ error }}</span>
          <button type="button" class="btn btn-secondary btn-sm" @click="$emit('retry')">重试</button>
        </div>

        <div v-else-if="items.length === 0" class="empty-state sheet-empty">
          <p>该月暂无分红记录（合计 ¥0.00）</p>
        </div>

        <ul v-else class="sheet-list">
          <li v-for="item in items" :key="rowKey(item)" class="sheet-row">
            <div class="row-main">
              <span class="row-name" :title="item.name || item.code">{{ item.name || item.code }}</span>
              <span class="row-chip" :style="sourceChipStyle(item.source)">
                {{ sourceLabel(item.source) }}
              </span>
              <span class="row-date">{{ formatDayLabel(item.ex_date) }}</span>
              <span
                v-if="item.is_mock_price"
                class="row-chip row-chip-muted"
                :style="chipStyle(MOCK_CHIP)"
                :title="MOCK_CHIP.title"
              >{{ MOCK_CHIP.label }}</span>
            </div>

            <div class="row-side">
              <span class="row-amount" :class="{ 'is-negative': amountOf(item) < 0 }">
                {{ signedAmount(item.amount) }}
              </span>

              <span
                class="row-chip"
                :style="chipStyle(statusColor(item.status))"
              >{{ statusLabel(item.status) }}</span>

              <span
                v-if="item.is_estimated"
                class="row-chip"
                :style="chipStyle(ESTIMATED_CHIP)"
                :title="ESTIMATED_CHIP.title"
              >{{ ESTIMATED_CHIP.label }}</span>

              <button
                v-if="needsConfirm(item)"
                type="button"
                class="btn btn-primary btn-sm"
                :disabled="confirmingKey === rowKey(item)"
                @click="$emit('confirm', item)"
              >
                {{ confirmingKey === rowKey(item) ? '确认中...' : '点击确认' }}
              </button>

              <button
                v-else-if="item.is_manual || item.status === 'manual'"
                type="button"
                class="btn btn-danger btn-sm"
                @click="$emit('delete-manual', item)"
              >删除</button>
            </div>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<script setup>
/**
 * 月 Sheet：底部弹层（不是 Modal），70vh，顶部 16px 圆角，50% 遮罩点击关闭（spec D11 / §7.4.4）。
 *
 * 数据由父组件（CalendarView）拉好后通过 `data` 传入，本组件只负责展示与派发事件：
 * 确认一笔 / 删除补录 / 新增补录 / 上下月（跨年由父组件重新请求）。
 */
import { computed } from 'vue'

import InfoTooltip from './InfoTooltip.vue'
import ManualDividendForm from './ManualDividendForm.vue'
import { formatAmount, formatSigned } from '../utils/format.js'
import { formatDayLabel, formatMonthLabel } from '../utils/calendar.js'
import {
  ESTIMATED_CHIP,
  MOCK_CHIP,
  SOURCE_CHIP_LABELS,
  SOURCE_COLORS,
  STATUS_COLORS,
} from '../utils/calendarColors.js'

const props = defineProps({
  year: { type: Number, required: true },
  /** 1..12 */
  month: { type: Number, required: true },
  /** MonthDividendsResponse | null */
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  /** 当前组合的资产（补录表单的标的选项） */
  assets: { type: Array, default: () => [] },
  /** 补录提交中 */
  submitting: { type: Boolean, default: false },
  /** 正在确认的行 key（`code|ex_date`），用于按行 disable */
  confirmingKey: { type: String, default: '' },
  /** 行内补录表单是否展开（由父组件受控，成功后父组件收起） */
  manualFormOpen: { type: Boolean, default: false },
})

const emit = defineEmits([
  'close',
  'prev-month',
  'next-month',
  'confirm',
  'delete-manual',
  'submit-manual',
  'update:manualFormOpen',
  'retry',
])

const STATUS_TOOLTIP =
  '状态按 akshare 的 IMPLEMENT_STATUS 推断：含「已实施」= 已到账；含「已发布 / 实施中」= 待到账；其余（董事会预案 / 股东大会通过 / 缺失）保守记为预测。「点击确认」把预告标成已到账（幂等，不会重复加金额）。'

const items = computed(() => (Array.isArray(props.data?.items) ? props.data.items : []))
const recordCount = computed(() => {
  const n = Number(props.data?.count)
  return Number.isFinite(n) ? n : items.value.length
})
const totalAmount = computed(() => {
  const n = Number(props.data?.total_amount)
  return Number.isFinite(n) ? n : 0
})
const title = computed(() => formatMonthLabel(props.year, props.month))

function rowKey(item) {
  return `${item?.code ?? ''}|${item?.ex_date ?? ''}`
}

function amountOf(item) {
  const n = Number(item?.amount)
  return Number.isFinite(n) ? n : 0
}

/** 金额带符号 + 币种：+¥46.50 / −¥3.00（口径统一走 formatSigned + formatAmount） */
function signedAmount(value) {
  return formatSigned(value, (v) => `¥${formatAmount(v)}`)
}

function sourceLabel(source) {
  return SOURCE_CHIP_LABELS[source] ?? source ?? '—'
}

function statusLabel(status) {
  return STATUS_COLORS[status]?.label ?? status ?? '—'
}

function statusColor(status) {
  return STATUS_COLORS[status] ?? STATUS_COLORS.predicted
}

/** 来源类型 chip：底色直接用 D10 的固定来源色，白字保证对比度 */
function sourceChipStyle(source) {
  return {
    background: SOURCE_COLORS[source] ?? STATUS_COLORS.predicted.background,
    color: '#ffffff',
    borderColor: 'transparent',
  }
}

function chipStyle(palette) {
  return {
    background: palette.background,
    color: palette.text,
    borderColor: palette.border ?? 'transparent',
  }
}

/**
 * predicted / announced 才需要「点击确认」（paid 已到账、manual 是补录）。
 * 「去年同期预估」项（is_estimated）**不给按钮**：它不是一笔真实记录，后端确认会 404。
 */
function needsConfirm(item) {
  if (item?.is_estimated) return false
  return item?.status === 'predicted' || item?.status === 'announced'
}

function toggleManual() {
  emit('update:manualFormOpen', !props.manualFormOpen)
}
</script>

<style scoped>
.sheet-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  z-index: 1000;
}

.sheet {
  width: 100%;
  max-width: 1100px;
  height: 70vh;
  display: flex;
  flex-direction: column;
  background: var(--card-bg, #fff);
  border-radius: 16px 16px 0 0;
  box-shadow: var(--shadow-md, 0 4px 6px -1px rgb(0 0 0 / 0.1));
  overflow: hidden;
}

.sheet-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 20px 8px;
}

.sheet-nav {
  display: flex;
  align-items: center;
  gap: 10px;
}

.sheet-nav-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: var(--radius, 8px);
  background: var(--card-bg, #fff);
  color: var(--text-secondary, #64748b);
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
}

.sheet-nav-btn:hover {
  border-color: var(--primary-color, #2563eb);
  color: var(--primary-color, #2563eb);
}

.sheet-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
  font-variant-numeric: tabular-nums;
  min-width: 8em;
  text-align: center;
}

.sheet-sub {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 20px 12px;
  font-size: 13px;
  color: var(--text-secondary, #64748b);
  font-variant-numeric: tabular-nums;
}

.sheet-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 20px 20px;
  border-top: 1px solid var(--border-color, #e2e8f0);
}

.sheet-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.sheet-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 4px;
  border-bottom: 1px solid var(--border-color, #f1f5f9);
}

.sheet-row:last-child {
  border-bottom: none;
}

.row-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.row-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary, #1e293b);
  max-width: 16em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-date {
  font-size: 13px;
  color: var(--text-secondary, #64748b);
  font-variant-numeric: tabular-nums;
}

.row-side {
  display: flex;
  align-items: center;
  gap: 10px;
}

.row-amount {
  font-size: 14px;
  font-weight: 600;
  color: var(--success-color);
  font-variant-numeric: tabular-nums;
  min-width: 7em;
  text-align: right;
}

.row-amount.is-negative {
  color: var(--danger-color, #ef4444);
}

.row-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}

.row-chip-muted {
  font-weight: 600;
}

.sheet-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 24px;
  color: var(--danger-color, #ef4444);
}

.sheet-empty {
  padding: 40px 20px;
}

@media (max-width: 640px) {
  .sheet {
    max-width: 100%;
  }

  .row-main,
  .row-side {
    width: 100%;
    justify-content: space-between;
  }

  .row-amount {
    min-width: 0;
  }
}
</style>
