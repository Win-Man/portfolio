<template>
  <div class="admin-page">
    <main class="container">
      <!-- 概览：与组合页 / 观察池页共用 .overview-grid，三页第一屏观感一致 -->
      <div class="overview-grid">
        <div class="overview-item">
          <div class="label">定时任务</div>
          <div class="value">{{ overview.total }}</div>
          <div class="sub">个后台任务</div>
        </div>
        <div class="overview-item" :class="{ highlight: overview.enabled > 0 }">
          <div class="label">已启用</div>
          <div class="value">{{ overview.enabled }}</div>
          <div class="sub">关闭的不参与调度</div>
        </div>
        <div class="overview-item">
          <div class="label">最近运行</div>
          <div class="value admin-value-time">
            {{ overview.latest ? formatMinute(overview.latest.started_at) : '—' }}
          </div>
          <div class="sub">{{ overview.latest ? overview.latest.jobName : '还没有运行记录' }}</div>
        </div>
        <div class="overview-item" :class="{ 'profit-down': overview.failures > 0 }">
          <div class="label">累计失败</div>
          <div class="value">{{ overview.failures }}</div>
          <div class="sub">含手动触发</div>
        </div>
      </div>

      <div class="overview-desc">
        计划时刻与开关在 <code>.env</code> 里配置（改完重启后端生效）；时区固定 Asia/Shanghai。
        页面每 30 秒自动刷新一次。
      </div>

      <div v-if="error" class="admin-error">
        <span>{{ error }}</span>
        <button type="button" class="btn btn-secondary btn-sm" @click="loadAll">重试</button>
      </div>

      <div v-else-if="loading" class="loading">加载中</div>

      <div v-else class="admin-jobs">
        <section v-for="job in jobs" :key="job.key" class="card admin-job">
          <div class="card-header">
            <div class="card-title">
              {{ job.name }}
              <span class="admin-key">{{ job.key }}</span>
            </div>
            <div class="card-actions">
              <span class="admin-badge" :class="job.enabled ? 'on' : 'off'">
                {{ job.enabled ? '已启用' : '已禁用' }}
              </span>
              <button
                type="button"
                class="btn btn-primary"
                :disabled="runningKey === job.key || loading"
                @click="handleRun(job)"
              >
                <span v-if="runningKey === job.key" class="admin-spinner"></span>
                {{ runningKey === job.key ? '运行中...' : '↻ 立即运行' }}
              </button>
            </div>
          </div>

          <p class="admin-desc">{{ job.description }}</p>

          <div class="admin-meta">
            <div class="admin-meta-item">
              <span class="k">计划</span>
              <span class="v">{{ job.schedule_label }}</span>
            </div>
            <div class="admin-meta-item">
              <span class="k">下次运行</span>
              <span class="v">{{ job.enabled ? nextRunText(job.next_run_at) : '已禁用，不会自动运行' }}</span>
            </div>
            <!-- 「最后运行时间」是这一页的核心信息，加粗单独成项 -->
            <div class="admin-meta-item strong">
              <span class="k">最后运行</span>
              <span class="v">{{ formatDateTime(job.last_run && job.last_run.started_at) }}</span>
            </div>
            <div class="admin-meta-item">
              <span class="k">上次结果</span>
              <span class="v">
                <span
                  class="admin-status"
                  :class="`tone-${statusMeta(job.last_run && job.last_run.status).tone}`"
                >{{ statusMeta(job.last_run && job.last_run.status).label }}</span>
                <span v-if="job.last_run" class="admin-sub" :title="job.last_run.message">
                  {{ cardResultText(job.last_run) }} · 耗时 {{ formatDuration(job.last_run.duration_ms) }}
                </span>
                <span v-else class="admin-sub">还没有运行记录</span>
              </span>
            </div>
            <div class="admin-meta-item">
              <span class="k">累计运行</span>
              <span class="v">
                {{ job.total_run_count }} 次
                <span class="admin-sub">{{ countsText(job) }}</span>
              </span>
            </div>
            <div v-if="job.last_success_at" class="admin-meta-item">
              <span class="k">上次成功</span>
              <span class="v">{{ formatDateTime(job.last_success_at) }}</span>
            </div>
          </div>

          <div v-if="job.errors_hint" class="admin-hint-error">
            最近失败原因：{{ job.errors_hint }}
          </div>

          <div class="admin-history">
            <div class="admin-history-head">
              <span class="admin-history-title">运行历史</span>
              <span class="admin-history-count">
                {{ (historyTotal[job.key] || 0) }} 条记录（显示最近 {{ (history[job.key] || []).length }} 条）
              </span>
              <button
                type="button"
                class="btn btn-ghost btn-sm"
                :disabled="runningKey === job.key"
                @click="loadRuns(job.key)"
              >刷新</button>
            </div>

            <div v-if="!history[job.key] || history[job.key].length === 0" class="empty-hint">
              还没有运行记录 —— 到点自动跑，或点右上角「立即运行」试一次
            </div>
            <div v-else class="table-container">
              <table class="data-table admin-table">
                <thead>
                  <tr>
                    <th>开始时间</th>
                    <th>触发</th>
                    <th>结果</th>
                    <th class="text-right">耗时</th>
                    <th class="text-right">成功 / 失败</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="run in history[job.key]" :key="run.id">
                    <td class="admin-nowrap">{{ formatDateTime(run.started_at) }}</td>
                    <td>{{ triggerLabel(run.trigger) }}</td>
                    <td>
                      <span class="admin-status" :class="`tone-${statusMeta(run.status).tone}`">
                        {{ statusMeta(run.status).label }}
                      </span>
                    </td>
                    <td class="text-right">{{ formatDuration(run.duration_ms) }}</td>
                    <td class="text-right">{{ run.succeeded }} / {{ run.failed }}</td>
                    <td class="admin-message">
                      <span :title="run.message">{{ run.message || '—' }}</span>
                      <ul v-if="run.errors && run.errors.length" class="admin-errors">
                        <li v-for="(msg, i) in run.errors.slice(0, 3)" :key="i">{{ msg }}</li>
                        <li v-if="run.errors.length > 3">… 另有 {{ run.errors.length - 3 }} 条</li>
                      </ul>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>

