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
