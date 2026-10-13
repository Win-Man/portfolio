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
</style>
