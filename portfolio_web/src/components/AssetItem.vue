<template>
  <tr class="asset-row" :class="{ expanded: isExpanded }">
    <td @click="toggleExpand" class="expandable-cell">
      <span class="expand-icon">{{ isExpanded ? '▼' : '▶' }}</span>
      {{ asset.name }}
      <span
        v-if="asset.asset_type && asset.asset_type !== '股票'"
        class="asset-type-badge"
        :class="asset.asset_type === 'ETF' ? 'type-etf' : 'type-other'"
      >{{ asset.asset_type }}</span>
    </td>
    <td @click="toggleExpand" class="expandable-cell">{{ asset.code }}</td>
    <td class="text-right">{{ formatNumber(asset.quantity) }}</td>
    <td class="text-right">
      ¥{{ formatPrice(asset.price) }}
      <span
        v-if="sourceBadge"
        class="source-badge"
        :class="['source-' + sourceBadge.kind]"
        :title="sourceBadge.hint"
      >
        {{ sourceBadge.label }}
      </span>
      <button
        class="btn-refresh btn-sm"
        @click.stop="$emit('refresh', asset)"
        :disabled="refreshing"
        title="获取最新价格"
      >
        {{ refreshing ? '...' : '↻' }}
      </button>
    </td>
    <td class="text-right">
      <template v-if="asset.has_cost">¥{{ formatPrice(asset.cost_price) }}</template>
      <span v-else class="muted" title="未填成本，编辑资产可补充">—</span>
    </td>
    <td class="text-right">¥{{ formatNumber(asset.holding_value) }}</td>
    <td class="text-right">
      <template v-if="asset.has_cost">
        <div class="profit-cell" :class="profitClass">
          {{ asset.profit_amount >= 0 ? '+' : '' }}¥{{ formatNumber(Math.abs(asset.profit_amount)) }}
        </div>
        <!-- 成本 ≤ 0 时后端返回 percent=null（负成本的比例无意义），只显示金额 -->
        <div v-if="asset.profit_percent !== null && asset.profit_percent !== undefined"
             class="profit-pct" :class="profitClass">
          {{ asset.profit_percent >= 0 ? '+' : '' }}{{ asset.profit_percent.toFixed(2) }}%
        </div>
        <div v-else class="profit-pct muted" title="成本 ≤ 0，盈亏比例无意义">—</div>
      </template>
      <span v-else class="muted" title="未填成本，无法计算盈亏">—</span>
    </td>
    <td class="text-right">¥{{ (asset.y2025_dividend || 0).toFixed(2) }}</td>
    <td class="text-right">¥{{ (asset.y2024_dividend || 0).toFixed(2) }}</td>
    <td class="text-right highlight-yield">{{ (asset.ttm_yield || 0).toFixed(2) }}%</td>
    <td class="actions" @click.stop>
      <button class="btn btn-secondary btn-sm" @click="$emit('edit', asset)">编辑</button>
      <button class="btn btn-danger btn-sm" @click="$emit('delete', asset.id)">删除</button>
    </td>
  </tr>
  
  <!-- 展开的分红历史区域 -->
  <tr v-if="isExpanded" class="dividend-detail-row">
    <td colspan="10">
      <div class="dividend-detail">
        <div class="dividend-header">
          <h4>📊 {{ asset.name }} ({{ asset.code }}) 历年分红记录</h4>
          <button class="btn btn-sm btn-secondary" @click="loadDividendHistory" :disabled="loadingDividend">
            {{ loadingDividend ? '加载中...' : '🔄 刷新' }}
          </button>
        </div>
        
        <div v-if="loadingDividend" class="loading-small">加载分红数据中...</div>
        
        <div v-else-if="dividendHistory.length > 0" class="dividend-table-container">
          <table class="dividend-table">
            <thead>
              <tr>
                <th>年份</th>
                <th>除权除息日</th>
                <th>每股分红(元)</th>
                <th>每股红股</th>
                <th>每股转增</th>
                <th>状态</th>
                <th>持股合计分红</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in dividendHistory" :key="item.year + item.ex_dividend_date">
                <td>{{ item.year }}年</td>
                <td>{{ item.ex_dividend_date || '-' }}</td>
                <td class="text-right">¥{{ (item.dividend_per_share || 0).toFixed(2) }}</td>
                <td class="text-right">{{ (item.bonus_shares || 0) }}</td>
                <td class="text-right">{{ (item.transfer_shares || 0) }}</td>
                <td>
                  <span :class="['status-badge', item.status === '已实施' ? 'status-done' : 'status-pending']">
                    {{ item.status }}
                  </span>
                </td>
                <td class="text-right highlight-dividend">
                  ¥{{ formatNumber(asset.quantity * (item.dividend_per_share || 0)) }}
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="6" class="text-right"><strong>累计分红合计：</strong></td>
                <td class="text-right highlight-dividend">
                  <strong>¥{{ formatNumber(totalHistoricalDividend) }}</strong>
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
        
        <div v-else class="empty-dividend">
          <p>暂无分红数据</p>
        </div>
        
        <div class="dividend-summary">
          <span>年均每股分红：<strong>¥{{ averageDividend }}/股</strong></span>
          <span>累计历史分红：<strong>¥{{ formatNumber(totalHistoricalDividend) }}</strong></span>
          <span>按持股计算累计：<strong>¥{{ formatNumber(asset.quantity * averageDividend) }}</strong></span>
        </div>
      </div>
    </td>
  </tr>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { getDividendHistory } from '../api/stock.js'

