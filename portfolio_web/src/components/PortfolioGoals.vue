<template>
  <section class="goal-section">
    <!-- 1. 标题栏 -->
    <div class="goal-header">
      <h2 class="goal-title">🎯 年收入目标</h2>
      <div class="goal-actions">
        <button type="button" class="btn btn-primary btn-sm" @click="$emit('add')">＋ 新增目标</button>
        <button
          type="button"
          class="btn btn-secondary btn-sm"
          :disabled="!selectedGoal"
          @click="handleEdit"
        >编辑</button>
        <button
          type="button"
          class="btn btn-danger btn-sm"
          :disabled="!selectedGoal"
          @click="handleRemove"
        >删除</button>
      </div>
    </div>

    <!-- 2. 目标下拉 -->
    <div class="goal-selector-row">
      <label for="goal-selector">目标：</label>
      <select
        id="goal-selector"
        class="goal-selector"
        :value="selectedGoalId ?? ''"
        :disabled="goals.length === 0"
        @change="handleSelectChange"
      >
        <option v-if="goals.length === 0" value="">暂无目标</option>
        <option v-for="goal in goals" :key="goal.id" :value="goal.id">{{ goal.name }}</option>
      </select>
    </div>

    <!-- 10. 加载态 -->
    <div v-if="loading" class="goal-loading">加载目标...</div>

    <template v-else>
      <!-- 9. 错误态 -->
      <div v-if="error" class="goal-error">
        <span>{{ error }}</span>
        <button type="button" class="btn btn-secondary btn-sm" @click="$emit('recalc', null)">重试</button>
      </div>

      <!-- 8. 空态 -->
      <div v-else-if="goals.length === 0" class="goal-empty">
        还没有目标，点击「＋ 新增目标」设置你的第一个年收入目标
      </div>

      <div v-else-if="progress" class="goal-body">
        <!-- 3. 进度条 -->
        <div class="goal-progress">
          <div class="goal-progress-track">
            <div
              class="goal-progress-fill"
              :class="{ 'is-achieved': isAchieved, 'is-loading': progressLoading }"
              :style="{ width: progressPercentWidth(progress.progress_percent) + '%' }"
            ></div>
          </div>
          <div class="goal-progress-text" :class="{ 'is-achieved': isAchieved }">
            <template v-if="isAchieved">✅ 已达成</template>
            <template v-else>已完成 {{ progressPercentText }}%</template>
          </div>
        </div>

        <!-- 4. 数字网格 -->
        <div class="goal-grid">
          <div class="goal-item">
            <div class="goal-label">当前年收入</div>
            <div class="goal-value">¥{{ formatAmount(progress.current_annual_income) }}</div>
            <div class="goal-sub">月均 ¥{{ formatAmount(progress.current_monthly_income) }}</div>
          </div>
          <div class="goal-item">
            <div class="goal-label">目标年收入</div>
            <div class="goal-value">¥{{ formatAmount(progress.target_annual_income) }}</div>
            <div class="goal-sub">月均 ¥{{ formatAmount(progress.target_monthly_income) }}</div>
          </div>
          <div class="goal-item">
            <div class="goal-label">缺口</div>
            <template v-if="isAchieved">
              <div class="goal-value">—</div>
            </template>
            <template v-else>
              <div class="goal-value">¥{{ formatAmount(progress.gap_annual) }}</div>
              <div class="goal-sub">月均 ¥{{ formatAmount(progress.gap_monthly) }}</div>
            </template>
          </div>
          <div class="goal-item" :class="{ 'is-achieved': isAchieved }">
            <div class="goal-label">完成度</div>
            <div class="goal-value">{{ progressPercentText }}%</div>
          </div>
        </div>

        <!-- 5. 达成路径 -->
        <div v-if="isAchieved" class="goal-surplus">
          已超出目标 ¥{{ formatAmount(progress.surplus_annual) }}/年（月均 ¥{{ formatAmount(progress.surplus_monthly) }}）
        </div>
        <div v-else class="goal-paths">
          <div class="goal-path">
            <div class="goal-path-label">① 收益率不变，还需再投入本金</div>
            <div class="goal-path-value">
              <template v-if="hasAdditionalCapital">¥{{ formatAmount(progress.additional_capital) }}</template>
              <template v-else>—</template>
            </div>
            <!-- 给不出金额时，把 calc_note 里的原因就地说明（不再重复灰色提示行） -->
            <div v-if="!hasAdditionalCapital && calcNote" class="goal-path-sub">{{ calcNote }}</div>
          </div>
          <div class="goal-path">
            <div class="goal-path-label">
              ② 本金不变，TTM 收益率需达到
              <InfoTooltip :text="CURRENT_YIELD_TOOLTIP" />
            </div>
            <div class="goal-path-value">{{ formatYieldPercent(progress.required_yield_percent) }}</div>
            <div v-if="hasYieldComparison" class="goal-path-sub">
              <template v-if="yieldGapPoints > 0">
                当前 {{ formatYieldPercent(progress.current_yield_percent) }}，还需提升 {{ yieldGapPoints.toFixed(2) }} 个百分点
              </template>
              <template v-else>当前已不低于该水平</template>
            </div>
          </div>
        </div>

        <!-- 6. 假设收益率输入 -->
        <div class="goal-assumed">
          <div class="goal-assumed-label">
            假设加仓收益率
            <InfoTooltip :text="ASSUMED_YIELD_TOOLTIP" />
          </div>
          <div class="goal-assumed-field">
            <div class="goal-assumed-input" :class="{ 'has-error': !!assumedError }">
              <input
                id="goal-assumed-yield"
                :value="assumedYieldInput"
                type="number"
                step="0.01"
                min="0"
                max="100"
                inputmode="decimal"
                placeholder="默认按加权 TTM"
                @input="handleAssumedYieldInput"
              />
              <span class="goal-assumed-suffix">%</span>
            </div>
            <div v-if="assumedError" class="goal-assumed-error">{{ assumedError }}</div>
            <div v-else class="goal-assumed-hint">默认取当前组合的加权平均 TTM 收益率</div>
          </div>
        </div>

        <!-- 7. 提示区 -->
        <div v-if="progress.has_mock_price" class="goal-notice goal-notice-warning">
          当前组合含模拟价格（网络拉取失败兜底），目标进度仅供参考
        </div>
        <div v-if="showCalcNote" class="goal-notice">{{ calcNote }}</div>
      </div>

      <div v-else class="goal-loading">加载目标进度...</div>
    </template>
  </section>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import InfoTooltip from './InfoTooltip.vue'
