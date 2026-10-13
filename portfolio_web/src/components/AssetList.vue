<template>
  <div class="card">
    <div class="card-header">
      <div class="card-title">资产列表</div>
      <div class="card-actions">
        <button
          v-if="assets.length > 0"
          class="btn btn-ghost"
          :title="showColumnPicker ? '关闭列设置' : '选择要显示的列'"
          @click="showColumnPicker = !showColumnPicker"
        >
          <span class="btn-icon">⚙</span> 列设置
          <span class="btn-count" v-if="visibleColumnCount !== DEFAULT_COLUMNS.length">
            ({{ visibleColumnCount }}/{{ DEFAULT_COLUMNS.length }})
          </span>
        </button>
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

    <div v-if="showColumnPicker" class="column-picker">
      <div class="column-picker-header">
        <span class="column-picker-title">选择要显示的列</span>
        <div class="column-picker-actions">
          <button class="btn btn-ghost btn-sm" @click="resetColumnVisibility">恢复默认</button>
          <button class="btn btn-ghost btn-sm" @click="hideAllColumns" :disabled="visibleColumnCount === 0">全不显示</button>
          <button class="btn btn-ghost btn-sm" @click="showAllColumns">全选</button>
        </div>
      </div>
      <div class="column-picker-list">
        <label
          v-for="col in DEFAULT_COLUMNS"
          :key="col.key"
          class="column-picker-item"
          :class="{ 'is-disabled': isLockedColumn(col.key) }"
        >
          <input
            type="checkbox"
            :checked="isColumnVisible(col.key)"
            :disabled="isLockedColumn(col.key)"
            @change="toggleColumn(col.key)"
          />
          <span class="column-picker-label">{{ col.label }}</span>
          <span v-if="isLockedColumn(col.key)" class="column-picker-hint">始终显示</span>
        </label>
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
          <col v-for="col in visibleColumns" :key="col.key" :style="{ width: col.width + 'px' }" />
        </colgroup>
        <thead>
          <tr>
            <th
              v-for="col in visibleColumns"
              :key="col.key"
              :data-col-key="col.key"
              :class="[
                col.align === 'right' ? 'text-right' : '',
                col.align === 'center' ? 'text-center' : '',
                col.sortable === false ? 'no-sort' : '',
                draggingKey === col.key ? 'is-dragging' : '',
                dropTargetKey === col.key
                  ? (dropAfter ? 'drop-after' : 'drop-before')
                  : ''
              ]"
              :title="'拖动表头可调整列顺序'"
              @mousedown="onHeaderMouseDown($event, col)"
              @click="onHeaderClick(col)"
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
            :columns="visibleColumns"
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
  { key: 'y2025_dividend', label: '25年每股分红', align: 'right',  width: 120, minWidth: 112 },
  { key: 'y2026_dividend', label: '26年每股分红', align: 'right',  width: 120, minWidth: 112 },
  { key: 'y2024_dividend', label: '24年每股分红', align: 'right',  width: 120, minWidth: 112 },
  { key: 'ttm_yield',      label: 'TTM收益率',    align: 'right',  width: 116, minWidth: 110 },
  { key: 'actions',        label: '操作',         align: 'center', width: 142, minWidth: 136, sortable: false, resizable: false },
]

// ---------- 列定义与可见性状态 ----------
// 默认：所有列都可见。「可见」是按 key 记录「是否在列设置里被勾选」
// 的一组集合（Set），不是删除列。DEFAULT_VISIBLE_KEYS 记录在所有 checkbox
// 都关掉时仍要保留的「锁定的」列（不让用户关掉关键标识列）。
// 「操作」列是「全不显示」也要保留的最后一道防线：用户至少能重新打开列设置
// 「名称」虽未锁定，但实际渲染里仍显示，避免「资产列表」成无标识的空表
const LOCKED_KEYS = new Set(['actions'])

// 列宽、可见性、显示顺序各存一个 key
const STORAGE_WIDTHS_KEY = 'portfolio.assetList.colWidths.v1'
const STORAGE_VISIBILITY_KEY = 'portfolio.assetList.visibility.v1'
const STORAGE_ORDER_KEY = 'portfolio.assetList.order.v1'

const columns = ref(DEFAULT_COLUMNS.map((c) => ({ ...c })))

const visibleKeys = ref(new Set(DEFAULT_COLUMNS.map((c) => c.key)))
const showColumnPicker = ref(false)

/** 实际渲染到表头/表体的列。LOCKED_KEYS 永远在内。 */
const visibleColumns = computed(() =>
  columns.value.filter((c) => visibleKeys.value.has(c.key))
)

