<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal goal-modal">
      <div class="modal-header">
        <h2>{{ isEdit ? '编辑目标' : '新增目标' }}</h2>
        <button class="modal-close" @click="$emit('close')">&times;</button>
      </div>

      <form @submit.prevent="handleSubmit" novalidate>
        <div class="modal-body">
          <div class="form-group">
            <label>目标名称 *</label>
            <input
              v-model="form.name"
              type="text"
              maxlength="50"
              placeholder="例如：生活自由线"
              required
            />
            <div v-if="errors.name" class="error">{{ errors.name }}</div>
          </div>

          <div class="form-group">
            <label>目标年收入 (元) *</label>
            <input
              v-model="form.annual_income_target"
              type="number"
              min="0.01"
              step="0.01"
              placeholder="例如：120000"
              required
            />
            <div v-if="errors.annual_income_target" class="error">
              {{ errors.annual_income_target }}
            </div>
            <div v-else-if="monthlyPreview !== null" class="form-hint">
              ≈ ¥{{ formatAmount(monthlyPreview) }} / 月
            </div>
          </div>
        </div>

        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" @click="$emit('close')">取消</button>
          <button type="submit" class="btn btn-primary" :disabled="submitting">
            {{ submitting ? '保存中...' : '保存' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { formatAmount } from '../utils/format.js'
import { toMonthly } from '../utils/goal.js'

const props = defineProps({
  /** 编辑时传入既有目标，新增时为 null */
  goal: {
    type: Object,
    default: null
  },
  submitting: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close', 'submit'])

const isEdit = computed(() => !!props.goal)

const form = ref({
  name: '',
  annual_income_target: null
})

const errors = ref({})

/** 实时预览：只有填了合法金额才显示「≈ ¥X.XX / 月」 */
const monthlyPreview = computed(() => {
  const v = Number(form.value.annual_income_target)
  if (!Number.isFinite(v) || v <= 0) return null
  return toMonthly(v)
})

watch(() => props.goal, (goal) => {
  if (goal) {
    form.value = {
      name: goal.name ?? '',
      annual_income_target: goal.annual_income_target ?? null
    }
  } else {
    form.value = { name: '', annual_income_target: null }
  }
  errors.value = {}
}, { immediate: true })

function validate() {
  errors.value = {}

  const name = String(form.value.name ?? '').trim()
  if (!name) {
    errors.value.name = '请输入目标名称'
  } else if (name.length > 50) {
    errors.value.name = '目标名称不能超过 50 个字'
  }

  const target = Number(form.value.annual_income_target)
  if (form.value.annual_income_target === null || form.value.annual_income_target === '' || !Number.isFinite(target)) {
    errors.value.annual_income_target = '请输入目标年收入'
  } else if (target <= 0) {
    errors.value.annual_income_target = '目标年收入必须大于 0'
  }

  return Object.keys(errors.value).length === 0
}

function handleSubmit() {
  // 校验失败只在字段下方显示红字，不发起请求
  if (!validate()) return

  emit('submit', {
    name: String(form.value.name).trim(),
    annual_income_target: Number(form.value.annual_income_target)
  })
}
</script>

<style scoped>
.goal-modal {
  max-width: 480px;
}
</style>