import { formatAmount } from '../utils/format.js'
import { formatYieldPercent, progressPercentWidth } from '../utils/goal.js'

const props = defineProps({
  goals: {
    type: Array,
    default: () => []
  },
  selectedGoalId: {
    type: Number,
    default: null
  },
  progress: {
    type: Object,
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  },
  progressLoading: {
    type: Boolean,
    default: false
  },
  error: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update:selectedGoalId', 'add', 'edit', 'remove', 'recalc'])

// 假设收益率输入的工具提示：说明默认值来源
const ASSUMED_YIELD_TOOLTIP =
  '默认取当前组合的加权平均 TTM 收益率；改这里可以按你打算买入标的的收益率重算所需本金'
// D3/D14：同屏的「当前收益率」与「加权 TTM 收益率」口径不同，必须解释清楚，避免用户以为算错了
const CURRENT_YIELD_TOOLTIP =
  '「当前收益率」= 当前年收入 ÷ 当前持仓市值 × 100；与概览卡里按市值加权的「加权平均 TTM 收益率」口径不同，两者数值可能不一致。加权 TTM 只作为「假设加仓收益率」的默认值来源，不参与进度与缺口计算。'

const selectedGoal = computed(
  () => props.goals.find((g) => String(g.id) === String(props.selectedGoalId)) || null
)

const isAchieved = computed(() => !!props.progress?.is_achieved)

const progressPercentText = computed(() => {
  const v = Number(props.progress?.progress_percent)
  return Number.isFinite(v) ? v.toFixed(2) : '0.00'
})

const hasAdditionalCapital = computed(() => {
  const v = props.progress?.additional_capital
  return v !== null && v !== undefined && Number.isFinite(Number(v))
})

const calcNote = computed(() => {
  const v = props.progress?.calc_note
  return typeof v === 'string' ? v.trim() : ''
})

// 路径 ① 已在正文里把 calc_note 当作「为什么给不出金额」的原因展示时，
// 底部不再重复一整行灰色提示（spec §7.4 第 7 条）
const showCalcNote = computed(() => {
  if (!calcNote.value) return false
  const usedInPath1 = !isAchieved.value && !hasAdditionalCapital.value
  return !usedInPath1
})

const yieldGapPoints = computed(() => {
  const v = Number(props.progress?.yield_gap_points)
  return Number.isFinite(v) ? v : 0
})

const hasYieldComparison = computed(() => {
  const p = props.progress
  if (!p) return false
  const nums = [p.required_yield_percent, p.current_yield_percent, p.yield_gap_points]
  return nums.every((v) => v !== null && v !== undefined && Number.isFinite(Number(v)))
})

function handleSelectChange(event) {
  const raw = event.target.value
  const hit = props.goals.find((g) => String(g.id) === String(raw))
  emit('update:selectedGoalId', hit ? hit.id : null)
}

function handleEdit() {
  if (!selectedGoal.value) return
  emit('edit', selectedGoal.value)
}

function handleRemove() {
  const goal = selectedGoal.value
  if (!goal) return
  // 与 AssetList 删资产的交互保持一致
  if (!confirm(`确定要删除目标「${goal.name}」吗？`)) return
  emit('remove', goal)
}

// ---------- 假设加仓收益率输入 ----------
// D5：手填值不持久化。getter 未 dirty 时跟随 progress（后端返回的实际采用值），
// 用户改动后 300ms 防抖只发一次请求；清空则立刻回到后端默认值。
const assumedYieldInput = ref('')
const assumedDirty = ref(false)
const assumedError = ref('')
let assumedTimer = null

function clearAssumedTimer() {
  if (assumedTimer) {
    clearTimeout(assumedTimer)
    assumedTimer = null
  }
}

function syncAssumedFromProgress() {
  const v = props.progress?.assumed_yield_percent
  if (v === null || v === undefined || !Number.isFinite(Number(v))) {
    assumedYieldInput.value = ''
    return
  }
  assumedYieldInput.value = Number(v).toFixed(2)
}

// 切换目标（goal_id 变化）→ dirty 复位，输入框回到新目标的默认值
watch(
  () => props.progress?.goal_id,
  () => {
    clearAssumedTimer()
    assumedDirty.value = false
    assumedError.value = ''
    syncAssumedFromProgress()
  },
  { immediate: true }
)

// 只在未 dirty 时随 progress 同步（用户手填的值不被请求回来的默认值覆盖）
watch(
  () => props.progress?.assumed_yield_percent,
  () => {
    if (assumedDirty.value) return
    syncAssumedFromProgress()
  }
)

function handleAssumedYieldInput(event) {
  const raw = event.target.value
  assumedYieldInput.value = raw
  assumedError.value = ''

  // 清空 → 立即回到后端默认值（不加防抖）
  if (raw === '' || raw === null || raw === undefined) {
    clearAssumedTimer()
    assumedDirty.value = false
    emit('recalc', null)
    return
  }

  const value = Number(raw)
  // 非法输入：描红 + 本地提示，不发请求
  if (!Number.isFinite(value) || value < 0 || value > 100) {
    clearAssumedTimer()
    assumedDirty.value = true
    assumedError.value = '请输入 0 ~ 100 之间的数字'
    return
  }

  assumedDirty.value = true
  clearAssumedTimer()
  assumedTimer = setTimeout(() => {
    assumedTimer = null
    emit('recalc', value)
  }, 300)
}

onBeforeUnmount(clearAssumedTimer)
</script>

<style scoped>
.goal-section {
  background: var(--card-bg);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px;
  margin-bottom: 24px;
}

.goal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}

.goal-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.goal-actions {
  display: flex;
  gap: 8px;
}

.goal-actions .btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 目标下拉 */
.goal-selector-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.goal-selector-row label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}

