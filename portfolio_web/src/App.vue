<template>
  <div class="app">
    <header class="app-header">
      <nav class="app-tabs">
        <router-link
          to="/"
          class="app-tab"
          :class="{ active: isPortfolioTab }"
        >投资组合</router-link>
        <router-link
          to="/calendar"
          class="app-tab"
          :class="{ active: isCalendarTab }"
        >收入日历</router-link>
      </nav>

      <div class="header-actions">
        <div class="portfolio-selector">
          <label>选择组合：</label>
          <select
            :value="selectedPortfolioId ?? ''"
            :disabled="portfolios.length === 0"
            @change="handlePortfolioChange"
          >
            <option v-if="portfolios.length === 0" value="">暂无组合</option>
            <option v-for="p in portfolios" :key="p.id" :value="p.id">
              {{ p.name }}
            </option>
          </select>
        </div>

        <div class="portfolio-actions">
          <button type="button" class="btn btn-primary btn-sm" @click="openAddPortfolio">
            ＋ 新增组合
          </button>
          <button
            type="button"
            class="btn btn-secondary btn-sm"
            :disabled="!selectedPortfolioId"
            @click="openRenamePortfolio"
          >重命名</button>
          <button
            type="button"
            class="btn btn-danger btn-sm"
            :disabled="!selectedPortfolioId"
            @click="handlePortfolioDelete"
          >删除组合</button>
        </div>
      </div>
    </header>

    <!-- keep-alive：切 tab 不销毁视图，日历数据留在各自的 ref 里（spec §8-19，不重复打接口） -->
    <router-view v-slot="{ Component }">
      <keep-alive>
        <component :is="Component" />
      </keep-alive>
    </router-view>

    <PortfolioForm
      v-if="showPortfolioModal"
      :portfolio="editingPortfolio"
      :submitting="portfolioSubmitting"
      @close="closePortfolioModal"
      @submit="handlePortfolioSubmit"
    />

    <Toast ref="toastRef" />
  </div>
</template>

<script setup>
/**
 * 应用外壳：header（tab 切换 + 组合选择器 + 组合管理）+ router-view。
 *
 * 组合选择器与「新增 / 重命名 / 删除组合」原本在 Portfolio.vue 的 header 里，
 * 现在提升到这里 —— 两个视图（投资组合 / 收入日历）共用同一个选中组合，
 * 通过 usePortfolioContext() 的 provide/inject 共享（spec §7.1 / §7.2）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import PortfolioForm from './components/PortfolioForm.vue'
import Toast from './components/Toast.vue'
import { setToastRef, toast } from './composables/useToast.js'
import { providePortfolioContext, usePortfolioContext } from './composables/usePortfolioContext.js'
import {
  createPortfolio,
  deletePortfolio,
  updatePortfolio,
} from './api/portfolio.js'
import { persistGoalId } from './utils/goal.js'

// 把共享状态 provide 给两个视图（模块级单例 + inject 访问器）
providePortfolioContext()

const {
  portfolios,
  selectedPortfolioId,
  reloadPortfolios,
  ensurePortfoliosLoaded,
  selectPortfolio,
  upsertPortfolioLocal,
} = usePortfolioContext()

const route = useRoute()
const isPortfolioTab = computed(() => route.name === 'portfolio')
const isCalendarTab = computed(() => route.name === 'calendar')

const toastRef = ref(null)

// ---------- 组合管理（新增 / 重命名 / 删除） ----------
const showPortfolioModal = ref(false)
const editingPortfolio = ref(null)
const portfolioSubmitting = ref(false)

onMounted(async () => {
  // 注册全局 toast 单例，供任意模块的 toast.success/error 调用
  setToastRef(toastRef.value)
  // 组合列表：router 守卫（直接进 /calendar）可能已经拉过，这里不重复请求
  await ensurePortfoliosLoaded()
})

/** 下拉切换组合：只改共享状态，各视图 watch(selectedPortfolioId) 自行重新加载 */
function handlePortfolioChange(event) {
  const raw = event.target.value
  const hit = portfolios.value.find((p) => String(p.id) === String(raw))
  selectPortfolio(hit ? hit.id : null)
}

function openAddPortfolio() {
  editingPortfolio.value = null
  showPortfolioModal.value = true
}

function openRenamePortfolio() {
  const current = portfolios.value.find((p) => p.id === selectedPortfolioId.value)
  if (!current) return
  editingPortfolio.value = { ...current }
  showPortfolioModal.value = true
}

function closePortfolioModal() {
  showPortfolioModal.value = false
  editingPortfolio.value = null
}

async function handlePortfolioSubmit(payload) {
  portfolioSubmitting.value = true
  try {
    if (editingPortfolio.value) {
      const updated = await updatePortfolio(editingPortfolio.value.id, payload.name)
      // 重命名：只更新本地列表项，不重拉数据（避免看板闪一下）
      upsertPortfolioLocal({ ...editingPortfolio.value, ...updated })
      toast.success('组合已重命名')
    } else {
      const created = await createPortfolio(payload.name)
      // 新增后直接选中新组合（reloadPortfolios 会把新 id 落到 preferredId）
      await reloadPortfolios(created?.id ?? null)
      toast.success(`组合「${created?.name ?? payload.name}」已创建`)
    }
    closePortfolioModal()
  } catch (e) {
    // 后端文案（如 422 校验失败）直接透传；弹窗保持打开以便修改
    console.error('保存组合失败:', e)
    toast.error(e.message || '保存失败，请稍后重试')
  }
  portfolioSubmitting.value = false
}

async function handlePortfolioDelete() {
  const current = portfolios.value.find((p) => p.id === selectedPortfolioId.value)
  if (!current) return
  if (!confirm(
    `确定要删除组合「${current.name}」吗？其下的资产与年收入目标会一并删除，且无法恢复。`
  )) {
    return
  }

  // 删除后优先选中原来的邻居组合，避免每次都跳回第一个
  const index = portfolios.value.findIndex((p) => p.id === current.id)
  const neighbor = portfolios.value[index + 1] || portfolios.value[index - 1] || null

  try {
    await deletePortfolio(current.id)
    // 该组合的记忆目标随组合一起失效，顺手清掉 localStorage 里的记录
    persistGoalId(current.id, null)
    // 当前选中的组合被删 → 先清空选中，再拉新列表并落到邻居
    if (selectedPortfolioId.value === current.id) {
      selectPortfolio(null)
    }
    await reloadPortfolios(neighbor ? neighbor.id : null)
    toast.success(`组合「${current.name}」已删除`)
  } catch (e) {
    console.error('删除组合失败:', e)
    toast.error(e.message || '删除组合失败，请稍后重试')
  }
}
</script>

<style scoped>
.app-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
}

.app-tab {
  padding: 8px 16px;
  border-radius: var(--radius);
  font-size: 15px;
  font-weight: 500;
  color: var(--text-secondary);
  text-decoration: none;
  transition: background 0.15s, color 0.15s;
}

.app-tab:hover {
  color: var(--primary-color);
  background: var(--bg-color);
}

.app-tab.active {
  color: #fff;
  background: var(--primary-color);
}

/* 窄屏时让 header 内的选择器/按钮换行，避免按钮被挤出视口 */
.header-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

/* 组合管理按钮组：新增 / 重命名 / 删除 */
.portfolio-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.portfolio-actions .btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