const props = defineProps({
  asset: {
    type: Object,
    required: true
  },
  refreshing: {
    type: Boolean,
    default: false
  }
})

defineEmits(['edit', 'delete', 'refresh'])

const isExpanded = ref(false)
const dividendHistory = ref([])
const loadingDividend = ref(false)

// 价格来源 badge
const sourceBadge = computed(() => {
  const src = props.asset.price_source || 'stale'
  // 优先用后端给的 label/hint；如果后端没给，前端兜底
  const labels = { real: '实时', mock: '模拟', stale: '未刷新' }
  const hints = {
    real: '腾讯行情接口返回的真实价',
    mock: '网络拉取失败，使用 ±3% 随机波动价，不可用于决策',
    stale: '该价格尚未从行情接口刷新过'
  }
  if (src === 'real' && props.asset.price_fetched_at) {
    const t = new Date(props.asset.price_fetched_at)
    const pad = n => String(n).padStart(2, '0')
    const local = `${t.getFullYear()}-${pad(t.getMonth()+1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`
    return { kind: 'real', label: '实时', hint: `腾讯行情接口返回的真实价（${local}）` }
  }
  return { kind: src, label: labels[src] || src, hint: hints[src] || src }
})

function formatPrice(p) {
  return (p == null || Number.isNaN(p)) ? '—' : Number(p).toFixed(2)
}

/**
 * 盈亏配色：A 股惯例是「红涨绿跌」，与欧美相反。
 * 盈利 → 红，亏损 → 绿。不要"顺手改成绿色盈利"。
 */
const profitClass = computed(() => {
  const v = Number(props.asset.profit_amount) || 0
  if (v > 0) return 'profit-up'
  if (v < 0) return 'profit-down'
  return 'profit-flat'
})

// 计算累计分红
const totalHistoricalDividend = computed(() => {
  return dividendHistory.value.reduce((sum, item) => {
    return sum + (item.dividend_per_share || 0) * props.asset.quantity
  }, 0)
})

// 计算年均每股分红
const averageDividend = computed(() => {
  if (dividendHistory.value.length === 0) return 0
  const total = dividendHistory.value.reduce((sum, item) => sum + (item.dividend_per_share || 0), 0)
  return (total / dividendHistory.value.length).toFixed(2)
})

function toggleExpand() {
  isExpanded.value = !isExpanded.value
  
  // 如果展开且还没有加载过分红数据，则加载
  if (isExpanded.value && dividendHistory.value.length === 0 && !loadingDividend.value) {
    loadDividendHistory()
  }
}