.goal-selector {
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  font-size: 14px;
  background: var(--card-bg);
  color: var(--text-primary);
  cursor: pointer;
  min-width: 200px;
}

.goal-selector:disabled {
  cursor: not-allowed;
  color: var(--text-secondary);
}

.goal-selector:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

/* 状态区 */
.goal-loading {
  padding: 24px;
  text-align: center;
  color: var(--text-secondary);
}

.goal-empty {
  padding: 32px 20px;
  text-align: center;
  color: var(--text-secondary);
}

.goal-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 20px;
  color: var(--danger-color);
}

/* 进度条 */
.goal-progress {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.goal-progress-track {
  flex: 1;
  height: 12px;
  background: var(--bg-color);
  border: 1px solid var(--border-color);
  border-radius: 999px;
  overflow: hidden;
}

.goal-progress-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--primary-color), var(--primary-light));
  transition: width 0.3s ease;
}

/* 达成绿：与红涨绿跌的「盈亏」语义无关，仅表示「目标已达成」 */
.goal-progress-fill.is-achieved {
  background: linear-gradient(90deg, #059669, #34d399);
}

.goal-progress-fill.is-loading {
  opacity: 0.6;
}

.goal-progress-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
}

.goal-progress-text.is-achieved {
  color: #059669;
}

