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
    
    <div v-else class="table-container" :class="{ resizing: !!resizingKey }">
      <!--
        用 colgroup + table-layout: fixed 控制列宽。
        auto 布局下浏览器按内容分配宽度，「名称」列会被长名字撑开并换行；
        fixed 布局才能让列宽真正可控（可拖动调整）并让长文本按列宽截断。
      -->
      <table class="data-table" :style="{ width: totalWidth + 'px', minWidth: '100%' }">
        <colgroup>
          <col v-for="col in columns" :key="col.key" :style="{ width: col.width + 'px' }" />
        </colgroup>
        <thead>
          <tr>
            <th
              v-for="col in columns"
              :key="col.key"
              :class="[
                col.align === 'right' ? 'text-right' : '',
                col.align === 'center' ? 'text-center' : '',
                col.sortable === false ? 'no-sort' : ''
              ]"
              @click="col.sortable === false ? null : sortBy(col.key)"
            >
              <span class="th-inner">
                <span class="th-label">{{ col.label }}</span>
                <span v-if="col.sortable !== false" class="sort-icon">{{ getSortIcon(col.key) }}</span>
              </span>

              <!-- 拖动改列宽 -->
              <span
                v-if="col.resizable !== false"
                class="col-resizer"
                :class="{ active: resizingKey === col.key }"
                title="拖动调整列宽（双击复位）"
                @mousedown.stop.prevent="startResize($event, col)"
                @dblclick.stop.prevent="resetColumn(col)"
                @click.stop
              ></span>
            </th>
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
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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

// ---------- 列定义 ----------
// width 为初始宽度；minWidth 是拖动下限（低于它会截断数字，故留足余量）
//
// 宽度按「表头文字 + 排序图标 + 左右各 12px padding」估足，两处约束取大者：
//   a) 表头不折行、排序图标不掉到第二行
//   b) 单元格内容（如「¥51.99 实时 ↻」）不被裁掉
// 默认总宽约 1296px，配合 .table-container（约 1312px）可完整放下而不出横向滚动条。
const DEFAULT_COLUMNS = [
  { key: 'name',           label: '名称',         align: 'left',   width: 136, minWidth: 90 },
  { key: 'code',           label: '代码',         align: 'left',   width: 78,  minWidth: 70 },
  { key: 'quantity',       label: '持股数量',     align: 'right',  width: 98,  minWidth: 94 },
  { key: 'price',          label: '最新价',       align: 'right',  width: 148, minWidth: 142 },
  { key: 'cost_price',     label: '成本',         align: 'right',  width: 96,  minWidth: 86 },
  { key: 'holding_value',  label: '持仓市值',     align: 'right',  width: 110, minWidth: 100 },
  { key: 'profit_amount',  label: '盈亏',         align: 'right',  width: 112, minWidth: 100 },
  { key: 'y2025_dividend', label: '25年每股分红', align: 'right',  width: 130, minWidth: 124 },
  { key: 'y2024_dividend', label: '24年每股分红', align: 'right',  width: 130, minWidth: 124 },
  { key: 'ttm_yield',      label: 'TTM收益率',    align: 'right',  width: 116, minWidth: 110 },
  { key: 'actions',        label: '操作',         align: 'center', width: 142, minWidth: 136, sortable: false, resizable: false },
]

const STORAGE_KEY = 'portfolio.assetList.colWidths.v1'

const columns = ref(DEFAULT_COLUMNS.map((c) => ({ ...c })))

const totalWidth = computed(() =>
  columns.value.reduce((sum, c) => sum + c.width, 0)
)

// ---------- 列宽持久化 ----------
onMounted(() => {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    for (const col of columns.value) {
      const w = Number(saved[col.key])
      if (Number.isFinite(w) && w >= col.minWidth) col.width = Math.round(w)
    }
  } catch {
    /* 存储不可用或数据损坏：使用默认宽度即可 */
  }
})

