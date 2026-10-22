<template>
  <div class="year-grid">
    <div v-for="quarter in quarters" :key="quarter.label" class="quarter-row">
      <div class="quarter-label">{{ quarter.label }}</div>

      <button
        v-for="cell in quarter.cells"
        :key="cell.month"
        type="button"
        class="month-cell"
        :class="{ selected: isSelected(cell.month), pending: isPending(cell) }"
        :style="cellStyle(cell.month)"
        :aria-pressed="isSelected(cell.month)"
        :title="isPending(cell) ? PENDING_MONTH_TITLE : undefined"
        @click="$emit('select-month', cell.month)"
      >
        <span class="month-amount">¥{{ formatAmount(cell.total_amount) }}</span>
        <span class="month-sub">
          <span class="month-dot" :style="{ background: dotColor(cell) }"></span>
          <span class="month-label">{{ cell.month }}月</span>
          <span v-if="estimatedAmount(cell) > 0" class="month-badge month-badge-estimated"
                :title="ESTIMATED_MONTH_TITLE">预估</span>
          <span v-else-if="isPending(cell)" class="month-badge">待收录</span>
        </span>
      </button>
    </div>
  </div>
</template>

<script setup>
/**
 * 年视图：4 行（Q1–Q4）× 3 列的月合计网格（spec D9 / §7.4.2）。
 *
 * 每格：主行 `¥{formatAmount(月合计)}`（tabular-nums），副行 `{月}月` + 状态色小圆点。
 * 点格 → emit('select-month', month)，由父组件决定「切月视图 + 打开月 Sheet」。
 */
import { computed } from 'vue'

import { formatAmount } from '../utils/format.js'
import { isPendingMonth } from '../utils/calendar.js'
import {
  ESTIMATED_MONTH_TITLE,
  PENDING_MONTH_TITLE,
  SELECTED_MONTH_BACKGROUND,
  SELECTED_MONTH_BORDER,
  STATUS_DOT_COLORS,
} from '../utils/calendarColors.js'

const props = defineProps({
  /** 12 个 CalendarMonth（缺项按 0 笔补位，保证网格永远是 12 格） */
  months: {
    type: Array,
    default: () => [],
  },
  /** 当前选中的月份 1..12；null 表示未选 */
  selectedMonth: {
    type: Number,
    default: null,
  },
})

defineEmits(['select-month'])

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/** 12 个槽位（后端 months 缺失/不齐时补 0 笔的占位，避免网格塌陷） */
const cells = computed(() => {
  const list = Array.isArray(props.months) ? props.months : []
  const out = []
  for (let month = 1; month <= 12; month++) {
    const hit = list.find((m) => num(m?.month) === month)
    out.push(
      hit ?? {
        month,
        total_amount: 0,
        count: 0,
        by_status: {},
        by_source: {},
      }
    )
  }
  return out
})

const quarters = computed(() => [
  { label: 'Q1', cells: cells.value.slice(0, 3) },
  { label: 'Q2', cells: cells.value.slice(3, 6) },
  { label: 'Q3', cells: cells.value.slice(6, 9) },
  { label: 'Q4', cells: cells.value.slice(9, 12) },
])

function isSelected(month) {
  return Number(props.selectedMonth) === Number(month)
}

/**
 * 「待收录」= 该月还没到、且 akshare 里没有记录、也没有去年同期预估（D2：不外推未来）。
 * 只有 0 笔且月份严格晚于当前年月才算，避免把往年「确实没分红」的月份误标成待收录。
 */
function isPending(cell) {
  if (!cell || num(cell.count) > 0) return false
  const year = Number(cell.year) || new Date().getFullYear()
  return isPendingMonth(year, cell.month)
}

/** 该月合计里「去年同期预估」占的金额（后端给，恒 >= 0） */
function estimatedAmount(cell) {
  const v = Number(cell?.estimated_amount)
  return Number.isFinite(v) && v > 0 ? v : 0
}

/** 选中态：2px 主色边框 + 浅主色底（颜色只从 calendarColors.js 取） */
function cellStyle(month) {
  if (!isSelected(month)) return null
  return {
    borderColor: SELECTED_MONTH_BORDER,
    background: SELECTED_MONTH_BACKGROUND,
  }
}

/**
 * 状态小圆点（spec §7.4.2，优先级从高到低）：
 *   0 笔 → 灰；含 announced → 黄；含 predicted → 橙；含 manual → 蓝；只剩 paid → 绿
 */
function dotColor(cell) {
  const count = num(cell?.count)
  const status = cell?.by_status ?? {}
  const has = (key) => num(status[key]) > 0
  if (count <= 0 && !has('paid') && !has('announced') && !has('predicted') && !has('manual')) {
    return isPending(cell) ? STATUS_DOT_COLORS.pending : STATUS_DOT_COLORS.none
  }
  if (has('announced')) return STATUS_DOT_COLORS.announced
  if (has('predicted')) return STATUS_DOT_COLORS.predicted
  if (has('manual')) return STATUS_DOT_COLORS.manual
  if (has('paid')) return STATUS_DOT_COLORS.paid
  return STATUS_DOT_COLORS.none
}
</script>

<style scoped>
.year-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.quarter-row {
  display: grid;
  /* 左侧季度标签 + 3 个月格 */
  grid-template-columns: 40px repeat(3, minmax(0, 1fr));
  align-items: stretch;
  gap: 12px;
}

.quarter-label {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
  letter-spacing: 0.04em;
  background: var(--bg-color, #f8fafc);
  border-radius: var(--radius, 8px);
}

.month-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 4px;
  padding: 12px 14px;
  min-width: 0;
  background: var(--card-bg, #fff);
  border: 2px solid transparent;
  /* 未选中时用 inset 阴影画出 1px 灰边，选中时换成 2px 主色实边，避免布局跳动 */
  box-shadow: inset 0 0 0 1px var(--border-color, #e2e8f0);
  border-radius: var(--radius, 8px);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition: box-shadow 0.15s, border-color 0.15s, background 0.15s;
}

.month-cell:hover {
  box-shadow: inset 0 0 0 1px var(--primary-color, #2563eb);
}

.month-cell.selected {
  box-shadow: none;
}

.month-amount {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.month-sub {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary, #64748b);
}

.month-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.month-cell.pending .month-amount {
  color: var(--text-secondary, #64748b);
  font-weight: 500;
}

.month-badge {
  font-size: 11px;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--bg-color, #f8fafc);
  border: 1px dashed var(--border-color, #e2e8f0);
  color: var(--text-secondary, #64748b);
  white-space: nowrap;
}

/* 「预估」用实线边框 + 略深字色，与「待收录」（虚线、纯占位）区分开 */
.month-badge-estimated {
  border-style: solid;
  border-color: #cbd5e1;
  background: #f1f5f9;
  color: #475569;
}

.month-label {
  font-variant-numeric: tabular-nums;
}

@media (max-width: 640px) {
  .quarter-row {
    grid-template-columns: 32px repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .month-cell {
    padding: 10px;
  }

  .month-amount {
    font-size: 13px;
  }
}
</style>
