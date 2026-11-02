<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal wl-modal">
      <div class="modal-header">
        <h2>{{ isEdit ? '编辑观察标的' : '新增观察标的' }}</h2>
        <button type="button" class="modal-close" @click="$emit('close')">&times;</button>
      </div>

      <form novalidate @submit.prevent="handleSubmit">
        <div class="modal-body">
          <!-- 代码搜索（仅新增时显示：代码是观察项的身份，编辑时不改） -->
          <div v-if="!isEdit" class="wl-search">
            <label>🔍 搜索标旳（代码或名称）</label>
            <div class="wl-search-box">
              <input
                v-model="searchKeyword"
                type="text"
                class="wl-search-input"
                placeholder="例如：长江电力 / 561580"
                @input="handleSearchInput"
              />
              <div v-if="searching" class="wl-search-loading">搜索中...</div>

              <div v-if="searchResults.length > 0" class="wl-search-results">
                <div
                  v-for="stock in searchResults"
                  :key="stock.code"
                  class="wl-search-item"
                  @click="selectStock(stock)"
                >
                  <span class="wl-stock-code">{{ stock.code }}</span>
                  <span class="wl-stock-name">{{ stock.name }}</span>
                  <span class="wl-stock-type">{{ stock.type || '股票' }}</span>
                  <span class="wl-stock-market">{{ stock.market }}</span>
                </div>
              </div>

              <div
                v-if="searched && searchResults.length === 0 && !searching"
                class="wl-search-empty"
              >
                未找到匹配的标的，可手动输入 6 位代码
              </div>
            </div>
            <div class="wl-search-divider"><span>或手动输入</span></div>
          </div>

          <div class="wl-form-row">
            <div class="form-group">
              <label>代码 *</label>
              <input
                v-model="form.code"
                type="text"
                placeholder="6 位代码，如 600900"
                :disabled="isEdit"
                @input="syncAssetType"
              />
              <div v-if="errors.code" class="error">{{ errors.code }}</div>
              <div v-else-if="isEdit" class="form-hint">代码不可修改（重码会被拒绝）</div>
            </div>

            <div class="form-group">
              <label>类型</label>
              <select v-model="form.asset_type" class="wl-select">
                <option value="股票">股票</option>
                <option value="ETF">ETF</option>
              </select>
            </div>
          </div>

          <div class="form-group">
            <label>名称 *</label>
            <input v-model="form.name" type="text" placeholder="例如：长江电力" />
            <div v-if="errors.name" class="error">{{ errors.name }}</div>
          </div>

          <div class="wl-form-row">
            <div class="form-group">
              <label>目标股息率 (%) *</label>
              <input
                v-model.number="form.target_yield"
                type="number"
                step="0.01"
                min="0.01"
                max="30"
                placeholder="例如：5.50"
              />
              <div v-if="errors.target_yield" class="error">{{ errors.target_yield }}</div>
              <div v-else class="form-hint">触发价 = TTM 每股分红 ÷ 该收益率</div>
            </div>

            <div class="form-group">
              <label>目标价 (元)</label>
              <input
                v-model.number="form.target_price"
                type="number"
                step="0.01"
                min="0"
                placeholder="选填，留空表示未设"
              />
              <div v-if="errors.target_price" class="error">{{ errors.target_price }}</div>
              <div v-else class="form-hint">
                {{ isEdit ? '清空后保存 = 取消目标价' : '选填，只影响「价格」那条信号' }}
              </div>
            </div>
          </div>

          <div class="form-group">
            <label>备注</label>
            <textarea v-model="form.note" rows="2" placeholder="可选备注，如「等回调到 5.5% 再买」"></textarea>
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
/**
 * 观察池新增 / 编辑弹窗。
 *
 * - 代码输入复用既有 `GET /api/stocks/search` 联想（与 AssetForm 同一接口）；
 * - 目标股息率必填（0 < v <= 30），目标价选填（D2）；前端先拦，后端 422 兜底；
 * - 编辑时目标价留空 → 提交 `0`，后端存 NULL 表示清空；
 * - 前端**不判断**是否达标，也不计算触发价 —— 全部由后端返回（D9）。
 */
import { computed, ref, watch } from 'vue'

import { guessAssetType, searchStocks } from '../api/stock.js'
import { toast } from '../composables/useToast.js'

const props = defineProps({
  /** 编辑时传入的观察项；null 表示新增 */
  item: {
    type: Object,
    default: null,
  },
  submitting: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['close', 'submit'])

const isEdit = computed(() => !!props.item)

function emptyForm() {
  return {
    code: '',
    name: '',
    asset_type: '股票',
    target_yield: null,
    target_price: null,
    note: '',
  }
}

const form = ref(emptyForm())
const errors = ref({})

// ---------- 搜索 ----------
const searchKeyword = ref('')
const searchResults = ref([])
const searching = ref(false)
const searched = ref(false)
let searchTimer = null

watch(
  () => props.item,
  (item) => {
    errors.value = {}
    if (item) {
      form.value = {
        code: item.code ?? '',
        name: item.name ?? '',
        asset_type: item.asset_type || guessAssetType(item.code),
        target_yield: item.target_yield ?? null,
        target_price: item.target_price ?? null,
        note: item.note ?? '',
      }
    } else {
      form.value = emptyForm()
    }
    searchKeyword.value = ''
    searchResults.value = []
    searched.value = false
  },
  { immediate: true },
)

function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  const keyword = searchKeyword.value.trim()
  if (keyword.length < 1) {
    searchResults.value = []
    searched.value = false
    return
  }

  searching.value = true
  searched.value = false
  searchTimer = setTimeout(async () => {
    try {
      const results = await searchStocks(keyword)
      searchResults.value = Array.isArray(results) ? results : []
    } catch (e) {
      console.error('搜索标的失败:', e)
      searchResults.value = []
    } finally {
      searching.value = false
      searched.value = true
    }
  }, 300)
}

