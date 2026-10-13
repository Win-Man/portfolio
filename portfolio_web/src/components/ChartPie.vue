<template>
  <div class="chart-card">
    <div class="chart-header">
      <div class="chart-title">{{ title }}</div>
      <div v-if="total > 0" class="chart-total">
        <span class="chart-total-label">{{ totalLabel }}</span>
        <span class="chart-total-value">{{ unit }}{{ formatAmount(total) }}</span>
      </div>
    </div>

    <div class="chart-body">
      <!-- 用 v-show 而不是 v-if：容器需始终存在，ECharts 才能测量尺寸 -->
      <div v-show="slices.length > 0" ref="el" class="chart-canvas"></div>

      <div v-if="slices.length === 0" class="chart-empty">
        <span class="empty-icon">📊</span>
        <p>{{ emptyText }}</p>
      </div>
    </div>

    <!--
      TOP5 图例（纯 DOM，不用 ECharts 的 LegendComponent）：
      饼图本身仍展示完整分布，图例只列占比最大的 5 项，用于「认色」。
      颜色取自饼图中的**原始下标**，保证与色块一致。
    -->
    <div v-if="legendItems.length > 0" class="chart-legend">
      <span class="legend-caption">TOP 5</span>
      <ul class="legend-list">
        <li v-for="item in legendItems" :key="item.name" class="legend-item">
          <span class="legend-dot" :style="{ background: item.color }"></span>
          <span class="legend-name" :title="item.name">{{ item.name }}</span>
          <span class="legend-percent">{{ item.percent.toFixed(1) }}%</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// 按需引入，避免打包整个 echarts（无图例，故不引入 LegendComponent）
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import {
  buildSlices,
  buildTooltipHtml,
  topSlices,
} from '../utils/chartData.js'
import { formatAmount } from '../utils/format.js'
import { MISC_COLOR, pickColor } from '../utils/chartColors.js'

echarts.use([PieChart, TooltipComponent, CanvasRenderer])

const props = defineProps({
  title: { type: String, required: true },
  // [{ name, value }]
  items: { type: Array, default: () => [] },
  unit: { type: String, default: '¥' },
  totalLabel: { type: String, default: '合计' },
  emptyText: { type: String, default: '暂无数据' },
  /** 与另一张饼图共享的「名称 → 颜色」映射，保证同一标的跨图同色 */
  colorMap: { type: Object, default: () => ({}) },
  maxSlices: { type: Number, default: 8 },
  minShare: { type: Number, default: 2 },
})

const el = ref(null)
let chart = null
let resizeObserver = null

const slices = computed(() =>
  buildSlices(props.items, { maxSlices: props.maxSlices, minShare: props.minShare })
)
const total = computed(() => slices.value.reduce((s, d) => s + d.value, 0))

function colorOf(d, i) {
  return d.isMisc ? MISC_COLOR : pickColor(d.name, i, props.colorMap)
}

/**
 * TOP5 图例项。
 *
 * 关键：先按**饼图中的原始下标**取色，再排序取前 5。
 * 若先排序再取色，图例色点会与饼图色块对不上（pickColor 依赖下标兜底）。
 */
const legendItems = computed(() =>
  topSlices(slices.value, 5).map((d) => ({
    name: d.name,
    value: d.value,
    percent: Number.isFinite(d.percent) ? d.percent : 0,
    // d.index 是原始下标，据此取色才能与饼图色块一致
    color: colorOf(d, d.index),
    isMisc: !!d.isMisc,
  }))
)

function buildOption() {
  const data = slices.value
  const percentOf = Object.fromEntries(data.map((d) => [d.name, d.percent]))

  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      padding: [8, 12],
      extraCssText: 'border-radius:6px; box-shadow:0 4px 12px rgba(0,0,0,.15);',
      textStyle: { color: '#f8fafc', fontSize: 12 },
      formatter: (p) =>
        buildTooltipHtml({
          name: p.name,
          value: p.value,
          percent: percentOf[p.name] ?? p.percent,
          unit: props.unit,
        }),
    },
    series: [
      {
        type: 'pie',
        // 实心饼图：无图例占位，居中并尽量放大
        radius: '78%',
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        minAngle: 2,
        itemStyle: {
          borderColor: '#fff',
          borderWidth: 2,
        },
        label: { show: false },
        labelLine: { show: false },
        emphasis: {
          scale: true,
          scaleSize: 8,
          itemStyle: { shadowBlur: 12, shadowColor: 'rgba(15,23,42,0.25)' },
        },
        data: data.map((d, i) => ({
          name: d.name,
          value: Number(d.value.toFixed(2)),
          itemStyle: { color: colorOf(d, i) },
        })),
      },
    ],
  }
}

function render() {
  if (!el.value || slices.value.length === 0) return
  if (!chart) {
    chart = echarts.init(el.value, null, { renderer: 'canvas' })
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
})

watch(slices, () => nextTick(render), { deep: true })
// 共享配色变化（切换组合）时也要重绘
watch(() => props.colorMap, () => nextTick(render), { deep: true })

onBeforeUnmount(() => {
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
}

.chart-body {
  /* 注意：不要用 flex:1 —— 它等价于 flex-basis:0%，
     会覆盖 height 并与「卡片高度由内容决定」形成循环，导致高度塌成 0 */
  position: relative;
  height: 300px;
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

/* ---------- TOP5 图例 ---------- */
.chart-legend {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color, #e2e8f0);
}

.legend-caption {
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary, #94a3b8);
  letter-spacing: 0.04em;
  line-height: 18px;
  /* 与第一行图例文字基线对齐 */
  padding-top: 1px;
}

.legend-list {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 2px 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  max-width: 100%;
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

/* 名称可能很长（如「央企红利ETF华泰柏瑞」）：截断 + title 悬浮看全名 */
.legend-name {
  min-width: 0;
  max-width: 10.5em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.legend-percent {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary, #64748b);
}
</style>
