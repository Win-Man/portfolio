<template>
  <div class="wl-chart">
    <div v-show="hasData" ref="el" class="wl-chart-canvas"></div>
    <div v-if="!hasData" class="wl-chart-empty">
      <span class="wl-chart-empty-icon">📈</span>
      <p>{{ loading ? '加载快照...' : '暂无快照数据' }}</p>
    </div>
  </div>
</template>

<script setup>
/**
 * 展开行里的双轴折线：价格（右轴 ¥）+ TTM 股息率（左轴 %）。
 *
 * - 只做**序列 → 图形**的映射，阈值判定与派生数值全部来自后端（D9）；
 * - 缺日就是缺数据：null 原样交给 ECharts 断线，**不插值、不回填**（D15）；
 * - 组件本身引入 echarts，只能被 WatchlistView 通过 defineAsyncComponent 异步加载，
 *   否则 echarts（约 460KB）会打进主包。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import { buildTrendSeries, alignExDividends, formatChartTip, spanDays } from '../utils/watchlist.js'
import { LINE_COLORS, MARK_COLORS } from '../utils/watchlistColors.js'

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
])

const props = defineProps({
  /** WatchlistSnapshotResponse[]（trade_date / price / ttm_yield ...） */
  snapshots: {
    type: Array,
    default: () => [],
  },
  /** 目标股息率（百分数）：画一条虚线参考线 */
  targetYield: {
    type: [Number, String],
    default: null,
  },
  /**
   * 除权日列表 `[{ ex_date, dividend_per_share }]`（GET /dividends）。
   * 用来画淡色竖线：除权日 TTM 滚动窗口会换入/换出某一期分红，
   * 股息率曲线当天出现台阶 —— 竖线让台阶「有出处」。
   */
  exDividends: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
})

const el = ref(null)
let chart = null
let resizeObserver = null

const series = computed(() => buildTrendSeries(props.snapshots))
const hasData = computed(() => series.value.dates.length > 0)
const targetYieldValue = computed(() => {
  const n = Number(props.targetYield)
  return Number.isFinite(n) && n > 0 ? n : null
})

/** 除权日对齐到当前窗口的横轴（只保留窗口内的，顺延到最近有数据的交易日） */
const exMarks = computed(() => alignExDividends(series.value.dates, props.exDividends))

/** 日期 → 当期每股分红，供 tooltip 提示「今天为什么台阶」 */
const exDividendMap = computed(() => {
  const map = new Map()
  for (const row of exMarks.value) map.set(row.date, row.amount)
  return map
})

/** '2026-10-01' → '10-01'（横轴标签；tooltip 里仍用完整日期） */
function axisLabel(date) {
  const text = String(date ?? '')
  return text.length >= 10 ? text.slice(5) : text
}

/**
 * 横轴标签粒度：跨度超过一年时只写 `MM-DD` 会满屏无年份的刻度
 * （「全部」是十年以上的窗口），此时改用 `YYYY-MM`。
 */
const axisLabelFormatter = computed(() => {
  const longSpan = spanDays(series.value.dates) > 400
  return (value) => {
    const text = String(value ?? '')
    return longSpan ? text.slice(0, 7) : axisLabel(text)
  }
})

/** 统一展示精度：股息率与价格都保留 2 位小数（与轴标签、表格一致）。 */
function tooltipFormatter(params) {
  if (!Array.isArray(params) || params.length === 0) return ''
  const index = params[0].dataIndex
  const date = series.value.dates[index] ?? ''
  const lines = params.map((item) => {
    const text = formatChartTip(item.value)
    let shown = '—'
    if (text !== '—') shown = item.seriesName === '价格' ? `¥${text}` : `${text}%`
    return `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${item.color};margin-right:6px"></span>${item.seriesName} ${shown}`
  })

  // 除权日当天补一行说明：股息率曲线的台阶在这一天有明确成因
  const amount = exDividendMap.value.get(String(date).slice(0, 10))
  if (amount !== undefined) {
    lines.push(
      `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${MARK_COLORS.exDividend};margin-right:6px"></span>除权日 · 每股派 ¥${amount.toFixed(3)}`
    )
  }

  return `${date}<br/>${lines.join('<br/>')}`
}

/** 除权日竖线：条数少时带文字标签，多了只画线（否则标签糊成一片） */
function buildExDividendMarkLine() {
  if (exMarks.value.length === 0) return undefined
  const showLabel = exMarks.value.length <= 3
  return {
    silent: true,
    symbol: 'none',
    animation: false,
    lineStyle: { color: MARK_COLORS.exDividend, type: 'solid', width: 1 },
    label: {
      show: showLabel,
      // 用 data 项的 name（{b}）而不是 params.value：
      // xAxis 型 markLine 的 value 是横轴坐标（日期字符串），拿它 toFixed 会得到 NaN
      formatter: '{b}',
      color: MARK_COLORS.exDividendText,
      fontSize: 11,
      // 竖线默认会把标签转 90° 变成竖排文字，几乎不可读 —— 强制水平
      rotate: 0,
      // 顶端内侧：竖线在绘图区顶部是空白区，不会压住曲线
      position: 'insideEndTop',
    },
    data: exMarks.value.map((row) => ({
      xAxis: row.date,
      name: `除权 ¥${row.amount.toFixed(3)}`,
    })),
  }
}