const totalWidth = computed(() =>
  visibleColumns.value.reduce((sum, c) => sum + c.width, 0)
)

// ---------- 列可见性工具 ----------
function isColumnVisible(key) {
  return visibleKeys.value.has(key)
}

function isLockedColumn(key) {
  return LOCKED_KEYS.has(key)
}

function toggleColumn(key) {
  if (LOCKED_KEYS.has(key)) return
  const next = new Set(visibleKeys.value)
  if (next.has(key)) {
    // 至少保留一个列可见，避免全空导致表头丢失
    if (next.size > 1) next.delete(key)
  } else {
    next.add(key)
  }
  visibleKeys.value = next
  persistVisibility()
}

const visibleColumnCount = computed(() => visibleKeys.value.size)

function showAllColumns() {
  visibleKeys.value = new Set(DEFAULT_COLUMNS.map((c) => c.key))
  persistVisibility()
}

function hideAllColumns() {
  // LOCKED_KEYS 始终保留
  visibleKeys.value = new Set(LOCKED_KEYS)
  persistVisibility()
}

function resetColumnVisibility() {
  visibleKeys.value = new Set(DEFAULT_COLUMNS.map((c) => c.key))
  persistVisibility()
}

function persistVisibility() {
  try {
    localStorage.setItem(
      STORAGE_VISIBILITY_KEY,
      JSON.stringify([...visibleKeys.value])
    )
  } catch {
    /* 隐私模式等 */
  }
}

// ---------- 列顺序（拖动表头调整） ----------
function persistOrder() {
  try {
    localStorage.setItem(
      STORAGE_ORDER_KEY,
      JSON.stringify(columns.value.map((c) => c.key))
    )
  } catch {
    /* 隐私模式等 */
  }
}

const draggingKey = ref(null)      // 正在被拖动的列
const dropTargetKey = ref(null)    // 悬停到的目标列
const dropAfter = ref(false)       // 插入到目标列之后（true）还是之前（false）
// 拖动刚结束时抑制紧随其后的 click（否则会顺带触发表头排序）
let suppressSortClick = false

const draggingCol = computed(() =>
  columns.value.find((c) => c.key === draggingKey.value) || null
)

/** 指针是否已移动足够距离（区分「点击排序」与「拖动排序」） */
const DRAG_THRESHOLD_PX = 5
let headerDown = null   // { key, startX }

function onHeaderMouseDown(e, col) {
  // 点在列宽拖动条上时走 resize，不进入排序拖动
  if (e.target?.closest?.('.col-resizer')) return
  if (e.button !== 0) return
  headerDown = { key: col.key, startX: e.clientX }
  window.addEventListener('mousemove', onHeaderMouseMove)
  window.addEventListener('mouseup', onHeaderMouseUp)
}

function onHeaderMouseMove(e) {
  if (!headerDown) return
  if (!draggingKey.value) {
    if (Math.abs(e.clientX - headerDown.startX) < DRAG_THRESHOLD_PX) return
    draggingKey.value = headerDown.key
  }
  // 找出指针下方的表头，决定插入位置
  const el = document.elementFromPoint(e.clientX, e.clientY)
  const th = el?.closest?.('th')
  const key = th?.dataset?.colKey
  if (!key || key === draggingKey.value) {
    dropTargetKey.value = null
    return
  }
  dropTargetKey.value = key
  const rect = th.getBoundingClientRect()
  dropAfter.value = e.clientX > rect.left + rect.width / 2
}

function onHeaderMouseUp() {
  window.removeEventListener('mousemove', onHeaderMouseMove)
  window.removeEventListener('mouseup', onHeaderMouseUp)

  const from = draggingKey.value
  const to = dropTargetKey.value
  if (from && to && from !== to) {
    applyReorder(from, to, dropAfter.value)
    suppressSortClick = true   // 让紧随的 click 不触发排序
    setTimeout(() => { suppressSortClick = false }, 0)
  }

  headerDown = null
  draggingKey.value = null
  dropTargetKey.value = null
  dropAfter.value = false
}

/** 把 fromKey 列移到 toKey 列的前/后（对整列数组生效，含隐藏列） */
function applyReorder(fromKey, toKey, after) {
  const arr = [...columns.value]
  const fromIdx = arr.findIndex((c) => c.key === fromKey)
  if (fromIdx < 0) return
  const [moved] = arr.splice(fromIdx, 1)
  let toIdx = arr.findIndex((c) => c.key === toKey)
  if (toIdx < 0) return
  if (after) toIdx += 1
  arr.splice(toIdx, 0, moved)
  columns.value = arr
  persistOrder()
}

