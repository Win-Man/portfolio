<template>
  <div>
    <header class="app-header">
      <h1>📈 投资组合管理</h1>
      <div class="header-actions">
        <div class="portfolio-selector">
          <label>选择组合：</label>
          <select v-model="selectedPortfolioId" @change="loadData">
            <option v-for="p in portfolios" :key="p.id" :value="p.id">
              {{ p.name }}
            </option>
          </select>
        </div>
      </div>
    </header>
    
    <main class="container">
      <div v-if="loadingSummary" class="loading">加载汇总数据...</div>
      <PortfolioOverview v-else :summary="summary" />
      
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import PortfolioOverview from '../components/PortfolioOverview.vue'
import AssetList from '../components/AssetList.vue'
import AssetForm from '../components/AssetForm.vue'
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
  await Promise.all([loadSummary(), loadAssets()])
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
    // 重新加载资产列表和汇总
    await loadData()
    
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
