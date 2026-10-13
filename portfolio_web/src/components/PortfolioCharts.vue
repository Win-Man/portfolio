<template>
  <div v-if="assets.length > 0" class="charts-grid">
    <ChartPie
      title="持仓市值分布"
      :items="valueItems"
      :color-map="colorMap"
      total-label="资产总额"
      empty-text="暂无持仓市值数据"
    />
    <ChartPie
      title="预计年收入分布"
      :items="incomeItems"
      :color-map="colorMap"
      total-label="预计年收入"
      empty-text="暂无分红数据（可点「批量刷新价格」拉取）"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ChartPie from './ChartPie.vue'
import { aggregateAssets, stableNameOrder } from '../utils/chartData.js'
import { buildColorMap } from '../utils/chartColors.js'

const props = defineProps({
  assets: {
    type: Array,
    default: () => []
  }
})

/**
 * 数据全部来自后端已算好的字段，前端只做聚合：
 *   holding_value          = quantity × price          （schemas.AssetResponse）
 *   expected_annual_income = quantity × dividend_per_share
 */
const valueItems = computed(() => aggregateAssets(props.assets, (a) => a.holding_value))
const incomeItems = computed(() => aggregateAssets(props.assets, (a) => a.expected_annual_income))

/**
 * 两张图共用一份配色，保证同一标的跨图同色 —— 便于对照
 * 「市值占比 vs 收入占比」，一眼看出哪只标的的股息效率更高。
 * 顺序以持仓市值降序为基准（主口径）。
 */
const colorMap = computed(() =>
  buildColorMap(stableNameOrder(props.assets, (a) => a.holding_value))
)
</script>

<style scoped>
.charts-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin: 12px 0 16px;
}

@media (max-width: 900px) {
  .charts-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
