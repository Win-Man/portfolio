<template>
  <div>
    <header class="app-header">
      <h1>📈 投资组合管理</h1>
      <div class="header-actions">
        <div class="portfolio-selector">
          <label>选择组合：</label>
          <select v-model="selectedPortfolioId" @change="handlePortfolioChange">
            <option v-for="p in portfolios" :key="p.id" :value="p.id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <div class="goal-selector">
          <label>目标：</label>
          <select v-model="selectedGoalId" :disabled="goals.length === 0">
            <option v-if="goals.length === 0" :value="null">暂无目标</option>
            <option v-for="g in goals" :key="g.id" :value="g.id">
              {{ g.name }}
            </option>
          </select>
        </div>
      </div>
    </header>
    
    <main class="container">
      <div v-if="loadingSummary" class="loading">加载汇总数据...</div>
      <PortfolioOverview v-else :summary="summary" />

      <PortfolioGoals
        :goals="goals"
        :selected-goal-id="selectedGoalId"
        :progress="goalProgress"
        :loading="loadingGoals"
        :progress-loading="progressLoading"
        :error="goalError"
        @update:selected-goal-id="selectedGoalId = $event"
        @add="openAddGoalModal"
        @edit="openEditGoalModal"
        @remove="handleGoalRemove"
        @recalc="handleGoalRecalc"
      />

      <PortfolioCharts :assets="assets" />

      <AssetList 
        :assets="assets" 
        :loading="loadingAssets"
        @edit="openEditModal"
        @delete="handleDelete"
        @add="openAddModal"
        @refresh="handleRefreshPrice"
        @refreshAll="handleRefreshAllPrices"
      />
    </main>
    
    <AssetForm 
      v-if="showModal"
      :asset="editingAsset"
      :submitting="submitting"
      @close="closeModal"
      @submit="handleSubmit"
    />

    <GoalForm
      v-if="showGoalModal"
      :goal="editingGoal"
      :submitting="goalSubmitting"
      @close="closeGoalModal"
      @submit="handleGoalSubmit"
    />
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, defineAsyncComponent } from 'vue'
import PortfolioOverview from '../components/PortfolioOverview.vue'
import AssetList from '../components/AssetList.vue'
import AssetForm from '../components/AssetForm.vue'
// PortfolioGoals 是轻量组件，同步引入；只有 echarts 图表走异步
import PortfolioGoals from '../components/PortfolioGoals.vue'
import GoalForm from '../components/GoalForm.vue'
import { toast } from '../composables/useToast.js'
import {
  getPortfolios,
  getPortfolioSummary,
  getAssets,
  createAsset,
  updateAsset,
  deleteAsset,
  refreshAssetPrice,
  refreshAllPrices
} from '../api/portfolio.js'
import {
  getGoals,
  createGoal,
  updateGoal,
  deleteGoal,
  getGoalProgress
} from '../api/goal.js'
import {
  pickInitialGoalId,
  readRememberedGoalId,
  persistGoalId
} from '../utils/goal.js'

// 图表依赖 echarts（约 460KB），异步加载避免拖慢首屏与主包体积
const PortfolioCharts = defineAsyncComponent(
  () => import('../components/PortfolioCharts.vue')
)

const portfolios = ref([])
const selectedPortfolioId = ref(null)
const summary = ref({
  total_market_value: 0,
  asset_count: 0,
  weighted_ttm_yield: 0,
  expected_annual_income: 0,
  description: '加载中...'
})
const assets = ref([])

const loadingSummary = ref(false)
const loadingAssets = ref(false)
const showModal = ref(false)
const editingAsset = ref(null)
const submitting = ref(false)

// ---------- 年收入目标 ----------
const goals = ref([])
const selectedGoalId = ref(null)
const goalProgress = ref(null)
const loadingGoals = ref(false)
const progressLoading = ref(false)
const goalError = ref('')
// null = 用后端默认（组合加权平均 TTM 收益率）；手填值不持久化（D5）
const assumedYieldOverride = ref(null)

const showGoalModal = ref(false)
const editingGoal = ref(null)
const goalSubmitting = ref(false)

// 并发保护：快速连打假设收益率时，只有最后一次请求的结果会写回
let progressRequestSeq = 0

