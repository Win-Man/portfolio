/**
 * 后台管理（定时任务）API 客户端。
 *
 * 与其他读接口一致：**没有假数据兜底**。任务状态、最后运行时间这种东西编不出来 ——
 * 拿不到就报错，比给一个「看起来正常」的空列表安全。
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function readErrorDetail(res) {
  try {
    const body = await res.json()
    const detail = body?.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0]
      if (typeof first === 'string' && first) return first
      if (first && typeof first.msg === 'string' && first.msg) return first.msg
    }
  } catch {
    /* 响应体不是 JSON：退回 HTTP 状态码 */
  }
  return ''
}

async function request(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const detail = await readErrorDetail(res)
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

/** 任务列表：配置 + 最后运行时间 + 下次运行 */
export async function getJobs() {
  return await request(`${API_BASE}/api/admin/jobs`)
}

/** 运行历史（新的在前） */
export async function getJobRuns(key, { limit = 10, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return await request(`${API_BASE}/api/admin/jobs/${encodeURIComponent(key)}/runs?${params}`)
}

/** 立即执行一次（返回本次运行记录） */
export async function runJobNow(key) {
  return await request(`${API_BASE}/api/admin/jobs/${encodeURIComponent(key)}/run`, {
    method: 'POST',
  })
}

// ---------- SQLite 体积与备份 ----------

/** 数据库概况：文件大小、页数、按表占用、备份目录概况 */
export async function getDatabaseInfo() {
  return await request(`${API_BASE}/api/admin/database`)
}

/** 备份列表（新的在前） */
export async function getBackups() {
  return await request(`${API_BASE}/api/admin/database/backups`)
}

/** 立即备份一份 */
export async function createBackup() {
  return await request(`${API_BASE}/api/admin/database/backups`, { method: 'POST' })
}

/** 删除一份备份 */
export async function deleteBackup(name) {
  return await request(`${API_BASE}/api/admin/database/backups/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

/** 下载地址：交给 <a href> 由浏览器直接下载，不用 fetch（省内存、有原生进度） */
export function backupDownloadUrl(name) {
  return `${API_BASE}/api/admin/database/backups/${encodeURIComponent(name)}`
}

// ---------- 日报邮件推送 ----------

/** 邮件配置状态（不含密码，账号已打码） */
export async function getMailConfig() {
  return await request(`${API_BASE}/api/admin/mail`)
}

/** 立即把某一天的日报发出去（默认今天） */
export async function sendMailNow({ reportDate = '' } = {}) {
  const query = reportDate ? `?report_date=${encodeURIComponent(reportDate)}` : ''
  return await request(`${API_BASE}/api/admin/mail/send${query}`, { method: 'POST' })
}

/** 发一封测试邮件：只验证 SMTP 通不通 */
export async function sendTestMail() {
  return await request(`${API_BASE}/api/admin/mail/test`, { method: 'POST' })
}

// ---------- A 股交易日历 ----------

/** 交易日历状态：今天开不开市、覆盖范围、未来若干天 */
export async function getTradingCalendar({ days = 10 } = {}) {
  return await request(`${API_BASE}/api/admin/trading-calendar?days=${days}`)
}

/** 强制联网刷新日历（akshare）。失败不抛错，返回的 calendar_stale 会是 true */
export async function refreshTradingCalendar({ days = 10 } = {}) {
  return await request(`${API_BASE}/api/admin/trading-calendar/refresh?days=${days}`, {
    method: 'POST',
  })
}
