<template>
  <!--
    紧凑卡片：左侧环形进度（完成度），右侧「当前 / 目标 / 缺口 / 达成路径」四行摘要。
    四块数字不再各占一张彩色卡片 —— 原布局 4 张卡要占 100px 高、还要各自重复
    「月均」副标题，信息密度低且把这一栏撑得很高。现在与右侧两张饼图同处一行。
  -->
  <section class="goal-card">
    <!-- 1. 标题行：与右侧饼图卡片标题同字号（13px / 600），视觉上是一条对齐的横线 -->
    <div class="goal-head">
      <span class="goal-title">🎯 年收入目标</span>
      <div class="goal-actions">
        <button type="button" class="btn btn-ghost btn-sm" title="新增目标" @click="$emit('add')">
          ＋ 新增
        </button>
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          :disabled="!selectedGoal"
          @click="handleEdit"
        >编辑</button>
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          :disabled="!selectedGoal"
          @click="handleRemove"
        >删除</button>
      </div>
    </div>

    <!-- 加载态 -->
    <div v-if="loading" class="goal-state">加载目标...</div>

    <template v-else>
      <!-- 错误态 -->
      <div v-if="error" class="goal-state goal-error">
        <span>{{ error }}</span>
        <button type="button" class="btn btn-secondary btn-sm" @click="$emit('recalc', null)">重试</button>
      </div>

      <!-- 空态 -->
      <div v-else-if="goals.length === 0" class="goal-state goal-empty">
        还没有目标，点「＋ 新增」设置第一个年收入目标
      </div>

      <div v-else-if="progress" class="goal-body">
        <div class="goal-main">
          <!-- 2. 环形进度：完成度是这张卡的「主数值」，环内只放百分数，不再单开一张卡 -->
          <div class="goal-ring" :class="{ 'is-achieved': isAchieved }">
            <svg viewBox="0 0 100 100" aria-hidden="true">
              <circle class="ring-track" cx="50" cy="50" r="42" />
              <circle
                v-if="ringPercent > 0"
                class="ring-fill"
                cx="50"
                cy="50"
                r="42"
                :stroke-dasharray="RING_CIRCUMFERENCE"
                :stroke-dashoffset="ringOffset"
              />
            </svg>
            <span class="ring-text" :style="{ fontSize: ringFontSize }">{{ progressPercentText }}%</span>
          </div>

          <div class="goal-facts">
            <!-- 3. 目标名：多目标时是可切换下拉，单目标时退化为纯文本（对齐参考图的标题） -->
            <div class="goal-name-row">
              <svg class="goal-flag" viewBox="0 0 16 16" aria-hidden="true">
                <path v-for="(d, i) in iconPaths('flag')" :key="i" :d="d" />
              </svg>
              <select
                v-if="goals.length > 1"
                id="goal-selector"
                class="goal-name-select"
                aria-label="选择目标"
                :value="selectedGoalId ?? ''"
                @change="handleSelectChange"
              >
                <option v-for="goal in goals" :key="goal.id" :value="goal.id">{{ goal.name }}</option>
              </select>
              <span v-else class="goal-name" :title="progress.goal_name">{{ progress.goal_name }}</span>
              <span v-if="isAchieved" class="goal-badge">已达成</span>
              <span v-if="progressLoading" class="goal-pending">更新中</span>
            </div>

            <!-- 4. 四行摘要：标签灰、数值黑粗、「月均」灰小字 -->
            <ul class="fact-list">
              <li class="fact">
                <svg class="fact-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path v-for="(d, i) in iconPaths('check')" :key="i" :d="d" />
                </svg>
                <span class="fact-label">当前</span>
                <span class="fact-value">¥{{ formatAmount(progress.current_annual_income) }}</span>
                <span class="fact-sub">月均 ¥{{ formatAmount(progress.current_monthly_income) }}</span>
              </li>

              <li class="fact">
                <svg class="fact-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path v-for="(d, i) in iconPaths('target')" :key="i" :d="d" />
                </svg>
                <span class="fact-label">目标</span>
                <span class="fact-value">¥{{ formatAmount(progress.target_annual_income) }}</span>
                <span class="fact-sub">月均 ¥{{ formatAmount(progress.target_monthly_income) }}</span>
              </li>

              <li v-if="!isAchieved" class="fact">
                <svg class="fact-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path v-for="(d, i) in iconPaths('gap')" :key="i" :d="d" />
                </svg>
                <span class="fact-label">缺口</span>
                <span class="fact-value">¥{{ formatAmount(progress.gap_annual) }}</span>
                <span class="fact-sub">月均 ¥{{ formatAmount(progress.gap_monthly) }}</span>
              </li>

              <!-- 5. 只靠股息复投：与「缺口」并排，是第三条达成路径，但只给一个年数 -->
              <li v-if="!isAchieved" class="fact">
                <svg class="fact-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path v-for="(d, i) in iconPaths('reinvest')" :key="i" :d="d" />
                </svg>
                <span class="fact-label">
                  复投
                  <InfoTooltip :text="reinvestTooltip" />
                </span>
                <span class="fact-value">{{ reinvestYearsText }}</span>
                <span class="fact-sub">{{ reinvestSubText }}</span>
              </li>

              <!-- 6. 达成路径：本金与收益率两种解法压在同一行，像一句人话而不是两张卡 -->
              <li class="fact fact-path">
                <svg class="fact-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path v-for="(d, i) in iconPaths('up')" :key="i" :d="d" />
                </svg>
                <span class="fact-text">
                  <template v-if="isAchieved">已超出目标 <b>¥{{ formatAmount(progress.surplus_annual) }}</b>/年（月均 ¥{{ formatAmount(progress.surplus_monthly) }}）</template>
                  <!--
                    两句解法必须紧贴（中间不能有换行缩进，否则 Vue 会补一个空格，
                    读成「本金， 或 整体息率」）；末尾「即可实现目标 + 图标」整体 nowrap，
                    避免折行时只剩「可实现目标」被甩到下一行。
                  -->
                  <template v-else><template v-if="hasAdditionalCapital">再投入 <b>¥{{ formatAmount(progress.additional_capital) }}</b> 本金，或</template><template v-if="hasRequiredYield">整体息率达 <b>{{ formatYieldPercent(progress.required_yield_percent) }}</b> <span class="fact-tail">即可实现目标<InfoTooltip :text="CURRENT_YIELD_TOOLTIP" /></span></template><template v-else>—</template></template>
                </span>
              </li>
            </ul>
          </div>
        </div>

        <!-- 7. 假设加仓收益率：驱动「再投入本金」与「股息复投」两项，收成卡片底部一行 -->
        <div class="goal-assumed">
          <span class="goal-assumed-label">
            假设加仓收益率
            <InfoTooltip :text="ASSUMED_YIELD_TOOLTIP" />
          </span>
          <span class="goal-assumed-input" :class="{ 'has-error': !!assumedError }">
            <input
              id="goal-assumed-yield"
              :value="assumedYieldInput"
              type="number"
              step="0.01"
              min="0"
              max="100"
              inputmode="decimal"
              aria-label="假设加仓收益率"
              placeholder="默认"
              @input="handleAssumedYieldInput"
            />
            <span class="goal-assumed-suffix">%</span>
          </span>
          <span v-if="assumedError" class="goal-assumed-error">{{ assumedError }}</span>
          <span v-else class="goal-assumed-hint">留空 = 加权 TTM</span>
        </div>

        <!-- 8. 提示区 -->
        <div v-if="progress.has_mock_price" class="goal-notice goal-notice-warning">
          含模拟价格，进度仅供参考
        </div>
        <div v-if="showCalcNote" class="goal-notice">{{ calcNote }}</div>
      </div>

      <div v-else class="goal-state">加载目标进度...</div>
    </template>
  </section>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import InfoTooltip from './InfoTooltip.vue'
