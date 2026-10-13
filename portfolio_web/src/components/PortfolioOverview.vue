<template>
  <div>
    <div class="overview-grid">
      <div class="overview-item">
        <div class="label">总市值</div>
        <div class="value">¥{{ formatNumber(summary?.total_market_value) }}</div>
      </div>
      <div class="overview-item">
        <div class="label">资产数量</div>
        <div class="value">{{ summary?.asset_count || 0 }}</div>
        <div class="sub">项标的</div>
      </div>
      <div class="overview-item" :class="profitClass">
        <div class="label">
          总盈亏
          <InfoTooltip
            v-if="hasCost"
            :text="`已填成本的 ${summary?.costed_asset_count || 0} 项标的，(现价 − 成本) × 持股数 汇总`"
          />
        </div>
        <template v-if="hasCost">
          <div class="value">{{ sign }}¥{{ formatNumber(Math.abs(totalProfit)) }}</div>
          <div class="sub">{{ sign }}{{ (summary?.total_profit_percent || 0).toFixed(2) }}%</div>
        </template>
        <template v-else>
          <div class="value">—</div>
          <div class="sub">暂无成本数据</div>
        </template>
      </div>
      <div class="overview-item highlight">
        <div class="label">
          加权平均 TTM 收益率
          <InfoTooltip text="按持仓市值加权计算的年度股息率" />
        </div>
        <div class="value">{{ (summary?.weighted_ttm_yield || 0).toFixed(2) }}%</div>
      </div>
      <div class="overview-item">
        <div class="label">预计年收入</div>
        <div class="value">¥{{ formatNumber(summary?.expected_annual_income) }}</div>
        <div class="sub">股息收益</div>
      </div>
    </div>
    <div class="overview-desc">
      {{ summary?.description || '加载中...' }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import InfoTooltip from './InfoTooltip.vue'

const props = defineProps({
  summary: {
    type: Object,
    default: () => null
  }
})

const totalProfit = computed(() => Number(props.summary?.total_profit) || 0)
const hasCost = computed(() => (props.summary?.costed_asset_count || 0) > 0)

const sign = computed(() => (totalProfit.value >= 0 ? '+' : '−'))

/**
 * 盈亏配色：A 股惯例「红涨绿跌」，与欧美相反。
 * 盈利 → 红，亏损 → 绿。（整张卡片换底色，白字在彩色卡片上仍清晰）
 */
const profitClass = computed(() => {
  if (!hasCost.value) return 'profit-flat'
  if (totalProfit.value > 0) return 'profit-up'
  if (totalProfit.value < 0) return 'profit-down'
  return 'profit-flat'
})

function formatNumber(num) {
  if (num == null) return '0.00'
  if (Math.abs(num) >= 10000) {
    return (num / 10000).toFixed(2) + '万'
  }
  return num.toFixed(2)
}
</script>
