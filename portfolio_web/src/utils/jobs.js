/**
 * 后台管理页的展示纯函数（无 Vue 依赖，可直接 node --test）。
 *
 * 时间口径：后端返回的是 **Asia/Shanghai 的墙上时间**（`2026-11-03T15:05:12`，
 * 不带时区后缀）。这里**按字符串拆字段**而不是 `new Date(...)`：
 *  - 不依赖浏览器时区，海外用户看到的也是「任务配置里写的 15:05」；
 *  - Safari 对无时区 ISO 串的解析历史上不一致，正则最稳。
 */

export const STATUS_META = {
  running: { label: '运行中', tone: 'info' },
  success: { label: '成功', tone: 'success' },
  partial: { label: '部分失败', tone: 'warning' },
  failed: { label: '失败', tone: 'danger' },
  skipped: { label: '跳过', tone: 'muted' },
}

/** 未知状态不要渲染成空白：回落成 muted + 原文。 */
export function statusMeta(status) {
  return STATUS_META[status] || { label: status || '未知', tone: 'muted' }
}

function parseParts(value) {
  if (!value || typeof value !== 'string') return null
  const m = value.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!m) return null
  return {
    year: Number(m[1]),
    month: Number(m[2]),
    day: Number(m[3]),
    hour: Number(m[4]),
    minute: Number(m[5]),
    second: Number(m[6] || 0),
  }
}

const pad = (n) => String(n).padStart(2, '0')

/** `2026-11-03T15:05:12` → `11-03 15:05:12`；空值 → `—` */
export function formatDateTime(value) {
  const p = parseParts(value)
  if (!p) return '—'
  return `${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`
}

/** 只到分钟：`11-03 15:05` */
export function formatMinute(value) {
  const p = parseParts(value)
  if (!p) return '—'
  return `${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`
}

/** `2026-11-03T15:05:12` → `15:05`（「每天 15:05」这类展示） */
export function formatTimeOnly(value) {
  const p = parseParts(value)
  if (!p) return '—'
  return `${pad(p.hour)}:${pad(p.minute)}`
}

/** 毫秒 → `820ms` / `1.2s` / `1m 05s`（null 是「没有数据」，不是 0ms） */
export function formatDuration(ms) {
  if (ms === null || ms === undefined || ms === '') return '—'
  const value = Number(ms)
  if (!Number.isFinite(value) || value < 0) return '—'
  if (value < 1000) return `${Math.round(value)}ms`
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`
  const totalSeconds = Math.round(value / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  return `${minutes}m ${pad(totalSeconds % 60)}s`
}

/**
 * 下次运行：相对于「现在」给出人话。
 * @param {string} value 后端 next_run_at
 * @param {Date} now 便于测试注入
 */
export function nextRunText(value, now = new Date()) {
  const p = parseParts(value)
  if (!p) return '—'
  const today = { year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate() }
  const time = `${pad(p.hour)}:${pad(p.minute)}`
  if (p.year === today.year && p.month === today.month && p.day === today.day) {
    return `今天 ${time}`
  }
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  if (
    p.year === tomorrow.getFullYear() &&
    p.month === tomorrow.getMonth() + 1 &&
    p.day === tomorrow.getDate()
  ) {
    return `明天 ${time}`
  }
  return `${pad(p.month)}-${pad(p.day)} ${time}`
}

/**
 * 「成功数 / 总数」。状态已经用徽标单独展示了，这里不再重复「成功」二字，
 * 否则卡片上会出现「成功 成功 10 · …」。跳过时没有分母，直接给原因。
 */
export function summarizeRun(run) {
  if (!run) return '—'
  if (run.status === 'skipped') return run.message || '无需更新'
  const total = Number(run.total ?? 0)
  const succeeded = Number(run.succeeded ?? 0)
  const text = `${succeeded}/${total}`
  return (run.failed ?? 0) > 0 ? `${text}（失败 ${run.failed}）` : text
}

/** 触发方式中文：schedule → 定时，manual → 手动 */
export function triggerLabel(trigger) {
  return trigger === 'manual' ? '手动' : '定时'
}

/**
 * 顶部 KPI：从任务列表里汇总。
 * 「最近一次运行」取所有任务中 started_at 最大的那条（字符串可直接比大小，
 * 因为格式固定为 YYYY-MM-DDTHH:MM:SS）。
 */
export function overviewOf(jobs) {
  const list = Array.isArray(jobs) ? jobs : []
  const enabled = list.filter((j) => j.enabled).length
  let latest = null
  let failures = 0
  for (const job of list) {
    const startedAt = job?.last_run?.started_at
    if (startedAt && (!latest || startedAt > latest.started_at)) {
      latest = { started_at: startedAt, jobName: job.name, status: job.last_run.status }
    }
    failures += Number(job?.status_counts?.failed || 0)
  }
  return {
    total: list.length,
    enabled,
    latest,
    failures,
  }
}