import { formatAmount } from '../utils/format.js'
import { formatYieldPercent, formatYears, progressPercentWidth } from '../utils/goal.js'

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
  '「整体息率」= 目标年收入 ÷ 当前持仓市值 × 100；与概览卡里按市值加权的「加权平均 TTM 收益率」口径不同，两者数值可能不一致。加权 TTM 只作为「假设加仓收益率」的默认值来源，不参与进度与缺口计算。'

/**
 * 行内图标（16×16 线性图标，统一 1.4 描边）。
 * 用 path 数组而不是内联四段 svg，模板里只留一行 v-for，
 * 四行的图标风格才会完全一致（线宽、圆心、半径）。
 */
const ICONS = {
  check: ['M8 1.6a6.4 6.4 0 1 0 0 12.8A6.4 6.4 0 0 0 8 1.6z', 'M5.1 8.2l2.1 2.1 3.8-4.4'],
  target: ['M8 1.6a6.4 6.4 0 1 0 0 12.8A6.4 6.4 0 0 0 8 1.6z', 'M8 5.3a2.7 2.7 0 1 0 0 5.4 2.7 2.7 0 0 0 0-5.4z'],
  gap: ['M8 1.6a6.4 6.4 0 1 0 0 12.8A6.4 6.4 0 0 0 8 1.6z', 'M5.2 8h5.6'],
  up: ['M8 1.6a6.4 6.4 0 1 0 0 12.8A6.4 6.4 0 0 0 8 1.6z', 'M8 11.2V5.1', 'M5.7 7.4L8 5.1l2.3 2.3'],
  // 复投：环形箭头（不给外圈，14px 下双圆会糊成一团）
  reinvest: ['M12.6 8.4a4.6 4.6 0 1 1-1.4-3.6', 'M11.9 2.3v2.9H9'],
  flag: ['M3.9 1.7v12.6', 'M3.9 2.5h7.8l-1.7 2.8 1.7 2.8H3.9']
}

