<template>
  <div class="chart-card">
    <div class="chart-header">
      <div class="chart-title">按月分红（按来源堆叠）</div>
      <div v-if="hasData" class="chart-total">
        <span class="chart-total-label">{{ year }}年已收录合计</span>
        <span class="chart-total-value">¥{{ formatAmount(yearTotal) }}</span>
      </div>
    </div>

    <div class="chart-body">
      <!-- 用 v-show 而不是 v-if：容器需始终存在，ECharts 才能测量尺寸 -->
      <div v-show="hasData" ref="el" class="chart-canvas"></div>
      <div v-if="!hasData" class="chart-empty">
        <span class="empty-icon">📊</span>
        <p>暂无分红数据</p>
      </div>
    </div>

    <!-- 固定图例（纯 DOM）：8 类来源的顺序与配色和堆叠柱一致，末尾一条是年度累计折线 -->
    <div class="chart-legend">
      <ul class="legend-list">
        <li v-for="item in legendItems" :key="item.key" class="legend-item">
          <span class="legend-dot" :style="{ background: item.color }"></span>
          <span class="legend-name">{{ item.label }}</span>
        </li>
        <li class="legend-item">
          <span class="legend-dot legend-dot-line" :style="{ background: CUMULATIVE_LINE_COLOR }"></span>
          <span class="legend-name">{{ year }}年累计</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
/**
 * 月视图：12 根堆叠柱（按来源拆）+ 1 条年度累计折线（spec D10 / §7.4.3）。
 *
 * 封装风格对齐 `ChartPie.vue`：onMounted 初始化、ResizeObserver + window resize、
 * onBeforeUnmount dispose、watch props 深度重绘。
 *
 * 本组件引入 echarts（约 460KB），**只能**被 CalendarView 通过 defineAsyncComponent 异步加载，
 * 否则 echarts 会被打进主包。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// 按需引入，避免打包整个 echarts
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import { cumulativeLine, stackedBarData } from '../utils/calendar.js'
import { formatAmount } from '../utils/format.js'
import {
  CUMULATIVE_LINE_COLOR,
  SOURCE_COLORS,
  SOURCE_LABELS,
  SOURCE_ORDER,
} from '../utils/calendarColors.js'

echarts.use([BarChart, LineChart, TooltipComponent, GridComponent, CanvasRenderer])

const props = defineProps({
  /** 12 个 CalendarMonth（含 by_source / total_amount / count） */
  months: {
    type: Array,
    default: () => [],
  },
  /** 用于 tooltip / 标题的年份 */
  year: {
    type: [Number, String],
    default: new Date().getFullYear(),
  },
})

const emit = defineEmits(['select-month'])

const el = ref(null)
let chart = null
let resizeObserver = null

const months = computed(() => (Array.isArray(props.months) ? props.months : []))
const hasData = computed(() => months.value.length > 0)
const yearTotal = computed(() =>
  months.value.reduce((sum, m) => sum + (Number(m?.total_amount) || 0), 0)
)
const legendItems = computed(() =>
  SOURCE_ORDER.map((key) => ({ key, label: SOURCE_LABELS[key], color: SOURCE_COLORS[key] }))
)

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/** 某月某类来源的金额 */
function sourceAmount(month, key) {
  return num(month?.by_source?.[key])
}

/** `YYYY-MM` */
function monthLabel(month) {
  return `${Number(props.year)}-${String(Number(month)).padStart(2, '0')}`
}

function tooltipFormatter(params) {
  const month = months.value[params.dataIndex]
  if (!month) return ''
  const label = monthLabel(month.month)

  // 折线：只报年度累计
  if (params.seriesType === 'line') {
    return `${label}&nbsp;&nbsp;累计 ¥${formatAmount(params.value)}`
  }

  // 柱：报该月合计 + 笔数 + 各类金额（0 值不占行）
  const lines = SOURCE_ORDER.map((key) => ({ key, value: sourceAmount(month, key) }))
    .filter((x) => x.value > 0)
    .map(
      (x) =>
        `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${SOURCE_COLORS[x.key]};margin-right:6px"></span>${SOURCE_LABELS[x.key]} ¥${formatAmount(x.value)}`
    )
  const head = `${label}&nbsp;&nbsp;共 ¥${formatAmount(month.total_amount)}&nbsp;&nbsp;笔数 ${num(month.count)}`
  if (lines.length === 0) return `${head}<br/>本月无分红`
  return `${head}<br/>${lines.join('<br/>')}`
}