async function loadDividendHistory() {
  loadingDividend.value = true
  
  try {
    const history = await getDividendHistory(props.asset.code, props.asset.name)
    dividendHistory.value = history
  } catch (e) {
    console.error('加载分红历史失败:', e)
    dividendHistory.value = []
  } finally {
    loadingDividend.value = false
  }
}

function formatNumber(num) {
  if (num >= 10000) {
    return (num / 10000).toFixed(2) + '万'
  }
  return num.toLocaleString()
}
</script>

<style scoped>
.asset-row {
  cursor: pointer;
}

.asset-row:hover {
  background-color: #f5f5f5;
}

.asset-row.expanded {
  background-color: #e8f4ff;
}

.expandable-cell {
  cursor: pointer;
}

.expand-icon {
  display: inline-block;
  width: 16px;
  font-size: 10px;
  color: #999;
  margin-right: 4px;
}

/* 资产类型徽标（ETF 等非个股） */
.asset-type-badge {
  display: inline-block;
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 10px;
  line-height: 1.4;
  vertical-align: middle;
  font-weight: 600;
  white-space: nowrap;
}

.type-etf {
  background: #fff1e6;
  color: #c2410c;
  border: 1px solid #fed7aa;
}

.type-other {
  background: #eef2f8;
  color: #475569;
  border: 1px solid #cbd5e1;
}

/* 盈亏：A 股惯例「红涨绿跌」（与欧美相反） */
.profit-cell {
  font-weight: 600;
  white-space: nowrap;
}

.profit-pct {
  font-size: 11px;
  opacity: 0.85;
  white-space: nowrap;
}

.profit-up { color: #e11d48; }    /* 盈利 → 红 */
.profit-down { color: #16a34a; }  /* 亏损 → 绿 */
.profit-flat { color: #94a3b8; }  /* 持平 → 灰 */

.muted {
  color: #cbd5e1;
  cursor: help;
}

/* 价格来源 badge：实时 / 模拟 / 未刷新 */
.source-badge {
  display: inline-block;
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 10px;
  line-height: 1.4;
  vertical-align: middle;
  cursor: help;
  font-weight: 500;
  white-space: nowrap;
}

.source-real {
  background: #e6f7ed;
  color: #389e0d;
  border: 1px solid #b7eb8f;
}

.source-mock {
  background: #fff7e6;
  color: #d46b08;
  border: 1px solid #ffd591;
}

.source-stale {
  background: #f5f5f5;
  color: #8c8c8c;
  border: 1px solid #d9d9d9;
}

.btn-refresh {
  background: none;
  border: 1px solid #1890ff;
  color: #1890ff;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  margin-left: 4px;
}

.btn-refresh:hover:not(:disabled) {
  background: #1890ff;
  color: white;
}

.btn-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 分红详情行 */
.dividend-detail-row {
  background-color: #fafafa;
}

.dividend-detail-row td {
  padding: 16px !important;
  vertical-align: top;
}

.dividend-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.dividend-header h4 {
  margin: 0;
  font-size: 14px;
  color: #333;
}

.loading-small {
  padding: 12px;
  color: #666;
  font-size: 13px;
}

.dividend-table-container {
  overflow-x: auto;
  margin-bottom: 12px;
}

.dividend-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.dividend-table th,
.dividend-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid #eee;
}

.dividend-table th {
  background-color: #f0f0f0;
  font-weight: 600;
  color: #333;
}

.dividend-table tbody tr:hover {
  background-color: #f5f5f5;
}

.text-right {
  text-align: right;
}

.highlight-dividend {
  color: #e6a23c;
  font-weight: 600;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
}

.status-done {
  background-color: #e6f7ed;
  color: #52c41a;
}

.status-pending {
  background-color: #fff7e6;
  color: #faad14;
}

.dividend-table tfoot {
  background-color: #fafafa;
}

.dividend-table tfoot td {
  border-top: 2px solid #ddd;
}

.empty-dividend {
  padding: 20px;
  text-align: center;
  color: #999;
}

.dividend-summary {
  display: flex;
  gap: 24px;
  padding: 12px;
  background-color: #f5f5f5;
  border-radius: 4px;
  font-size: 13px;
  color: #666;
}

.dividend-summary strong {
  color: #333;
}
</style>