<script setup>
/**
 * 后台管理：定时任务的配置、最后运行时间、运行历史、手动触发。
 *
 * 数据来自 `GET /api/admin/jobs`（配置 + 最后一次运行）与每个任务的
 * `GET /api/admin/jobs/{key}/runs`（历史）。所有时间是后端算好的
 * Asia/Shanghai 墙上时间，这里只做格式化，不做时区换算。
 *
 * 轮询：30 秒一次（页面在 keep-alive 里，用 activated/deactivated 控制定时器，
 * 切走就停，避免后台一直打接口）。
 */
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref } from 'vue'

import { getJobRuns, getJobs, runJobNow } from '../api/admin.js'
import { toast } from '../composables/useToast.js'
import {
  formatDateTime,
  formatDuration,
  formatMinute,
  nextRunText,
  overviewOf,
  statusMeta,
  summarizeRun,
  triggerLabel,
} from '../utils/jobs.js'

const HISTORY_LIMIT = 10
const POLL_INTERVAL_MS = 30_000

const jobs = ref([])
const loading = ref(true)
const error = ref('')
const runningKey = ref('')
const history = reactive({})
const historyTotal = reactive({})

let timer = null

const overview = computed(() => overviewOf(jobs.value))

function countsText(job) {
  const counts = job.status_counts || {}
  const parts = ['success', 'partial', 'failed', 'skipped']
    .filter((key) => (counts[key] || 0) > 0)
    .map((key) => `${statusMeta(key).label} ${counts[key]}`)
  return parts.length ? parts.join(' · ') : '暂无'
}

/**
 * 卡片上的结果摘要。跳过时只说「今天无需更新」——
 * 完整原因（如「2026-11-03 的快照已齐」）放在 title 与历史表的说明列里，
 * 否则一行塞不下，还会和「累计运行」挤在一起。
 */
function cardResultText(run) {
  if (run.status === 'skipped') return '今天无需更新'
  return summarizeRun(run)
}

function applyJobs(list) {
  jobs.value = Array.isArray(list) ? list : []
}

