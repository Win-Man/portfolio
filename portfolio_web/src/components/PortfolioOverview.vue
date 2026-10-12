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
import InfoTooltip from './InfoTooltip.vue'

defineProps({
  summary: {
    type: Object,
    default: () => null
  }
})

function formatNumber(num) {
  if (num == null) return '0.00'
  if (num >= 10000) {
    return (num / 10000).toFixed(2) + '万'
  }
  return num.toFixed(2)
}
</script>
