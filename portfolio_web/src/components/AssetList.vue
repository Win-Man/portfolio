<template>
  <div class="card">
    <div class="card-header">
      <div class="card-title">资产列表</div>
      <div class="card-actions">
        <button 
          v-if="assets.length > 0" 
          class="btn btn-primary" 
          @click="$emit('refreshAll')"
          :disabled="refreshingAll"
        >
          {{ refreshingAll ? '刷新中...' : '↻ 批量刷新价格' }}
        </button>
        <button class="btn btn-primary" @click="$emit('add')">+ 新增资产</button>
      </div>
    </div>
    
    <div v-if="loading" class="loading">加载中</div>
    
    <div v-else-if="assets.length === 0" class="empty-state">
      <p>暂无资产数据</p>
      <button class="btn btn-primary" @click="$emit('add')">添加第一个资产</button>
    </div>
    
    <div v-else class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th @click="sortBy('name')">
              股票名称 <span class="sort-icon">{{ getSortIcon('name') }}</span>
            </th>
            <th @click="sortBy('code')">
              代码 <span class="sort-icon">{{ getSortIcon('code') }}</span>
            </th>
            <th @click="sortBy('quantity')" class="text-right">
              持股数量 <span class="sort-icon">{{ getSortIcon('quantity') }}</span>
            </th>
            <th @click="sortBy('price')" class="text-right">
              最新价 <span class="sort-icon">{{ getSortIcon('price') }}</span>
            </th>
            <th @click="sortBy('holding_value')" class="text-right">
              持仓市值 <span class="sort-icon">{{ getSortIcon('holding_value') }}</span>
            </th>
            <th @click="sortBy('y2025_dividend')" class="text-right">
              25年每股分红 <span class="sort-icon">{{ getSortIcon('y2025_dividend') }}</span>
            </th>
            <th @click="sortBy('y2024_dividend')" class="text-right">
              24年每股分红 <span class="sort-icon">{{ getSortIcon('y2024_dividend') }}</span>
            </th>
            <th @click="sortBy('ttm_yield')" class="text-right">
              TTM收益率 <span class="sort-icon">{{ getSortIcon('ttm_yield') }}</span>
            </th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <AssetItem 
            v-for="asset in sortedAssets" 
            :key="asset.id" 
            :asset="asset"
            :refreshing="refreshingId === asset.id"
            @edit="$emit('edit', asset)"
            @delete="$emit('delete', asset.id)"
            @refresh="handleRefresh"
          />
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import AssetItem from './AssetItem.vue'

const props = defineProps({
  assets: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['edit', 'delete', 'add', 'refresh', 'refreshAll'])

const sortKey = ref('name')
const sortOrder = ref('asc')
const refreshingId = ref(null)
const refreshingAll = ref(false)

const sortedAssets = computed(() => {
  return [...props.assets].sort((a, b) => {
    let valA = a[sortKey.value]
    let valB = b[sortKey.value]
    
    if (typeof valA === 'string') {
      valA = valA.toLowerCase()
      valB = valB.toLowerCase()
    }
    
    if (sortOrder.value === 'asc') {
      return valA > valB ? 1 : valA < valB ? -1 : 0
    } else {
      return valA < valB ? 1 : valA > valB ? -1 : 0
    }
  })
})

function sortBy(key) {
  if (sortKey.value === key) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortOrder.value = 'asc'
  }
}

function getSortIcon(key) {
  if (sortKey.value !== key) return '↕'
  return sortOrder.value === 'asc' ? '↑' : '↓'
}

function handleRefresh(asset) {
  refreshingId.value = asset.id
  emit('refresh', asset, () => {
    refreshingId.value = null
  })
}
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.card-title {
  font-size: 18px;
  font-weight: 600;
}

.card-actions {
  display: flex;
  gap: 8px;
}
</style>
