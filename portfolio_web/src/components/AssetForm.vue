<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal modal-lg">
      <div class="modal-header">
        <h2>{{ isEdit ? '编辑资产' : '新增资产' }}</h2>
        <button class="modal-close" @click="$emit('close')">&times;</button>
      </div>
      
      <form @submit.prevent="handleSubmit" novalidate>
        <div class="modal-body">
          <!-- 股票搜索区域（仅新增时显示） -->
          <div v-if="!isEdit" class="search-section">
            <label>🔍 搜索股票 / ETF</label>
            <div class="search-box">
              <input 
                v-model="searchKeyword" 
                type="text" 
                placeholder="输入代码或名称搜索（支持 ETF，如 561580）"
                @input="handleSearchInput"
                class="search-input"
              />
              <div v-if="searching" class="search-loading">搜索中...</div>
              
              <!-- 搜索结果下拉（放在search-box内部以便正确定位） -->
              <div v-if="searchResults.length > 0" class="search-results">
                <div 
                  v-for="stock in searchResults" 
                  :key="stock.code"
                  class="search-result-item"
                  @click="selectStock(stock)"
                >
                  <span class="stock-code">{{ stock.code }}</span>
                  <span class="stock-name">{{ stock.name }}</span>
                  <span
                    class="stock-type"
                    :class="stock.type === 'ETF' ? 'type-etf' : 'type-stock'"
                  >{{ stock.type || '股票' }}</span>
                  <span class="stock-market">{{ stock.market }}</span>
                </div>
              </div>
              
              <div v-if="searched && searchResults.length === 0 && !searching" class="search-empty">
                未找到匹配的标的，请手动输入
              </div>
            </div>
            
            <div class="search-divider">
              <span>或手动输入</span>
            </div>
          </div>
          
          <div class="form-group">
            <label>名称 *</label>
            <input 
              v-model="form.name" 
              type="text" 
              placeholder="例如：招商银行 / 央企红利ETF"
              required
            />
            <div v-if="errors.name" class="error">{{ errors.name }}</div>
          </div>
          
          <div class="form-group">
            <label>代码 *</label>
            <input 
              v-model="form.code" 
              type="text" 
              placeholder="例如：600036 / 561580"
              required
            />
            <div v-if="errors.code" class="error">{{ errors.code }}</div>
          </div>
          
          <div class="form-row">
            <div class="form-group">
              <label>持股数量 *</label>
              <input 
                v-model.number="form.quantity" 
                type="number" 
                min="1"
                placeholder="例如：1000"
                required
              />
              <div v-if="errors.quantity" class="error">{{ errors.quantity }}</div>
            </div>
            
            <div class="form-group">
              <label>最新价格 (元) *</label>
              <input 
                v-model.number="form.price" 
                type="number" 
                min="0.01"
                step="0.01"
                placeholder="例如：35.50"
                required
              />
              <div v-if="errors.price" class="error">{{ errors.price }}</div>
            </div>
          </div>
          
          <div class="form-row">
            <div class="form-group">
              <label>最近一年每股分红 (元)</label>
              <input 
                v-model.number="form.dividend_per_share" 
                type="number" 
                min="0"
                step="0.01"
                placeholder="例如：1.20"
                @input="calculateTTMFromDividend"
              />
              <div class="form-hint">填写后将自动计算 TTM 收益率</div>
            </div>
            
            <div class="form-group">
              <label>TTM 收益率 (%)</label>
              <input 
                v-model.number="form.ttm_yield" 
                type="number" 
                min="0"
                max="100"
                step="0.01"
                placeholder="例如：3.38"
                @input="calculateDividendFromTTM"
              />
              <div v-if="form.price > 0 && form.dividend_per_share > 0" class="form-hint">
                自动计算值：{{ calculatedTTM.toFixed(2) }}%
              </div>
            </div>
          </div>
          
          <div class="form-group">
            <label>备注</label>
            <textarea 
              v-model="form.note" 
              placeholder="可选备注信息"
              rows="2"
            ></textarea>
          </div>
        </div>
        
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" @click="$emit('close')">取消</button>
          <button type="submit" class="btn btn-primary" :disabled="submitting">
            {{ submitting ? '提交中...' : (isEdit ? '保存' : '添加') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { searchStocks, getStockPrice, guessAssetType } from '../api/stock.js'
import { toast } from '../composables/useToast.js'

const props = defineProps({
  asset: {
    type: Object,
    default: null
  },
  submitting: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close', 'submit'])

const isEdit = computed(() => !!props.asset)

// 搜索相关
const searchKeyword = ref('')
const searchResults = ref([])
const searching = ref(false)
const searched = ref(false)
let searchTimeout = null

const form = ref({
  name: '',
  code: '',
  asset_type: '股票',
  quantity: null,
  price: null,
  dividend_per_share: null,
  ttm_yield: null,
  note: ''
})

const errors = ref({})

watch(() => props.asset, (newAsset) => {
  if (newAsset) {
    // 编辑已有资产：补齐可能缺失的 asset_type
    form.value = { asset_type: guessAssetType(newAsset.code), ...newAsset }
  } else {
    form.value = {
      name: '',
      code: '',
      asset_type: '股票',
      quantity: null,
      price: null,
      dividend_per_share: null,
      ttm_yield: null,
      note: ''
    }
  }
  errors.value = {}
  // 重置搜索
  searchKeyword.value = ''
  searchResults.value = []
  searched.value = false
}, { immediate: true })

// 资产类型跟随代码自动识别：手动输入 561580 也会变成 ETF
watch(() => form.value.code, (code) => {
  if (code) {
    form.value.asset_type = guessAssetType(code)
  }
})

const calculatedTTM = computed(() => {
  if (form.value.price > 0 && form.value.dividend_per_share > 0) {
    return (form.value.dividend_per_share / form.value.price) * 100
  }
  return 0
})

// 从每股分红计算 TTM
function calculateTTMFromDividend() {
  if (form.value.price > 0 && form.value.dividend_per_share > 0) {
    form.value.ttm_yield = calculatedTTM.value
  }
}

// 从 TTM 反推每股分红
function calculateDividendFromTTM() {
  if (form.value.price > 0 && form.value.ttm_yield > 0) {
    form.value.dividend_per_share = (form.value.ttm_yield * form.value.price) / 100
  }
}

// 搜索输入处理（防抖）
function handleSearchInput() {
  if (searchTimeout) {
    clearTimeout(searchTimeout)
  }
  
  const keyword = searchKeyword.value.trim()
  
  if (keyword.length < 1) {
    searchResults.value = []
    searched.value = false
    return
  }
  
  searching.value = true
  searched.value = false
  
  searchTimeout = setTimeout(async () => {
    try {
      const results = await searchStocks(keyword)
      searchResults.value = results
    } catch (e) {
      console.error('搜索失败:', e)
      searchResults.value = []
    } finally {
      searching.value = false
      searched.value = true
    }
  }, 300) // 300ms 防抖
}

// 选择搜索结果
async function selectStock(stock) {
  form.value.code = stock.code
  form.value.name = stock.name
  // 带上资产类型（ETF / 股票），后端也会按代码兜底推断
  form.value.asset_type = stock.type || guessAssetType(stock.code)
  searchKeyword.value = ''
  searchResults.value = []
  searched.value = false
  
  // 清除相关错误
  delete errors.value.code
  delete errors.value.name
  
  // 自动获取实时价格
  const priceData = await getStockPrice(stock.code)
  if (priceData && priceData.price) {
    form.value.price = priceData.price
  }
}

function validate() {
  errors.value = {}
  
  if (!form.value.name?.trim()) {
    errors.value.name = '请输入股票名称'
  }
  
  if (!form.value.code?.trim()) {
    errors.value.code = '请输入股票代码'
  }
  
  if (!form.value.quantity || form.value.quantity <= 0) {
    errors.value.quantity = '请输入有效的持股数量'
  }
  
  if (!form.value.price || form.value.price <= 0) {
    errors.value.price = '请输入有效的价格'
  }
  
  return Object.keys(errors.value).length === 0
}

function handleSubmit() {
  console.log('[AssetForm] handleSubmit called, form:', JSON.parse(JSON.stringify(form.value)))
  if (!validate()) {
    console.warn('[AssetForm] validation failed:', errors.value)
    // 校验失败时 toast 第一个错误（红色 .error 字小，可能没注意）
    const firstError = Object.values(errors.value)[0]
    if (firstError) {
      toast.warning(`请检查表单：${firstError}`)
    }
    return
  }

  const data = { ...form.value }

  // 如果填写了每股分红且没有手动输入 TTM，则自动计算
  if (data.price > 0 && data.dividend_per_share > 0 && data.ttm_yield === null) {
    data.ttm_yield = calculatedTTM.value
  }

  console.log('[AssetForm] submitting:', data)
  // 清理 null 字段：null 会让后端 Pydantic 报 422，未提供的字段会走 schema 默认值
  // 注意 0 是合法值（用户主动填的 0），不要清掉
  const payload = Object.fromEntries(
    Object.entries(data).filter(([_, v]) => v !== null && v !== '')
  )
  emit('submit', payload)
}
</script>

<style scoped>
.modal-lg {
  max-width: 600px;
}

.search-section {
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px dashed #ddd;
}

.search-section label {
  display: block;
  margin-bottom: 8px;
  font-weight: 500;
}

.search-box {
  position: relative;
}

.search-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
}

.search-input:focus {
  outline: none;
  border-color: #1890ff;
  box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.2);
}

.search-loading {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: #999;
  font-size: 12px;
}

.search-results {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: white;
  border: 1px solid #ddd;
  border-radius: 6px;
  margin-top: 4px;
  max-height: 240px;
  overflow-y: auto;
  z-index: 100;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.search-result-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f0f0f0;
}

.search-result-item:last-child {
  border-bottom: none;
}

.search-result-item:hover {
  background-color: #e6f7ff;
}

.stock-code {
  font-weight: 600;
  color: #333;
  margin-right: 12px;
  min-width: 70px;
}

.stock-name {
  flex: 1;
  color: #666;
}

/* 资产类型徽标：ETF 用醒目色以区分个股 */
.stock-type {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  margin-right: 6px;
  white-space: nowrap;
}

.type-stock {
  background: #eef2f8;
  color: #475569;
}

.type-etf {
  background: #fff1e6;
  color: #c2410c;
  border: 1px solid #fed7aa;
  font-weight: 600;
}

.stock-market {
  font-size: 12px;
  color: #999;
  background: #f5f5f5;
  padding: 2px 6px;
  border-radius: 3px;
}

.search-empty {
  padding: 12px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

.search-divider {
  text-align: center;
  margin-top: 16px;
  position: relative;
}

.search-divider::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background: #ddd;
}

.search-divider span {
  position: relative;
  background: white;
  padding: 0 12px;
  color: #999;
  font-size: 12px;
}

.form-row {
  display: flex;
  gap: 16px;
}

.form-row .form-group {
  flex: 1;
}

.form-hint {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
}

textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  resize: vertical;
  font-family: inherit;
}

textarea:focus {
  outline: none;
  border-color: #1890ff;
  box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.2);
}
</style>
