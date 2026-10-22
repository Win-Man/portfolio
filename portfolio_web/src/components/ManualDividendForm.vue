<template>
  <form class="manual-form" novalidate @submit.prevent="handleSubmit">
    <div class="manual-field">
      <label for="manual-asset">标的</label>
      <select id="manual-asset" v-model="form.code" :disabled="assets.length === 0">
        <option v-if="assets.length === 0" value="">组合内暂无可选标的</option>
        <option v-for="asset in assets" :key="asset.id ?? asset.code" :value="asset.code">
          {{ asset.name || asset.code }}（{{ asset.code }}）
        </option>
      </select>
      <div v-if="errors.code" class="error">{{ errors.code }}</div>
    </div>

    <div class="manual-field">
      <label for="manual-date">派息日</label>
      <input id="manual-date" v-model="form.ex_date" type="date" :max="today" />
      <div v-if="errors.ex_date" class="error">{{ errors.ex_date }}</div>
    </div>

    <div class="manual-field">
      <label for="manual-amount">每股金额 (元)</label>
      <input
        id="manual-amount"
        v-model="form.amount_per_share"
        type="number"
        step="0.0001"
        min="0"
        inputmode="decimal"
        placeholder="例如：0.5"
      />
      <div v-if="errors.amount_per_share" class="error">{{ errors.amount_per_share }}</div>
    </div>

    <div class="manual-field">
      <label for="manual-note">备注</label>
      <input
        id="manual-note"
        v-model="form.note"
        type="text"
        maxlength="100"
        placeholder="选填，例如：场外基金分红"
      />
    </div>

    <div class="manual-actions">
      <button type="button" class="btn btn-secondary btn-sm" :disabled="submitting" @click="$emit('cancel')">
        取消
      </button>
      <button
        type="submit"
        class="btn btn-primary btn-sm"
        :disabled="submitting || assets.length === 0"
      >
        {{ submitting ? '保存中...' : '保存' }}
      </button>
    </div>
  </form>
</template>

<script setup>
/**
 * 月 Sheet 里的**行内**补录表单（spec §7.4.5，不是 Modal）。
 *
 * 校验口径与后端一致：
 *   - 金额 > 0（后端 422）
 *   - 派息日 ≤ 今天（后端 422「不能补未来日期；预告请等待 akshare 收录」）
 * 校验失败只在本行显示红字，不发请求；提交态由父组件的 `submitting` 控制。
 */
import { computed, ref, watch } from 'vue'

const props = defineProps({
  /** 当前组合的资产列表（来自 api/portfolio.js 的 getAssets） */
  assets: {
    type: Array,
    default: () => [],
  },
  /** 父组件正在提交（按钮转 busy，防重复提交） */
  submitting: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['submit', 'cancel'])

const form = ref({
  code: '',
  ex_date: '',
  amount_per_share: '',
  note: '',
})
const errors = ref({})

/** 本机今天的 `YYYY-MM-DD`（不用 toISOString，避免时区把日期挪一天） */
const today = computed(() => {
  const d = new Date()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${month}-${day}`
})

// 默认选中第一个标的（组合内必须存在该 code，否则后端 422）
watch(
  () => props.assets,
  (list) => {
    if (form.value.code) return
    if (Array.isArray(list) && list.length > 0) form.value.code = list[0].code
  },
  { immediate: true }
)

function validate() {
  errors.value = {}

  if (!form.value.code) errors.value.code = '请选择标的'

  const amount = Number(form.value.amount_per_share)
  if (
    form.value.amount_per_share === '' ||
    form.value.amount_per_share === null ||
    form.value.amount_per_share === undefined ||
    !Number.isFinite(amount)
  ) {
    errors.value.amount_per_share = '请输入每股金额'
  } else if (amount <= 0) {
    errors.value.amount_per_share = '每股金额必须大于 0'
  }

  const date = String(form.value.ex_date ?? '').trim()
  if (!date) {
    errors.value.ex_date = '请选择派息日'
  } else if (date > today.value) {
    errors.value.ex_date = '不能补未来日期；预告请等待 akshare 收录'
  }

  return Object.keys(errors.value).length === 0
}

function handleSubmit() {
  if (props.submitting) return
  if (!validate()) return
  emit('submit', {
    code: form.value.code,
    ex_date: form.value.ex_date,
    amount_per_share: Number(form.value.amount_per_share),
    note: String(form.value.note ?? '').trim(),
  })
}
</script>

<style scoped>
.manual-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px 12px;
  align-items: start;
  margin-bottom: 12px;
  padding: 12px;
  background: var(--bg-color, #f8fafc);
  border: 1px dashed var(--border-color, #e2e8f0);
  border-radius: var(--radius, 8px);
}

.manual-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.manual-field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary, #64748b);
}

.manual-field input,
.manual-field select {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: var(--radius, 8px);
  font-size: 13px;
  background: var(--card-bg, #fff);
  color: var(--text-primary, #1e293b);
}

.manual-field input:focus,
.manual-field select:focus {
  outline: none;
  border-color: var(--primary-color, #2563eb);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.manual-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  /* 与第一个字段的 label 高度对齐 */
  padding-top: 18px;
}

@media (max-width: 640px) {
  .manual-actions {
    padding-top: 0;
    grid-column: 1 / -1;
  }
}
</style>