function buildOption() {
  const { dates, prices, yields } = series.value
  const target = targetYieldValue.value
  // 点数一多（90 / 180 天，上百个交易日）圆点会把折线糊成虚线，改成不画点；
  // 30 天这种稀疏窗口仍保留圆点，方便看出单日数值。
  const showSymbol = dates.length <= 45

  return {
    grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      padding: [8, 12],
      extraCssText:
        'border-radius:6px; box-shadow:0 4px 12px rgba(0,0,0,.15); font-variant-numeric: tabular-nums;',
      textStyle: { color: '#f8fafc', fontSize: 12 },
      formatter: tooltipFormatter,
    },
    legend: {
      top: 0,
      left: 'center',
      itemWidth: 12,
      itemHeight: 3,
      textStyle: { color: '#64748b', fontSize: 11 },
      data: ['股息率', '价格'],
    },
    xAxis: {
      type: 'category',
      // data 用**完整日期**：除权日竖线靠 xAxis 值定位，只留 'MM-DD'
      // 在跨年窗口（180 / 365 天）会出现重复类目，竖线会落到错误的那一年
      data: dates,
      boundaryGap: false,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisLabel: { color: '#64748b', fontSize: 11, formatter: axisLabelFormatter.value },
    },
    yAxis: [
      {
        type: 'value',
        name: '股息率 (%)',
        nameTextStyle: { color: LINE_COLORS.yield, fontSize: 11, align: 'left' },
        scale: true,
        // 左轴必须把目标股息率包进可视区间：否则近期股息率波动很小时
        // （如 2.85%~3.06%），目标线 4.00% 会被裁到轴外、虚线直接消失
        ...(target === null
          ? {}
          : {
              min: ({ min }) => Math.min(Number(min) || 0, target) * 0.98,
              max: ({ max }) => Math.max(Number(max) || 0, target) * 1.02,
            }),
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
        axisLabel: {
          color: LINE_COLORS.yield,
          fontSize: 11,
          formatter: (v) => `${Number(v).toFixed(2)}%`,
        },
      },
      {
        type: 'value',
        name: '价格 (¥)',
        nameTextStyle: { color: LINE_COLORS.price, fontSize: 11, align: 'right' },
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          color: LINE_COLORS.price,
          fontSize: 11,
          formatter: (v) => Number(v).toFixed(2),
        },
      },
    ],
    series: [
      {
        name: '股息率',
        type: 'line',
        yAxisIndex: 0,
        data: yields,
        connectNulls: false,
        smooth: false,
        symbol: 'circle',
        symbolSize: 5,
        showSymbol,
        lineStyle: { color: LINE_COLORS.yield, width: 2 },
        itemStyle: { color: LINE_COLORS.yield },
        markLine:
          target === null
            ? undefined
            : {
                silent: true,
                symbol: 'none',
                lineStyle: { color: LINE_COLORS.targetYield, type: 'dashed', width: 1.5 },
                label: {
                  formatter: `目标 ${target.toFixed(2)}%`,
                  color: LINE_COLORS.targetYield,
                  fontSize: 11,
                  // 放线段中部：左端会压住 y 轴刻度（5.61%），右端压住价格线收尾和右轴刻度
                  position: 'insideMiddleTop',
                },
                data: [{ yAxis: target }],
              },
      },
      {
        name: '价格',
        type: 'line',
        yAxisIndex: 1,
        data: prices,
        connectNulls: false,
        smooth: false,
        symbol: 'circle',
        symbolSize: 5,
        showSymbol,
        lineStyle: { color: LINE_COLORS.price, width: 2 },
        itemStyle: { color: LINE_COLORS.price },
        // 除权日竖线挂在价格线上：它贯穿整个绘图区高度，
        // 与股息率线的水平目标虚线互不干扰（各自独立 markLine，样式不串）
        markLine: buildExDividendMarkLine(),
      },
    ],
  }
}

function render() {
  if (!el.value || !hasData.value) return
  if (!chart) {
    chart = echarts.init(el.value, null, { renderer: 'canvas' })
  }
  chart.setOption(buildOption(), true)
  chart.resize()
}

onMounted(() => {
  nextTick(render)
  resizeObserver = new ResizeObserver(() => {
    if (chart) chart.resize()
  })
  if (el.value) resizeObserver.observe(el.value)
  window.addEventListener('resize', handleWindowResize)
})

function handleWindowResize() {
  if (chart) chart.resize()
}

watch(series, () => nextTick(render), { deep: true })
watch(targetYieldValue, () => nextTick(render))
watch(exMarks, () => nextTick(render), { deep: true })

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
.wl-chart {
  position: relative;
  height: 220px;
}

.wl-chart-canvas {
  width: 100%;
  height: 100%;
}

.wl-chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
}

.wl-chart-empty-icon {
  font-size: 22px;
  opacity: 0.5;
}

.wl-chart-empty p {
  margin: 0;
}
</style>