function iconPaths(name) {
  return ICONS[name] || []
}

// 环的几何：r=42、stroke-width=10 → 直径 94，留 3px 防描边被 viewBox 裁掉
const RING_CIRCUMFERENCE = 2 * Math.PI * 42

const selectedGoal = computed(
  () => props.goals.find((g) => String(g.id) === String(props.selectedGoalId)) || null
)

const isAchieved = computed(() => !!props.progress?.is_achieved)

const progressPercentText = computed(() => {
  const v = Number(props.progress?.progress_percent)
  return Number.isFinite(v) ? v.toFixed(2) : '0.00'
})

/** 环的填充比例：超额封顶 100%，环画不出更长（数字仍显示原值） */
const ringPercent = computed(() => progressPercentWidth(props.progress?.progress_percent))

const ringOffset = computed(
  () => RING_CIRCUMFERENCE * (1 - Math.min(100, Math.max(0, ringPercent.value)) / 100)
)

/** 环内文字长度不定（4.10% / 126.60%）：按字符数缩字号，避免撑出环外 */
const ringFontSize = computed(() => {
  const len = progressPercentText.value.length + 1
  if (len <= 5) return '19px'
  if (len <= 6) return '16px'
  return '13px'
})

const hasAdditionalCapital = computed(() => {
  const v = props.progress?.additional_capital
  return v !== null && v !== undefined && Number.isFinite(Number(v))
})

const hasRequiredYield = computed(() => {
  const v = props.progress?.required_yield_percent
  return v !== null && v !== undefined && Number.isFinite(Number(v))
})

// ---------- 路径 ③：只靠股息复投（后端算好年数，这里只管怎么读） ----------
const reinvestYearsText = computed(() => formatYears(props.progress?.reinvest_years))

