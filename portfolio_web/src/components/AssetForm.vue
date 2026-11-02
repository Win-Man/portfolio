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
                @input="onQuantityInput"
              />
              <div v-if="errors.quantity" class="error">{{ errors.quantity }}</div>
            </div>
            
            <div class="form-group">
              <label>最新价格 (元) *</label>
              <input 
                v-model.number="form.price" 
                type="number" 
                min="0.001"
                step="0.001"
                placeholder="例如：35.50 / 1.523"
                required
              />
              <div v-if="errors.price" class="error">{{ errors.price }}</div>
            </div>
          </div>
          
          <div class="form-row">
            <div class="form-group">
              <label>每股成本 (元)</label>
              <!--
                step=any 而不是 0.001：券商显示的 3 位成本是四舍五入后的值，
                真实成本（总成本÷份数）常带 6 位小数。固定 3 位会让浏览器
                直接判 stepMismatch、拦下提交，并且把盈亏算偏（513530 差 1.03 元）。
              -->
              <input 
                v-model.number="form.cost_price" 
                type="number" 
                step="any"
                placeholder="留空则按最新价格"
                @input="onCostPriceInput"
              />
              <div class="form-hint">
                <template v-if="!costTouched">未填时默认等于最新价（盈亏为 0）</template>
                <template v-else-if="profitPreviewText">盈亏 {{ profitPreviewText }}</template>
                <template v-else-if="isNegativeCost">成本 ≤ 0，仅按金额计算盈亏</template>
              </div>
            </div>

            <div class="form-group">
              <label>持仓成本总额 (元)</label>
              <input 
                :value="costAmount ?? ''" 
                type="number" 
                step="0.01"
                placeholder="例如：11408.53"
                @input="onCostAmountInput"
              />
              <div class="form-hint">
                按券商「市值 − 盈亏」填，可反推每股成本
              </div>
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
import { formatAmount } from '../utils/format.js'
import { costAmountFromPrice, costPriceFromAmount } from '../utils/cost.js'

