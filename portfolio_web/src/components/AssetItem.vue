<template>
  <tr class="asset-row" :class="{ expanded: isExpanded }">
    <!--
      单元格按 `columns`（可见列，且已按用户排定的顺序）动态渲染。
      ⚠ 必须用 v-for 而不是写死顺序的 <td v-if>：
      否则列隐藏/重排后，表头（AssetList 按 visibleColumns 渲染）与
      本行单元格会错位——表头少了一列，单元格却没少。
    -->
    <td
      v-for="col in columns"
      :key="col.key"
      :class="cellClass(col)"
      v-bind="cellAttrs(col)"
    >
      <!-- 名称 -->
      <template v-if="col.key === 'name'">
        <div class="name-inner">
          <span class="expand-icon">{{ isExpanded ? '▼' : '▶' }}</span>
          <span class="name-text">{{ asset.name }}</span>
          <span
            v-if="asset.asset_type && asset.asset_type !== '股票'"
            class="asset-type-badge"
            :class="asset.asset_type === 'ETF' ? 'type-etf' : 'type-other'"
          >{{ asset.asset_type }}</span>
        </div>
      </template>

      <!-- 代码 -->
      <template v-else-if="col.key === 'code'">{{ asset.code }}</template>

      <!-- 持股数量 -->
      <template v-else-if="col.key === 'quantity'">
        {{ formatQuantity(asset.quantity) }}
      </template>

      <!-- 最新价（价格 + 来源徽标 + 刷新按钮，同一行） -->
      <template v-else-if="col.key === 'price'">
        <span class="price-value">¥{{ formatAmount(asset.price) }}</span>
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
      </template>

      <!-- 每股成本 -->
      <template v-else-if="col.key === 'cost_price'">
        <template v-if="asset.has_cost">¥{{ formatCost(asset.cost_price) }}</template>
        <span v-else class="muted" title="未填成本，编辑资产可补充">—</span>
      </template>

      <!-- 持仓市值 -->
      <template v-else-if="col.key === 'holding_value'">
        ¥{{ formatAmount(asset.holding_value) }}
      </template>

      <!-- 盈亏（金额 + 比例） -->
      <template v-else-if="col.key === 'profit_amount'">
        <template v-if="asset.has_cost">
          <div class="profit-cell" :class="profitClass">
            {{ asset.profit_amount >= 0 ? '+' : '' }}¥{{ formatAmount(Math.abs(asset.profit_amount)) }}
          </div>
          <!-- 成本 ≤ 0 时后端返回 percent=null（负成本的比例无意义），只显示金额 -->
          <div
            v-if="asset.profit_percent !== null && asset.profit_percent !== undefined"
            class="profit-pct"
            :class="profitClass"
          >
            {{ asset.profit_percent >= 0 ? '+' : '' }}{{ formatAmount(asset.profit_percent) }}%
          </div>
          <div v-else class="profit-pct muted" title="成本 ≤ 0，盈亏比例无意义">—</div>
        </template>
        <span v-else class="muted" title="未填成本，无法计算盈亏">—</span>
      </template>

      <!-- 历年每股分红（按列 key 里嵌的年份取对应字段） -->
      <template v-else-if="col.key === 'y2026_dividend'">
        ¥{{ formatAmount(asset.y2026_dividend) }}
      </template>
      <template v-else-if="col.key === 'y2025_dividend'">
        ¥{{ formatAmount(asset.y2025_dividend) }}
      </template>
      <template v-else-if="col.key === 'y2024_dividend'">
        ¥{{ formatAmount(asset.y2024_dividend) }}
      </template>

      <!-- TTM 收益率 -->
      <template v-else-if="col.key === 'ttm_yield'">
        {{ formatAmount(asset.ttm_yield) }}%
      </template>

      <!-- 操作 -->
      <template v-else-if="col.key === 'actions'">
        <button class="btn btn-secondary btn-sm" @click="$emit('edit', asset)">编辑</button>
        <button class="btn btn-danger btn-sm" @click="$emit('delete', asset.id)">删除</button>
      </template>
    </td>
  </tr>
  
  <!-- 展开的分红历史区域 -->
  <tr v-if="isExpanded" class="dividend-detail-row">
    <td :colspan="Math.max(columns.length, 1)">
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
                <td class="text-right">¥{{ formatAmount(item.dividend_per_share) }}</td>
                <td class="text-right">{{ formatQuantity(item.bonus_shares) }}</td>
                <td class="text-right">{{ formatQuantity(item.transfer_shares) }}</td>
                <td>
                  <span :class="['status-badge', item.status === '已实施' ? 'status-done' : 'status-pending']">
                    {{ item.status }}
                  </span>
                </td>
                <td class="text-right highlight-dividend">
                  ¥{{ formatAmount(asset.quantity * (item.dividend_per_share || 0)) }}
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="6" class="text-right"><strong>累计分红合计：</strong></td>
                <td class="text-right highlight-dividend">
                  <strong>¥{{ formatAmount(totalHistoricalDividend) }}</strong>
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
        
        <div v-else class="empty-dividend">
          <p>暂无分红数据</p>
        </div>
        
        <div class="dividend-summary">
          <span>年均每股分红：<strong>¥{{ formatAmount(averageDividend) }}/股</strong></span>
          <span>累计历史分红：<strong>¥{{ formatAmount(totalHistoricalDividend) }}</strong></span>
          <span>按持股计算累计：<strong>¥{{ formatAmount(asset.quantity * averageDividend) }}</strong></span>
        </div>
      </div>
    </td>
  </tr>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { getDividendHistory } from '../api/stock.js'