function selectStock(stock) {
  form.value.code = stock.code
  form.value.name = stock.name
  form.value.asset_type = stock.type || guessAssetType(stock.code)
  searchKeyword.value = ''
  searchResults.value = []
  searched.value = false
  delete errors.value.code
  delete errors.value.name
}

/** 手动输入代码时按代码推断类型（5xxxxx / 1xx 基金 → ETF） */
function syncAssetType() {
  if (form.value.code) {
    form.value.asset_type = guessAssetType(form.value.code)
  }
}

// ---------- 校验与提交 ----------

/** 归一化：剥掉 sh/sz 前缀、去空白、转大写（与后端 market.normalize_code 同规则） */
function normalizeCode(raw) {
  let code = String(raw ?? '').trim()
  if (code.length > 6 && /^(sh|sz|bj)/i.test(code)) code = code.slice(2)
  return code.toUpperCase()
}

function validate() {
  errors.value = {}

  const code = normalizeCode(form.value.code)
  if (!/^\d{6}$/.test(code)) {
    errors.value.code = '请输入 6 位数字代码'
  }

  if (!String(form.value.name ?? '').trim()) {
    errors.value.name = '请输入标的名称'
  } else if (String(form.value.name).trim().length > 50) {
    errors.value.name = '名称不能超过 50 个字符'
  }

  const targetYield = Number(form.value.target_yield)
  if (form.value.target_yield === null || form.value.target_yield === '' || !Number.isFinite(targetYield)) {
    errors.value.target_yield = '请填写目标股息率'
  } else if (targetYield <= 0 || targetYield > 30) {
    errors.value.target_yield = '目标股息率需在 0 ~ 30 之间'
  }

  if (form.value.target_price !== null && form.value.target_price !== '') {
    const targetPrice = Number(form.value.target_price)
    if (!Number.isFinite(targetPrice) || targetPrice < 0) {
      errors.value.target_price = '目标价必须大于 0（留空表示未设）'
    }
  }

  return Object.keys(errors.value).length === 0
}

function handleSubmit() {
  if (!validate()) {
    const firstError = Object.values(errors.value)[0]
    if (firstError) toast.warning(`请检查表单：${firstError}`)
    return
  }

  const code = normalizeCode(form.value.code)
  const hasTargetPrice =
    form.value.target_price !== null &&
    form.value.target_price !== '' &&
    Number(form.value.target_price) > 0

  const payload = {
    name: String(form.value.name).trim(),
    asset_type: form.value.asset_type || '股票',
    target_yield: Number(form.value.target_yield),
    note: String(form.value.note ?? '').trim(),
    // 目标价：编辑时用 0 表达「清空」，新增时不传（后端按未设处理）
    target_price: hasTargetPrice ? Number(form.value.target_price) : (isEdit.value ? 0 : null),
  }
  if (!isEdit.value) {
    payload.code = code
  }

  emit('submit', payload)
}
</script>

<style scoped>
.wl-modal {
  max-width: 560px;
}

.wl-form-row {
  display: flex;
  gap: 16px;
}

.wl-form-row .form-group {
  flex: 1;
}

.wl-select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  font-size: 14px;
  background: var(--card-bg);
  color: var(--text-primary);
}

.wl-select:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.wl-search {
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px dashed var(--border-color);
}

.wl-search label {
  display: block;
  margin-bottom: 8px;
  font-weight: 500;
  font-size: 14px;
}

.wl-search-box {
  position: relative;
}

.wl-search-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  font-size: 14px;
}

.wl-search-input:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.wl-search-loading {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 12px;
  color: var(--text-secondary);
}

.wl-search-results {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  margin-top: 4px;
  max-height: 220px;
  overflow-y: auto;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  box-shadow: var(--shadow-md);
  z-index: 100;
}

.wl-search-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--bg-color);
}

.wl-search-item:last-child {
  border-bottom: none;
}

.wl-search-item:hover {
  background: #eff6ff;
}

.wl-stock-code {
  font-weight: 600;
  min-width: 64px;
  font-variant-numeric: tabular-nums;
}

.wl-stock-name {
  flex: 1;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wl-stock-type {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--bg-color);
  color: var(--text-secondary);
}

.wl-stock-market {
  font-size: 11px;
  color: var(--text-secondary);
}

.wl-search-empty {
  padding: 10px;
  text-align: center;
  font-size: 12px;
  color: var(--text-secondary);
}

.wl-search-divider {
  position: relative;
  margin-top: 14px;
  text-align: center;
}

.wl-search-divider::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--border-color);
}

.wl-search-divider span {
  position: relative;
  padding: 0 12px;
  background: var(--card-bg);
  font-size: 12px;
  color: var(--text-secondary);
}

textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  font-size: 14px;
  font-family: inherit;
  resize: vertical;
}

textarea:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
</style>
