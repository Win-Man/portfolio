<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal portfolio-modal">
      <div class="modal-header">
        <h2>{{ isEdit ? '重命名组合' : '新增组合' }}</h2>
        <button class="modal-close" @click="$emit('close')">&times;</button>
      </div>

      <form @submit.prevent="handleSubmit" novalidate>
        <div class="modal-body">
          <div class="form-group">
            <label>组合名称 *</label>
            <input
              ref="nameInput"
              v-model="form.name"
              type="text"
              maxlength="50"
              placeholder="例如：养老金 / 孩子教育金"
              required
            />
            <div v-if="errors.name" class="error">{{ errors.name }}</div>
            <div v-else class="form-hint">名称仅用于标识，可以与其它组合重名</div>
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
import { ref, computed, watch, onMounted, nextTick } from 'vue'

const props = defineProps({
  /** 重命名时传入既有组合，新增时为 null */
  portfolio: {
    type: Object,
    default: null
  },
  submitting: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close', 'submit'])

const isEdit = computed(() => !!props.portfolio)

const nameInput = ref(null)

const form = ref({ name: '' })
const errors = ref({})

watch(() => props.portfolio, (portfolio) => {
  form.value = { name: portfolio?.name ?? '' }
  errors.value = {}
}, { immediate: true })

// 弹窗一打开就聚焦输入框，并选中已有名称方便直接改写
onMounted(async () => {
  await nextTick()
  const el = nameInput.value
  if (!el) return
  el.focus()
  if (isEdit.value) el.select()
})

function validate() {
  errors.value = {}
  const name = String(form.value.name ?? '').trim()
  if (!name) {
    errors.value.name = '请输入组合名称'
  } else if (name.length > 50) {
    errors.value.name = '组合名称不能超过 50 个字'
  }
  return Object.keys(errors.value).length === 0
}

function handleSubmit() {
  // 校验失败只在字段下方显示红字，不发起请求
  if (!validate()) return
  emit('submit', { name: String(form.value.name).trim() })
}
</script>

<style scoped>
.portfolio-modal {
  max-width: 440px;
}
</style>