async function loadRuns(key) {
  try {
    const data = await getJobRuns(key, { limit: HISTORY_LIMIT })
    history[key] = data.items || []
    historyTotal[key] = data.total || 0
  } catch (e) {
    // 历史拉不到不影响任务卡片本身：只在控制台留痕，页面上仍显示「暂无记录」
    console.error(`加载 ${key} 运行历史失败:`, e)
    history[key] = history[key] || []
  }
}

async function loadAll({ quiet = false } = {}) {
  if (!quiet) {
    loading.value = true
    error.value = ''
  }
  try {
    const list = await getJobs()
    applyJobs(list)
    await Promise.all(jobs.value.map((job) => loadRuns(job.key)))
    error.value = ''
  } catch (e) {
    console.error('加载定时任务失败:', e)
    if (!quiet) error.value = e.message || '加载失败，请稍后重试'
  } finally {
    if (!quiet) loading.value = false
  }
}

async function handleRun(job) {
  runningKey.value = job.key
  try {
    const run = await runJobNow(job.key)
    const meta = statusMeta(run.status)
    const text = `${job.name}：${meta.label}`
    if (run.status === 'failed') toast.error(`${text}（${run.message || '见运行历史'}）`)
    else toast.success(`${text} · ${summarizeRun(run)}`)
  } catch (e) {
    toast.error(e.message || '触发失败，请稍后重试')
  } finally {
    runningKey.value = ''
    await loadAll({ quiet: true })
  }
}

function startPolling() {
  stopPolling()
  timer = setInterval(() => loadAll({ quiet: true }), POLL_INTERVAL_MS)
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(loadAll)
onActivated(() => {
  // 首次挂载时 onActivated 也会触发，避免重复请求
  if (jobs.value.length > 0) loadAll({ quiet: true })
  startPolling()
})
onDeactivated(stopPolling)
onUnmounted(stopPolling)
</script>

<style scoped>
.admin-jobs {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.admin-job {
  padding: 20px;
}

.admin-key {
  margin-left: 8px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: var(--text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 400;
}

.admin-badge {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.2;
}

.admin-badge.on {
  background: #dcfce7;
  color: #15803d;
}

.admin-badge.off {
  background: #f1f5f9;
  color: var(--text-secondary);
}

.admin-desc {
  margin: 4px 0 16px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.7;
}

.admin-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px 24px;
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  background: #fcfdff;
}

@media (max-width: 980px) {
  .admin-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 620px) {
  .admin-meta {
    grid-template-columns: 1fr;
  }
}

.admin-meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.admin-meta-item .k {
  color: var(--text-secondary);
  font-size: 12px;
}

.admin-meta-item .v {
  font-size: 14px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.admin-meta-item.strong .v {
  font-size: 16px;
  font-weight: 600;
}

.admin-value-time {
  font-size: 20px !important;
}

.admin-sub {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 400;
}

.admin-status {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.6;
  white-space: nowrap;
}

.admin-status.tone-success {
  background: #dcfce7;
  color: #15803d;
}

.admin-status.tone-warning {
  background: #fef3c7;
  color: #b45309;
}

.admin-status.tone-danger {
  background: #fee2e2;
  color: #b91c1c;
}

.admin-status.tone-info {
  background: #dbeafe;
  color: #1d4ed8;
}

.admin-status.tone-muted {
  background: #f1f5f9;
  color: var(--text-secondary);
}

.admin-hint-error {
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fef2f2;
  color: #b91c1c;
  font-size: 12px;
}

.admin-error {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border-radius: var(--radius);
  background: #fef2f2;
  color: #b91c1c;
}

.admin-history {
  margin-top: 18px;
}

.admin-history-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.admin-history-title {
  font-size: 14px;
  font-weight: 600;
}

.admin-history-count {
  color: var(--text-secondary);
  font-size: 12px;
  margin-left: auto;
}

.admin-table {
  font-size: 13px;
}

.admin-nowrap {
  white-space: nowrap;
}

.admin-message {
  max-width: 420px;
  color: var(--text-secondary);
}

.admin-errors {
  margin: 4px 0 0 16px;
  font-size: 12px;
  color: #b91c1c;
}

.admin-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-right: 6px;
  border: 2px solid rgba(255, 255, 255, 0.5);
  border-top-color: #fff;
  border-radius: 50%;
  animation: admin-spin 0.7s linear infinite;
}

@keyframes admin-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