/**
 * 副标题：能算 → 「第 N 年达标」；算不出 → 后端给的短原因（「当前无股息收入」等）。
 * 年数超过 100 时主数值已退化成「100 年以上」，再标「第 N 年」只会假装精确，索性不写。
 */
const reinvestSubText = computed(() => {
  const note = typeof props.progress?.reinvest_note === 'string' ? props.progress.reinvest_note.trim() : ''
  if (note) return note
  const years = Number(props.progress?.reinvest_years)
  if (Number.isFinite(years) && years >= 100) return ''
  const ceil = Number(props.progress?.reinvest_years_ceil)
  if (!Number.isFinite(ceil) || ceil <= 0) return ''
  return `第 ${ceil} 年达标`
})

const reinvestTooltip = computed(() => {
  const y = formatYieldPercent(props.progress?.assumed_yield_percent)
  return (
    `假设每年股息全部按 ${y} 再买入、不追加新本金，年收入就按 ${y} 逐年复利增长；` +
    `年数 = ln(目标 ÷ 当前) ÷ ln(1 + 收益率)。收益率取卡片底部的「假设加仓收益率」` +
    `（默认即组合加权 TTM），改它会连这条路径一起变。`
  )
})

const calcNote = computed(() => {
  const v = props.progress?.calc_note
  return typeof v === 'string' ? v.trim() : ''
})

// 达成路径不再内嵌 calc_note（它已压成一行短句），原因单独一行灰字展示
const showCalcNote = computed(() => !!calcNote.value && !isAchieved.value)

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
/*
  与右侧两张饼图卡片同处一行，宽度只有原来的 1/3：
  所以外框、字号、间距一律按「窄卡」设计，且卡片自身撑满行高
  （height:100%），三张卡的上/下边缘才会齐平。
*/
.goal-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  padding: 12px 14px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  /* 供下方容器查询用：卡片被拉宽到整行时才把摘要行切成两列 */
  container-type: inline-size;
  container-name: goalcard;
}

/* ---------- 标题行 ---------- */
.goal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.goal-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.goal-actions {
  display: flex;
  gap: 2px;
}

/* 标题行里的按钮比 btn-sm 再小一号：4 个元素要塞进 1/3 栏宽 */
.goal-actions .btn {
  padding: 2px 6px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}

.goal-actions .btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ---------- 状态区 ---------- */
.goal-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px 8px;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary);
}

.goal-error {
  color: var(--danger-color);
}

.goal-empty {
  line-height: 1.7;
}

/* ---------- 主体 ---------- */
.goal-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
  min-width: 0;
  margin-top: 10px;
}