function buildOption() {
  const bars = stackedBarData(months.value).map((series) => ({
    type: 'bar',
    ...series,
    barMaxWidth: 28,
    emphasis: { focus: 'series' },
  }))

  const line = {
    name: '年度累计',
    type: 'line',
    yAxisIndex: 1,
    data: cumulativeLine(months.value),
    smooth: false,
    symbol: 'circle',
    symbolSize: 5,
    z: 5,
    lineStyle: { color: CUMULATIVE_LINE_COLOR, width: 2 },
    itemStyle: { color: CUMULATIVE_LINE_COLOR },
  }

  return {
    grid: { left: 8, right: 8, top: 16, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      padding: [8, 12],
      extraCssText:
        'border-radius:6px; box-shadow:0 4px 12px rgba(0,0,0,.15); font-variant-numeric: tabular-nums;',
      textStyle: { color: '#f8fafc', fontSize: 12 },
      formatter: tooltipFormatter,
    },
    xAxis: {
      type: 'category',
      data: months.value.map((m) => `${num(m?.month)}月`),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisLabel: { color: '#64748b', fontSize: 11 },
    },
    yAxis: [
      {
        type: 'value',
        name: '分红 (元)',
        nameTextStyle: { color: '#94a3b8', fontSize: 11 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
        axisLabel: { color: '#64748b', fontSize: 11, formatter: (v) => formatAmount(v) },
      },
      {
        type: 'value',
        name: '累计 (元)',
        nameTextStyle: { color: '#94a3b8', fontSize: 11 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: CUMULATIVE_LINE_COLOR, fontSize: 11, formatter: (v) => formatAmount(v) },
      },
    ],
    series: [...bars, line],
  }
}

function render() {
  if (!el.value || !hasData.value) return
  if (!chart) {
    chart = echarts.init(el.value, null, { renderer: 'canvas' })
    // 点柱子 → 选月（交互闭环，父组件负责打开月 Sheet）
    chart.on('click', (params) => {
      if (!params || params.seriesType !== 'bar') return
      emit('select-month', params.dataIndex + 1)
    })
  }
  chart.setOption(buildOption(), true)
  chart.resize()
}

onMounted(() => {
  nextTick(render)
  // 监听容器尺寸变化（窗口缩放、断点切换）
  resizeObserver = new ResizeObserver(() => {
    if (chart) chart.resize()
  })
  if (el.value) resizeObserver.observe(el.value)
  window.addEventListener('resize', handleWindowResize)
})

function handleWindowResize() {
  if (chart) chart.resize()
}

watch(months, () => nextTick(render), { deep: true })
watch(() => props.year, () => nextTick(render))

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (chart) {
    chart.dispose()
    chart = null
  }
})
</script>

<style scoped>
.chart-card {
  background: var(--card-bg, #fff);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: var(--radius, 8px);
  box-shadow: var(--shadow, 0 1px 3px rgb(0 0 0 / 0.1));
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}

.chart-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}

.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}

.chart-total {
  display: flex;
  align-items: baseline;
  gap: 6px;
  white-space: nowrap;
}

.chart-total-label {
  font-size: 12px;
  color: var(--text-secondary, #64748b);
}

.chart-total-value {
  font-size: 15px;
  font-weight: 700;
  color: var(--primary-color, #2563eb);
  font-variant-numeric: tabular-nums;
}

.chart-body {
  position: relative;
  height: 360px;
}

.chart-canvas {
  width: 100%;
  height: 100%;
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-secondary, #94a3b8);
  font-size: 13px;
}

.empty-icon {
  font-size: 26px;
  opacity: 0.5;
}

.chart-empty p {
  margin: 0;
}

.chart-legend {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color, #e2e8f0);
}

.legend-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-primary, #1e293b);
}

.legend-dot {
  flex: 0 0 auto;
  width: 9px;
  height: 9px;
  border-radius: 2px;
}

.legend-dot-line {
  width: 14px;
  height: 3px;
  border-radius: 2px;
}

.legend-name {
  white-space: nowrap;
}
</style>
