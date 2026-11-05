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

      <!-- SQLite 体积与备份：独立于任务列表，任务接口挂了也能备份 -->
      <section v-if="dbInfo" class="card admin-db">
        <div class="card-header">
          <div class="card-title">
            SQLite 数据库
            <span class="admin-key">{{ dbInfo.file_name || '未配置' }}</span>
            <span v-if="!dbInfo.supported || !dbInfo.exists" class="admin-badge off">
              {{ dbInfo.supported ? '文件缺失' : '非 SQLite' }}
            </span>
          </div>
          <div class="card-actions">
            <span class="db-size" :title="dbInfo.path">{{ formatBytes(dbInfo.size_bytes) }}</span>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="backingUp || !dbInfo.exists"
              @click="handleBackup"
            >
              <span v-if="backingUp" class="admin-spinner"></span>
              {{ backingUp ? '备份中...' : '⤓ 立即备份' }}
            </button>
          </div>
        </div>

        <p class="admin-desc">
          备份走 SQLite 官方的在线备份 API（逐页复制、遇写锁重试），后台任务正在写入也能拿到
          <b>事务一致</b>的快照，不是简单拷文件。备份落在
          <code :title="dbInfo.backup_dir">{{ backupDirLabel }}</code>，不会自动清理 —— 删哪份由你决定。
        </p>

        <div class="admin-meta db-meta">
          <div class="admin-meta-item strong">
            <span class="k">文件大小</span>
            <span class="v">
              {{ formatBytes(dbInfo.size_bytes) }}
              <span class="admin-sub" :title="dbInfo.path">
                {{ dbInfo.tables.length }} 张表 · {{ dbInfo.page_count }} 页
              </span>
            </span>
          </div>
          <div class="admin-meta-item">
            <span class="k">可回收</span>
            <span class="v">
              {{ formatBytes(dbInfo.reclaimable_bytes) }}
              <span class="admin-sub">
                {{ dbInfo.reclaimable_bytes > 0 ? '空闲页，VACUUM 可释放' : '没有空闲页' }}
              </span>
            </span>
          </div>
          <div class="admin-meta-item">
            <span class="k">日志模式</span>
            <span class="v">
              {{ dbInfo.journal_mode || '—' }}
              <span class="admin-sub">
                {{ dbInfo.wal_size_bytes > 0 ? `WAL ${formatBytes(dbInfo.wal_size_bytes)}` : '无 WAL 文件' }}
              </span>
            </span>
          </div>
          <div class="admin-meta-item">
            <span class="k">备份</span>
            <span class="v">
              {{ dbInfo.backup_count }} 份
              <span class="admin-sub">共 {{ formatBytes(dbInfo.backup_size_bytes) }}</span>
            </span>
          </div>
        </div>

        <!-- 按表占用：默认收起，避免把任务卡片挤到屏幕外 -->
        <details v-if="dbInfo.tables.length" class="db-tables">
          <summary>按表占用（{{ dbInfo.tables.length }} 张表）</summary>
          <div class="table-container">
            <table class="data-table admin-table">
              <thead>
                <tr>
                  <th>表</th>
                  <th class="text-right">行数</th>
                  <th class="text-right">占用</th>
                  <th class="text-right">占总大小</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in dbInfo.tables" :key="row.name">
                  <td class="db-table-name">{{ row.name }}</td>
                  <td class="text-right">{{ row.rows.toLocaleString('zh-CN') }}</td>
                  <td class="text-right">{{ formatBytes(row.size_bytes) }}</td>
                  <td class="text-right admin-sub">{{ shareText(row.size_bytes) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
        <div v-else-if="dbInfo.exists && !dbInfo.tables_supported" class="admin-hint-note">
          当前 SQLite 构建未启用 <code>dbstat</code>，无法按表统计占用（总大小不受影响）。
        </div>

        <div class="admin-history">
          <div class="admin-history-head">
            <span class="admin-history-title">备份</span>
            <span class="admin-history-count">
              {{ backups.length ? `共 ${backups.length} 份，新的在前` : '' }}
            </span>
            <button
              type="button"
              class="btn btn-ghost btn-sm"
              :disabled="loadingDb"
              @click="loadDatabase"
            >刷新</button>
          </div>

          <div v-if="dbError" class="admin-hint-error">{{ dbError }}</div>
          <div v-else-if="!backups.length" class="empty-hint">
            还没有备份 —— 点右上角「立即备份」生成一份（就是当前库的完整快照）
          </div>
          <div v-else class="table-container db-backup-list">
            <table class="data-table admin-table">
              <thead>
                <tr>
                  <th>文件</th>
                  <th class="text-right">大小</th>
                  <th>生成时间</th>
                  <th class="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in backups" :key="row.name">
                  <td class="db-file-name" :title="row.name">{{ row.name }}</td>
                  <td class="text-right">{{ formatBytes(row.size_bytes) }}</td>
                  <td class="admin-nowrap">{{ formatDateTime(row.created_at) }}</td>
                  <td class="text-right db-actions">
                    <a
                      class="btn btn-ghost btn-sm"
                      :href="backupDownloadUrl(row.name)"
                      :download="row.name"
                    >下载</a>
                    <button
                      type="button"
                      class="btn btn-ghost btn-sm db-danger"
                      :disabled="deletingName === row.name"
                      @click="handleDeleteBackup(row)"
                    >{{ deletingName === row.name ? '删除中…' : '删除' }}</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

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

import {
  backupDownloadUrl,
  createBackup,
  deleteBackup,
  getBackups,
  getDatabaseInfo,
  getJobRuns,
  getJobs,
  runJobNow,
} from '../api/admin.js'
import { toast } from '../composables/useToast.js'
import { formatBytes } from '../utils/format.js'
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

// 数据库卡片是**独立**的一块状态：任务接口挂了它照样能用，反之亦然
const dbInfo = ref(null)
const backups = ref([])
const loadingDb = ref(false)
const backingUp = ref(false)
const deletingName = ref('')
const dbError = ref('')

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

// ---------- 数据库：体积与备份 ----------

/** 备份目录只显示最后两级（portfolio_api/backups），完整路径放 title，否则长路径会把描述撑断行 */
const backupDirLabel = computed(() => {
  const raw = (dbInfo.value && dbInfo.value.backup_dir) || ''
  if (!raw) return 'backups'
  return raw.split('/').filter(Boolean).slice(-2).join('/')
})

/** 单表占用占总大小的比例。总大小为 0 时给「—」，不做 0/0。 */
function shareText(sizeBytes) {
  const total = Number(dbInfo.value && dbInfo.value.size_bytes) || 0
  if (!total) return '—'
  return `${((Number(sizeBytes) / total) * 100).toFixed(1)}%`
}

async function loadDatabase({ quiet = false } = {}) {
  if (!quiet) loadingDb.value = true
  try {
    // 概况与列表一起取：概况里的 backup_count / latest_backup 也来自列表
    const [info, list] = await Promise.all([getDatabaseInfo(), getBackups()])
    dbInfo.value = info
    backups.value = Array.isArray(list.items) ? list.items : []
    dbError.value = ''
  } catch (e) {
    // 数据库卡片失败只在自己这一块提示，不接管整页的 error
    console.error('加载数据库信息失败:', e)
    dbError.value = e.message || '数据库信息加载失败'
    if (!dbInfo.value) dbInfo.value = null
  } finally {
    loadingDb.value = false
  }
}

async function handleBackup() {
  backingUp.value = true
  try {
    const info = await createBackup()
    toast.success(`备份完成：${info.name}（${formatBytes(info.size_bytes)}）`)
    await loadDatabase({ quiet: true })
  } catch (e) {
    toast.error(e.message || '备份失败，请稍后重试')
  } finally {
    backingUp.value = false
  }
}

async function handleDeleteBackup(row) {
  if (!confirm(`确定删除备份「${row.name}」吗？删除后无法恢复。`)) return
  deletingName.value = row.name
  try {
    await deleteBackup(row.name)
    toast.success(`已删除 ${row.name}`)
    await loadDatabase({ quiet: true })
  } catch (e) {
    toast.error(e.message || '删除失败，请稍后重试')
  } finally {
    deletingName.value = ''
  }
}

function startPolling() {
  stopPolling()
  timer = setInterval(() => {
    loadAll({ quiet: true })
    loadDatabase({ quiet: true })
  }, POLL_INTERVAL_MS)
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(() => {
  loadAll()
  loadDatabase()
})
onActivated(() => {
  // 首次挂载时 onActivated 也会触发，避免重复请求
  if (jobs.value.length > 0) loadAll({ quiet: true })
  if (dbInfo.value) loadDatabase({ quiet: true })
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
/* ---------- SQLite 数据库卡片 ---------- */
.admin-db {
  padding: 20px;
  margin-bottom: 16px;
}

/* 空间大小是这张卡最重要的数字，放大到与「最近运行」同级 */
.db-size {
  margin-right: 4px;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

/*
  4 项元信息。必须自己写媒体查询：.db-meta 与 .admin-meta 特异性相同、
  又排在文件后面，会把 .admin-meta 的 980/620 断点覆盖掉。
*/
.db-meta {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

@media (max-width: 1100px) {
  .db-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 620px) {
  .db-meta {
    grid-template-columns: 1fr;
  }
}

.db-tables {
  margin-top: 14px;
  padding: 10px 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  background: #fcfdff;
}

.db-tables > summary {
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.db-tables > summary:hover {
  color: var(--primary-color);
}

.db-tables[open] > summary {
  margin-bottom: 8px;
}

.db-table-name,
.db-file-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.db-file-name {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 备份会越攒越多，列表限高滚动，不让卡片无限变长 */
.db-backup-list {
  max-height: 300px;
  overflow-y: auto;
}

.db-actions {
  white-space: nowrap;
}

.db-actions .btn {
  padding: 3px 10px;
  font-size: 12px;
  text-decoration: none;
}

.db-actions .btn + .btn {
  margin-left: 6px;
}

.db-danger:hover:not(:disabled) {
  background: #fef2f2;
  border-color: #fca5a5;
  color: #b91c1c;
}

.admin-hint-note {
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #f8fafc;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.7;
}

</style>