onMounted(async () => {
  await loadPortfolios()
})

async function loadPortfolios() {
  try {
    const data = await getPortfolios()
    portfolios.value = data
    if (data.length > 0) {
      selectedPortfolioId.value = data[0].id
      await loadData()
    }
  } catch (e) {
    console.error('加载组合列表失败:', e)
  }
}

async function loadData() {
  if (!selectedPortfolioId.value) return
  await Promise.all([loadSummary(), loadAssets(), loadGoals()])
}

/** 组合切换：除重新加载数据外，清掉手填的假设收益率与上一条进度（D5 / spec §7.5-11） */
async function handlePortfolioChange() {
  assumedYieldOverride.value = null
  goalProgress.value = null
  goalError.value = ''
  await loadData()
}

async function loadSummary() {
  loadingSummary.value = true
  try {
    summary.value = await getPortfolioSummary(selectedPortfolioId.value)
  } catch (e) {
    console.error('加载汇总失败:', e)
    summary.value.description = '加载失败，请稍后重试'
  }
  loadingSummary.value = false
}

async function loadAssets() {
  loadingAssets.value = true
  try {
    assets.value = await getAssets(selectedPortfolioId.value)
  } catch (e) {
    console.error('加载资产列表失败:', e)
    assets.value = []
  }
  loadingAssets.value = false
}

// ---------- 年收入目标：加载 ----------
async function loadGoals() {
  const pid = selectedPortfolioId.value
  if (!pid) return
  loadingGoals.value = true
  goalError.value = ''
  try {
    const data = await getGoals(pid)
    goals.value = Array.isArray(data) ? data : []
    // 记忆值仍有效则用记忆值，否则落到第一个目标（D8）
    selectedGoalId.value = pickInitialGoalId(goals.value, readRememberedGoalId(pid))
    if (goals.value.length === 0) {
      // 列表空了：清掉该组合的记忆值，避免下次拿一个已不存在的 id
      persistGoalId(pid, null)
      goalProgress.value = null
    }
  } catch (e) {
    console.error('加载目标列表失败:', e)
    goals.value = []
    selectedGoalId.value = null
    goalProgress.value = null
    goalError.value = '目标加载失败，请稍后重试'
    toast.error('目标加载失败，请稍后重试')
  }
  loadingGoals.value = false
  if (selectedGoalId.value !== null && selectedGoalId.value !== undefined) {
    await loadGoalProgress()
  }
}

async function loadGoalProgress() {
  const pid = selectedPortfolioId.value
  const gid = selectedGoalId.value
  if (!pid || gid === null || gid === undefined) {
    // 作废在途请求，避免旧目标的进度把已清空的状态又写回来
    progressRequestSeq++
    progressLoading.value = false
    goalProgress.value = null
    return
  }
  const seq = ++progressRequestSeq
  progressLoading.value = true
  try {
    const data = await getGoalProgress(pid, gid, assumedYieldOverride.value)
    if (seq === progressRequestSeq) goalProgress.value = data
  } catch (e) {
    console.error('加载目标进度失败:', e)
    // 保留上一次的 progress，避免页面上已有的数字闪没
    toast.error('目标进度计算失败，请稍后重试')
  } finally {
    if (seq === progressRequestSeq) progressLoading.value = false
  }
}

// 切换目标 → 记住选择 + 手填的假设收益率回到默认值 + 重算
watch(selectedGoalId, async (id) => {
  const pid = selectedPortfolioId.value
  if (pid) persistGoalId(pid, id === undefined ? null : id)
  assumedYieldOverride.value = null
  await loadGoalProgress()
})

/** 假设收益率输入：y 为百分数，null = 用后端默认值 */
function handleGoalRecalc(y) {
  assumedYieldOverride.value = y
  // 错误态（错误态只可能来自目标列表加载失败）下「重试」要重新拉列表，
  // 否则此时 selectedGoalId 已被清空，只重算进度会直接早退、按钮形同虚设
  if (goalError.value) {
    loadGoals()
    return
  }
  loadGoalProgress()
}

// ---------- 年收入目标：新增 / 编辑 / 删除 ----------
function openAddGoalModal() {
  editingGoal.value = null
  showGoalModal.value = true
}

function openEditGoalModal(goal) {
  editingGoal.value = goal ? { ...goal } : null
  showGoalModal.value = true
}