/* 数字网格 */
.goal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.goal-item {
  padding: 16px;
  background: linear-gradient(135deg, var(--primary-color), var(--primary-light));
  border-radius: var(--radius);
  color: white;
  container-type: inline-size;
}

.goal-item.is-achieved {
  background: linear-gradient(135deg, #059669, #34d399);
}

.goal-label {
  font-size: 13px;
  opacity: 0.9;
  margin-bottom: 4px;
}

/* 与 .overview-item .value 同思路：按卡片宽度缩放，金额不折行 */
.goal-value {
  font-size: clamp(15px, 8.5cqw, 24px);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  line-height: 1.35;
  font-weight: 700;
}

.goal-sub {
  font-size: 12px;
  opacity: 0.8;
  margin-top: 4px;
}

/* 达成路径 */
.goal-paths {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.goal-path {
  padding: 14px 16px;
  background: var(--bg-color);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}

.goal-path-label {
  display: flex;
  align-items: center;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.goal-path-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--primary-color);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.goal-path-sub {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 6px;
}

.goal-surplus {
  padding: 14px 16px;
  margin-bottom: 16px;
  border-radius: var(--radius);
  background: rgba(5, 150, 105, 0.1);
  border: 1px solid rgba(5, 150, 105, 0.3);
  color: #059669;
  font-size: 14px;
  font-weight: 600;
}

/* 假设加仓收益率 */
.goal-assumed {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.goal-assumed-label {
  display: flex;
  align-items: center;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  padding-top: 8px;
}

.goal-assumed-field {
  flex: 1;
  min-width: 220px;
}

.goal-assumed-input {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  background: var(--card-bg);
  padding: 0 10px 0 0;
}

.goal-assumed-input.has-error {
  border-color: var(--danger-color);
}

.goal-assumed-input input {
  width: 120px;
  padding: 8px 10px;
  border: none;
  font-size: 14px;
  background: transparent;
  color: var(--text-primary);
}

.goal-assumed-input input:focus {
  outline: none;
}

.goal-assumed-suffix {
  color: var(--text-secondary);
  font-size: 13px;
}

.goal-assumed-hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.goal-assumed-error {
  font-size: 12px;
  color: var(--danger-color);
  margin-top: 4px;
}

/* 提示区 */
.goal-notice {
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--bg-color);
  color: var(--text-secondary);
  font-size: 13px;
  margin-top: 8px;
}

.goal-notice-warning {
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.35);
  color: #b45309;
}
</style>