function persistWidths() {
  try {
    const out = {}
    for (const col of columns.value) out[col.key] = col.width
    localStorage.setItem(STORAGE_KEY, JSON.stringify(out))
  } catch {
    /* 隐私模式等场景下忽略 */
  }
}

// ---------- 拖动调整列宽 ----------
const resizingKey = ref(null)
let dragStartX = 0
let dragStartWidth = 0

function startResize(e, col) {
  resizingKey.value = col.key
  dragStartX = e.clientX
  dragStartWidth = col.width
  // 监听挂在 window 上：鼠标拖出表格区域也不断
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', endResize)
  document.body.classList.add('col-resizing')
}

function onResizeMove(e) {
  const col = columns.value.find((c) => c.key === resizingKey.value)
  if (!col) return
  const next = Math.round(dragStartWidth + (e.clientX - dragStartX))
  col.width = Math.max(col.minWidth, next)
}

function endResize() {
  if (resizingKey.value) persistWidths()
  resizingKey.value = null
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', endResize)
  document.body.classList.remove('col-resizing')
}

/** 双击列边界恢复该列默认宽度 */
function resetColumn(col) {
  const def = DEFAULT_COLUMNS.find((c) => c.key === col.key)
  if (def) {
    col.width = def.width
    persistWidths()
  }
}

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', endResize)
  document.body.classList.remove('col-resizing')
})

// ---------- 排序 ----------
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

/* 拖动中：整块区域显示列宽光标并禁止选中文本 */
.table-container.resizing {
  cursor: col-resize;
  user-select: none;
}

.table-container.resizing :deep(td),
.table-container.resizing :deep(th) {
  user-select: none;
}

.text-right {
  text-align: right;
}

.text-center {
  text-align: center;
}

/* 表头：允许换行（列窄时标题折成两行，比截断可读），并作为拖动条的定位容器 */
.data-table :deep(th) {
  position: relative;
  white-space: normal;
  line-height: 1.25;
}

/*
  ⚠ table-layout 必须是 fixed。
  auto 布局下浏览器按内容分配列宽，colgroup 的 width 只是"建议"，
  拖动改的宽度会被内容需求覆盖（实测 180px 被撑成 248px，拖动不跟手）。
  fixed 布局才让列宽真正可控，长文本也才会按列宽截断。
*/
.data-table {
  table-layout: fixed;
}

.data-table :deep(th.no-sort) {
  cursor: default;
}

.th-label {
  vertical-align: middle;
}

/* 标题与排序图标作为一个整体：用 inline-flex 让图标始终贴着标题，
   而不是在列窄时被挤到标题下方的单独一行。
   inline-flex 盒子本身由 th 的 text-align 决定左右位置 */
.th-inner {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  vertical-align: middle;
  max-width: 100%;
}

/* 标题不换行：折成两行会让排序图标在垂直居中后"浮"到两行之间。
   列被拖得过窄时用省略号截断，而不是换行。 */
.th-label {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 图标不参与收缩，且抵消 main.css 里的 margin-left（这里用 gap 控制间距） */
.th-inner .sort-icon {
  flex: 0 0 auto;
  margin-left: 0;
}

/* 拖动条：贴在列右边界，悬停时高亮 */
.col-resizer {
  position: absolute;
  top: 0;
  right: -4px;
  width: 9px;
  height: 100%;
  cursor: col-resize;
  z-index: 2;
  user-select: none;
}

.col-resizer::after {
  content: '';
  position: absolute;
  top: 20%;
  bottom: 20%;
  left: 4px;
  width: 1px;
  background: transparent;
  transition: background 0.15s;
}

.col-resizer:hover::after,
.col-resizer.active::after {
  background: var(--primary-color, #2563eb);
}
</style>

<style>
/* 拖动列宽期间全局生效：避免鼠标移出表格后光标/选中状态回退 */
body.col-resizing,
body.col-resizing * {
  cursor: col-resize !important;
  user-select: none !important;
}
</style>