function closeGoalModal() {
  showGoalModal.value = false
  editingGoal.value = null
}

async function handleGoalSubmit(payload) {
  const pid = selectedPortfolioId.value
  if (!pid) return
  goalSubmitting.value = true
  try {
    if (editingGoal.value) {
      await updateGoal(editingGoal.value.id, payload)
      await loadGoals()
      await loadGoalProgress()
    } else {
      const created = await createGoal(pid, payload)
      await loadGoals()
      // 选中新建的目标；id 变化会触发 watch（记住选择 + 重算进度）
      if (created && created.id !== null && created.id !== undefined) {
        selectedGoalId.value = created.id
      }
      await nextTick()
      await loadGoalProgress()
    }
    closeGoalModal()
  } catch (e) {
    // 409「同名目标已存在」等后端文案直接透传给用户；弹窗保持打开以便修改
    console.error('目标提交失败:', e)
    toast.error(e.message || '提交失败，请稍后重试')
  }
  goalSubmitting.value = false
}

async function handleGoalRemove(goal) {
  if (!goal) return
  try {
    await deleteGoal(goal.id)
    // loadGoals 内会按记忆值重新选（被删的目标记忆失效 → 落到第一个目标）
    await loadGoals()
    await loadGoalProgress()
  } catch (e) {
    console.error('删除目标失败:', e)
    toast.error(e.message || '删除失败，请稍后重试')
  }
}

function openAddModal() {
  editingAsset.value = null
  showModal.value = true
}

function openEditModal(asset) {
  editingAsset.value = { ...asset }
  showModal.value = true
}

function closeModal() {
  showModal.value = false
  editingAsset.value = null
}

async function handleSubmit(data) {
  submitting.value = true
  try {
    if (editingAsset.value) {
      await updateAsset(editingAsset.value.id, data)
    } else {
      await createAsset(selectedPortfolioId.value, data)
    }
    closeModal()
    await loadData()
  } catch (e) {
    console.error('提交失败:', e)
    toast.error('提交失败，请稍后重试')
  }
  submitting.value = false
}

async function handleDelete(assetId) {
  if (!confirm('确定要删除这个资产吗？')) return
  
  try {
    await deleteAsset(assetId)
    await loadData()
  } catch (e) {
    console.error('删除失败:', e)
    toast.error('删除失败，请稍后重试')
  }
}

async function handleRefreshPrice(asset, done) {
  try {
    const updatedAsset = await refreshAssetPrice(asset)
    // 更新本地数据
    const index = assets.value.findIndex(a => a.id === asset.id)
    if (index !== -1) {
      assets.value[index] = updatedAsset
    }
    // 重新计算汇总
    await loadSummary()
    // 市值与年收入都会变，目标进度也要跟着重算
    await loadGoalProgress()
  } catch (e) {
    console.error('刷新价格失败:', e)
    toast.error('刷新价格失败，请稍后重试')
  } finally {
    done && done()
  }
}

async function handleRefreshAllPrices() {
  try {
    const result = await refreshAllPrices(selectedPortfolioId.value)
    // 重新加载资产列表和汇总（loadData 里已含目标列表）
    await loadData()
    // 市值与年收入都会变，目标进度也要跟着重算
    await loadGoalProgress()
    
    const successCount = result.results.filter(r => r.success).length
    const failCount = result.results.filter(r => !r.success).length
    const msg = `价格刷新完成：${successCount} 成功 / ${failCount} 失败`
    if (failCount === 0) {
      toast.success(msg)
    } else {
      toast.warning(msg)
    }
  } catch (e) {
    console.error('批量刷新价格失败:', e)
    toast.error('批量刷新价格失败，请稍后重试')
  }
}
</script>

<style scoped>
/* header 里的「目标：」下拉：与 main.css 的 .portfolio-selector 保持一致的视觉 */
.goal-selector {
  display: flex;
  align-items: center;
  gap: 12px;
}

.goal-selector label {
  font-weight: 500;
  color: var(--text-secondary);
}

.goal-selector select {
  padding: 8px 16px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  font-size: 14px;
  background: var(--card-bg);
  cursor: pointer;
  min-width: 200px;
}

.goal-selector select:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
</style>
