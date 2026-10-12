<template>
  <div class="chart-card">
    <div class="chart-header">
      <div class="chart-title">{{ title }}</div>
      <div class="chart-meta" v-if="itemCount > 0">{{ itemCount }} 项标的</div>
    </div>

    <div class="chart-body">
      <!-- 用 v-show 而不是 v-if：容器需始终存在，ECharts 才能测量尺寸 -->
      <div v-show="slices.length > 0" class="pie-wrap">
        <div ref="el" class="chart-canvas"></div>
        <div v-show="slices.length > 0" class="pie-center">
          <div class="pie-center-value">{{ unit }}{{ formatCompact(total) }}</div>
          <div class="pie-center-label">{{ totalLabel }}</div>
        </div>
      </div>

      <div v-if="slices.length === 0" class="chart-empty">
        <span class="empty-icon">📊</span>
        <p>{{ emptyText }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// 按需引入，避免打包整个 echarts
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { LabelLayout } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'

import { buildSlices, formatCompact, formatExact, truncate } from '../utils/chartData.js'
import { MISC_COLOR, pickColor } from '../utils/chartColors.js'

echarts.use([PieChart, LegendComponent, TooltipComponent, LabelLayout, CanvasRenderer])

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
const itemCount = computed(() => slices.value.length)

function colorOf(d, i) {
  return d.isMisc ? MISC_COLOR : pickColor(d.name, i, props.colorMap)
}

function buildOption() {
  const data = slices.value
  const percentOf = Object.fromEntries(data.map((d) => [d.name, d.percent]))

  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      padding: [8, 12],
      textStyle: { color: '#f8fafc', fontSize: 12 },
      formatter: (p) => {
        const pct = percentOf[p.name] ?? p.percent
        return [
          `<div style="font-weight:600;margin-bottom:4px">${p.name}</div>`,
          `<div>${props.unit}${formatExact(p.value)}</div>`,
          `<div style="opacity:.75">占比 ${pct.toFixed(2)}%</div>`,
        ].join('')
      },
    },
    legend: {
      type: 'scroll',
      orient: 'vertical',
      right: 4,
      top: 'middle',
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 8,
      icon: 'circle',
      pageIconColor: '#2563eb',
      pageIconInactiveColor: '#cbd5e1',
      pageTextStyle: { color: '#64748b', fontSize: 11 },
      textStyle: { fontSize: 12, color: '#475569' },
      formatter: (name) => {
        const pct = percentOf[name]
        const label = truncate(name, 8)
        return pct == null ? label : `${label}  ${pct.toFixed(1)}%`
      },
    },
    series: [
      {
        type: 'pie',
        // 环形：中间留白用于展示合计
        radius: ['46%', '68%'],
        center: ['34%', '50%'],
        avoidLabelOverlap: true,
        minAngle: 2,
        itemStyle: {
          borderColor: '#fff',
          borderWidth: 2,
          borderRadius: 4,
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
  // 监听容器尺寸变化（窗口缩放、侧栏展开、grid 断点切换）
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
  padding: 16px 18px 8px;
  display: flex;
  flex-direction: column;
}

.chart-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 4px;
}

.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}

.chart-meta {
  font-size: 12px;
  color: var(--text-secondary, #64748b);
}

.chart-body {
  flex: 1;
  min-height: 280px;
  position: relative;
}

.pie-wrap {
  position: relative;
  width: 100%;
  height: 280px;
}

.chart-canvas {
  width: 100%;
  height: 100%;
}

/* 环形中心：left 与 series.center[0] 保持一致 */
.pie-center {
  position: absolute;
  left: 34%;
  top: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  pointer-events: none;
  line-height: 1.3;
}

.pie-center-value {
  font-size: 17px;
  font-weight: 700;
  color: var(--text-primary, #1e293b);
  white-space: nowrap;
}

.pie-center-label {
  font-size: 11px;
  color: var(--text-secondary, #64748b);
  white-space: nowrap;
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
</style>