.goal-main {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

/* 环形进度 */
.goal-ring {
  position: relative;
  flex: 0 0 auto;
  width: 100px;
  height: 100px;
}

.goal-ring svg {
  width: 100%;
  height: 100%;
  /* 进度从 12 点方向开始 */
  transform: rotate(-90deg);
}

.ring-track {
  fill: none;
  stroke: #eef2f7;
  stroke-width: 10;
}

.ring-fill {
  fill: none;
  stroke: var(--primary-color);
  stroke-width: 10;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.4s ease;
}

/* 达成绿：与红涨绿跌的「盈亏」语义无关，仅表示「目标已达成」 */
.goal-ring.is-achieved .ring-fill {
  stroke: #059669;
}

.ring-text {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

.goal-ring.is-achieved .ring-text {
  color: #059669;
}

/* 右侧文字列 */
.goal-facts {
  flex: 1;
  min-width: 0;
}

.goal-name-row {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  margin-bottom: 6px;
}

.goal-flag {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: var(--text-primary);
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.goal-name {
  min-width: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 多目标时才出现的下拉：做成「看起来像标题、点开是列表」的样式 */
.goal-name-select {
  min-width: 0;
  max-width: 100%;
  padding: 0 4px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  font-family: inherit;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  cursor: pointer;
}

.goal-name-select:hover {
  border-color: var(--border-color);
}

.goal-name-select:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.goal-badge {
  flex: 0 0 auto;
  padding: 0 5px;
  border-radius: 4px;
  background: rgba(5, 150, 105, 0.12);
  color: #059669;
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
}

.goal-pending {
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--text-secondary);
}

/* ---------- 四行摘要 ---------- */
.fact-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
  min-width: 0;
}

.fact {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  font-size: 13px;
  line-height: 1.45;
}

.fact-icon {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: #94a3b8;
  stroke-width: 1.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.fact-label {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  /* 固定标签列宽：2 个字 + 「复投」那行的 16px 提示图标，
     不这么做的话只有带图标的行数值会向右错开 20px，一眼就能看出参差。 */
  min-width: 44px;
  gap: 3px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.fact-value {
  min-width: 0;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fact-sub {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.fact-path {
  align-items: flex-start;
}

/* 路径行图标与首行文字基线对齐：图标比 13px 文字略高，下移 1px 才不会顶在上面 */
.fact-path .fact-icon {
  margin-top: 2px;
}

.fact-text {
  min-width: 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-secondary);
}

.fact-text b {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

/* 句尾「即可实现目标 ⓘ」不可拆开折行，否则图标会跟着半句话单独掉到下一行 */
.fact-tail {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}

/* ---------- 底部：假设加仓收益率 ---------- */
.goal-assumed {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 6px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color);
  font-size: 12px;
}

.goal-assumed-label {
  display: flex;
  align-items: center;
  color: var(--text-secondary);
  white-space: nowrap;
}

.goal-assumed-input {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border-color);
  border-radius: 5px;
  background: var(--card-bg);
  padding-right: 5px;
}

.goal-assumed-input.has-error {
  border-color: var(--danger-color);
}

.goal-assumed-input input {
  width: 56px;
  padding: 1px 4px;
  border: none;
  font-size: 12px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  background: transparent;
  color: var(--text-primary);
}

.goal-assumed-input input:focus {
  outline: none;
}

.goal-assumed-suffix {
  color: var(--text-secondary);
  font-size: 11px;
}

.goal-assumed-hint {
  color: var(--text-secondary);
  opacity: 0.85;
}

.goal-assumed-error {
  color: var(--danger-color);
}

/* ---------- 提示区 ---------- */
.goal-notice {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
}

.goal-notice-warning {
  color: #b45309;
}

/*
  卡片被拉成整行宽（900~1280 断点下独占一行，约 1040px）时，
  四行摘要还挤在左侧 500px 里会留出一大片空白 —— 改成两列：
  当前 / 目标 并排，缺口与达成路径合占一行。
  用容器查询而不是媒体查询：判断依据是**卡片自身宽度**，
  三列布局下这张卡只有 500px，不该切两列。
*/
@container goalcard (min-width: 700px) {
  .fact-list {
    display: grid;
    /* auto auto 而不是 1fr 1fr：列宽跟着内容走，否则「当前」和「目标」会被
       撑到卡片两头，中间空出一大段。 */
    grid-template-columns: auto auto;
    justify-content: start;
    column-gap: 32px;
  }

  /* 「再投入本金，或整体息率…」是一整句，被切到半列里会折成三行；
     它独占一行后，上面两行正好是「当前/目标」「缺口/复投」两组同类数字。 */
  .fact-path {
    grid-column: 1 / -1;
  }
}

/*
  本卡片在最左侧，两个 tooltip 图标离屏幕左边只有 100px 出头：
  全局样式里的「以图标为中心左右各伸一半」会把浮层推出视口，
  这里改成与图标左对齐向右展开。
*/
.goal-assumed-label :deep(.tooltip-content),
.fact-label :deep(.tooltip-content),
.fact-text :deep(.tooltip-content) {
  left: 0;
  transform: none;
}

.goal-assumed-label :deep(.tooltip-content::after),
.fact-label :deep(.tooltip-content::after),
.fact-text :deep(.tooltip-content::after) {
  left: 12px;
  transform: none;
}
</style>