const props = defineProps({
  asset: {
    type: Object,
    default: null
  },
  submitting: {
    type: Boolean,
    default: false
  },
  // 可选：新增时的预填值（spec 003 D12「转入持仓」用）。
  // 只填 code / name / price / asset_type，数量与成本留空待用户填写；
  // 不传时行为与原来完全一致（编辑模式忽略它）。
  initialValues: {
    type: Object,
    default: null
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
  cost_price: null,
  dividend_per_share: null,
  ttm_yield: null,
  note: ''
})

/** 空白表单（新增时的初始值） */
function emptyForm() {
  return {
    name: '',
    code: '',
    asset_type: '股票',
    quantity: null,
    price: null,
    cost_price: null,
    dividend_per_share: null,
    ttm_yield: null,
    note: ''
  }
}

/**
 * 只把「预填白名单」里的字段合并进空白表单：
 * 数量 / 成本 / 分红一律留空（转入持仓时这些是用户要自己决定的值）。
 */
function withInitialValues(base) {
  const source = props.initialValues
  if (!source) return base
  const next = { ...base }
  if (source.code) next.code = String(source.code)
  if (source.name) next.name = String(source.name)
  if (source.asset_type) next.asset_type = source.asset_type
  else if (source.code) next.asset_type = guessAssetType(source.code)
  const price = Number(source.price)
  if (Number.isFinite(price) && price > 0) next.price = price
  return next
}

const errors = ref({})

/**
 * 成本是否被用户手动改过。
 * 未改过时，成本跟随「最新价」自动填充（含搜索选股后自动拉到的价格）；
 * 一旦用户动过成本输入框，就不再覆盖他的输入。
 */
const costTouched = ref(false)

const profitPreview = computed(() => {
  const p = Number(form.value.price)
  const c = Number(form.value.cost_price)
  // 成本 ≤ 0 时比例无意义（负成本会算出误导性的负百分比），只展示金额
  if (!p || !c || c <= 0) return null
  return ((p - c) / c) * 100
})

/** 盈亏金额预览（与券商 App 的「总盈亏」同口径：(现价 − 成本) × 份数） */
const profitPreviewAmount = computed(() => {
  const p = Number(form.value.price)
  const q = Number(form.value.quantity)
  const c = Number(form.value.cost_price)
  if (!Number.isFinite(p) || !Number.isFinite(q) || !Number.isFinite(c)) return null
  if (p <= 0 || q <= 0 || form.value.cost_price === null || form.value.cost_price === '') return null
  return (p - c) * q
})

/**
 * 盈亏提示文案：「+¥13.97（+0.12%）」。
 * 金额放前面 —— 用户手上是券商的「总盈亏」，逐位对账看金额最快，
 * 只给百分比（旧版）对一个 1 万元的仓位没有任何可核对性。
 */
const profitPreviewText = computed(() => {
  const amount = profitPreviewAmount.value
  if (amount === null) return ''
  const amountText = `${amount >= 0 ? '+' : '−'}¥${formatAmount(Math.abs(amount))}`
  const pct = profitPreview.value
  if (pct === null) return amountText
  return `${amountText}（${pct >= 0 ? '+' : '−'}${Math.abs(pct).toFixed(2)}%）`
})

// ---------- 每股成本 ↔ 持仓成本总额 联动 ----------
/**
 * 「谁是最后编辑的，谁就是准的」：
 *   - 改每股成本 → 总额跟着算；
 *   - 改总额     → 每股成本 = 总额 ÷ 份数（6 位小数）；
 *   - 改份数     → 若最后编的是总额，则保持总额不变反推每股成本；否则重算总额。
 * 这样券商成本只有 3 位显示时，用户可以用「市值 − 总盈亏」精确填进来。
 */
const costAmount = ref(null)
const amountIsSource = ref(false)

function refreshCostAmount() {
  costAmount.value = costAmountFromPrice(form.value.cost_price, form.value.quantity)
}

function onCostPriceInput() {
  costTouched.value = true
  amountIsSource.value = false
  refreshCostAmount()
  delete errors.value.cost_price
}

function onCostAmountInput(event) {
  const raw = event.target.value
  if (raw === '') {
    costAmount.value = null
    amountIsSource.value = false
    return
  }
  const amount = Number(raw)
  if (!Number.isFinite(amount)) return
  costAmount.value = amount
  amountIsSource.value = true
  costTouched.value = true
  form.value.cost_price = costPriceFromAmount(amount, form.value.quantity)
}

function onQuantityInput() {
  if (amountIsSource.value) {
    form.value.cost_price = costPriceFromAmount(costAmount.value, form.value.quantity)
  } else {
    refreshCostAmount()
  }
}

/** 成本为负（分红/高抛低吸后摊薄成本转负）——合法，但比例不展示 */
const isNegativeCost = computed(() => {
  const c = Number(form.value.cost_price)
  return Number.isFinite(c) && c < 0
})

// 最新价变化时，若成本未被手动改过则跟随
watch(() => form.value.price, (p) => {
  if (!costTouched.value && p > 0) {
    form.value.cost_price = p
    refreshCostAmount()
  }
})

watch(() => props.asset, (newAsset) => {
  if (newAsset) {
    // 编辑已有资产：补齐可能缺失的 asset_type
    form.value = { asset_type: guessAssetType(newAsset.code), ...newAsset }
    // 编辑时成本来自库里的真实值，不应被价格覆盖
    costTouched.value = true
  } else {
    // 新增：空白表单，可带「转入持仓」的预填值（不传时与原来完全一致）
    form.value = withInitialValues(emptyForm())
    costTouched.value = false
  }
  // 成本总额是「每股成本 × 份数」的派生显示，换标的时必须重算
  amountIsSource.value = false
  refreshCostAmount()
  errors.value = {}
  // 重置搜索
  searchKeyword.value = ''
  searchResults.value = []
  searched.value = false
}, { immediate: true })

// 同一个实例被复用且换了预填值时（例如连续两次「转入持仓」），同步刷新表单
watch(() => props.initialValues, () => {
  if (props.asset) return
  form.value = withInitialValues(emptyForm())
  amountIsSource.value = false
  refreshCostAmount()
  errors.value = {}
})

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