function onHeaderClick(col) {
  if (suppressSortClick) return
  if (col.sortable === false) return
  sortBy(col.key)
}

// ---------- 列宽持久化 ----------
onMounted(() => {
  // 列宽
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_WIDTHS_KEY) || '{}')
    for (const col of columns.value) {
      const w = Number(saved[col.key])
      if (Number.isFinite(w) && w >= col.minWidth) col.width = Math.round(w)
    }
  } catch {
    /* 存储不可用或数据损坏：使用默认宽度即可 */
  }
  // 列可见性
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_VISIBILITY_KEY) || '[]')
    if (Array.isArray(saved) && saved.length > 0) {
      // 尊重用户保存的选择（含「全不显示」只留锁定列的情况），
      // 只做两件事：过滤掉已不存在的列 key、补回锁定列
      const next = new Set(saved.filter((k) => DEFAULT_COLUMNS.some((c) => c.key === k)))
      for (const k of LOCKED_KEYS) next.add(k)
      visibleKeys.value = next
    }
  } catch {
    /* 忽略 */
  }
  // 列顺序：按保存的 key 顺序重排 columns；
  // 未在保存列表里的新列（如后续版本新增的字段）追加到末尾，不会被丢掉
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_ORDER_KEY) || '[]')
    if (Array.isArray(saved) && saved.length > 0) {
      const byKey = new Map(columns.value.map((c) => [c.key, c]))
      const ordered = []
      for (const k of saved) {
        if (byKey.has(k)) {
          ordered.push(byKey.get(k))
          byKey.delete(k)
        }
      }
      // 剩下的（新列 / 已从保存列表移除的）保持默认相对顺序追加
      for (const c of columns.value) {
        if (byKey.has(c.key)) ordered.push(c)
      }
      columns.value = ordered
    }
  } catch {
    /* 忽略 */
  }
})

function persistWidths() {
  try {
    const out = {}
    for (const col of columns.value) out[col.key] = col.width
    localStorage.setItem(STORAGE_WIDTHS_KEY, JSON.stringify(out))
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

.btn-icon {
  display: inline-block;
  margin-right: 2px;
}

.btn-count {
  margin-left: 4px;
  opacity: 0.7;
  font-weight: 500;
}

/* 列选择面板：紧贴表头上方的水平条，水平排列所有列的勾选框 */
.column-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 10px;
  background: #f8fafc;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: var(--radius, 8px);
}

.column-picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.column-picker-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}

.column-picker-actions {
  display: flex;
  gap: 4px;
}

.column-picker-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.column-picker-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-primary, #1e293b);
  user-select: none;
  transition: background 0.12s;
}

.column-picker-item:hover:not(.is-disabled) {
  background: rgba(37, 99, 235, 0.08);
}

.column-picker-item input[type='checkbox'] {
  margin: 0;
  cursor: pointer;
}

.column-picker-item.is-disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.column-picker-item.is-disabled input[type='checkbox'] {
  cursor: not-allowed;
}

.column-picker-hint {
  font-size: 11px;
  color: var(--text-secondary, #94a3b8);
  margin-left: 2px;
}

.btn-ghost {
  background: transparent;
  color: var(--text-primary, #1e293b);
  border: 1px solid var(--border-color, #e2e8f0);
}

.btn-ghost:hover:not(:disabled) {
  background: rgba(15, 23, 42, 0.04);
  border-color: var(--primary-color, #2563eb);
  color: var(--primary-color, #2563eb);
}

.btn-sm {
  padding: 4px 10px;
  font-size: 12px;
  border-radius: 4px;
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

/* ---------- 拖动表头调整列顺序 ---------- */
.data-table :deep(th) {
  cursor: grab;
}

.data-table :deep(th.is-dragging) {
  cursor: grabbing;
  opacity: 0.45;
}

/* 插入位置指示线：贴在目标列的左/右边界 */
.data-table :deep(th.drop-before)::before,
.data-table :deep(th.drop-after)::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--primary-color, #2563eb);
  z-index: 3;
  pointer-events: none;
}

.data-table :deep(th.drop-before)::before {
  left: -1px;
}

.data-table :deep(th.drop-after)::after {
  right: -1px;
}

/* 拖动排序进行中：整表禁止选中文本 */
.data-table :deep(th.is-dragging) ~ :deep(th),
.data-table.reordering {
  user-select: none;
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