import { formatAmount, formatQuantity, formatCost } from '../utils/format'

const props = defineProps({
  asset: {
    type: Object,
    required: true
  },
  refreshing: {
    type: Boolean,
    default: false
  },
  /**
   * 可见列（已按用户排定的顺序），由 AssetList 传入。
   * 本组件的单元格按它动态渲染，保证与表头逐列对齐。
   */
  columns: {
    type: Array,
    default: () => []
  }
})

defineEmits(['edit', 'delete', 'refresh'])

/**
 * 单元格的 class：数字列右对齐 + 等宽数字；名称列做截断；操作列禁止换行。
 * 与 AssetList 表头的对齐规则保持一致。
 */
const NUMERIC_KEYS = new Set([
  'quantity', 'price', 'cost_price', 'holding_value', 'profit_amount',
  'y2026_dividend', 'y2025_dividend', 'y2024_dividend', 'ttm_yield',
])

function cellClass(col) {
  const classes = []
  if (NUMERIC_KEYS.has(col.key)) {
    classes.push('text-right')
  } else if (col.align === 'center') {
    classes.push('text-center')
  }
  if (col.key === 'name') {
    classes.push('expandable-cell', 'name-cell')
  } else if (col.key === 'code') {
    classes.push('expandable-cell')
  } else if (col.key === 'price') {
    classes.push('price-cell')
  } else if (col.key === 'ttm_yield') {
    classes.push('highlight-yield')
  } else if (col.key === 'actions') {
    classes.push('actions')
  }
  return classes
}

/** 单元格的额外属性：名称列加 title（悬浮看全名）、名称/代码格可点展开、操作格阻断冒泡 */
function cellAttrs(col) {
  if (col.key === 'name') {
    return { title: props.asset.name, onClick: toggleExpand }
  }
  if (col.key === 'code') {
    return { onClick: toggleExpand }
  }
  if (col.key === 'actions') {
    return { onClick: (e) => e.stopPropagation() }
  }
  return {}
}

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

// 计算年均每股分红（返回数值，展示时再统一格式化）
const averageDividend = computed(() => {
  if (dividendHistory.value.length === 0) return 0
  const total = dividendHistory.value.reduce((sum, item) => sum + (item.dividend_per_share || 0), 0)
  return total / dividendHistory.value.length
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
</script>

<style scoped>
.asset-row {
  cursor: pointer;
}

/* fixed 布局下列宽由 colgroup 决定，内容超出需自行裁剪，否则会溢出到相邻单元格 */
.asset-row td {
  overflow: hidden;
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

/* 名称列：固定列宽内单行显示，超出用省略号截断；
   完整名称通过 title 属性在悬浮时显示（长 ETF 名称不再撑高行高） */
.name-cell {
  overflow: hidden;
}

.name-inner {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;           /* 让子元素可以收缩，否则 flex 子项不会截断 */
}

.name-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.asset-type-badge {
  flex: 0 0 auto;
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

/* 数字列统一处理：
   - nowrap：价格单元格里有「价格 + 来源徽标 + 刷新按钮」三样，
     列宽被挤压时徽标/按钮会掉到第二行；nowrap 后由 .table-container 横向滚动承接
   - tabular-nums：等宽数字，配合「固定 2 位小数 + 右对齐」让各列小数点列成一条线 */
.asset-row td.text-right,
.dividend-table td.text-right {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* 最新价：价格、徽标、刷新按钮保持同一行 */
.price-cell {
  white-space: nowrap;
}

.price-value {
  font-variant-numeric: tabular-nums;
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
