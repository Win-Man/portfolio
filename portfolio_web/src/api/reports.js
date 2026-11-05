/**
 * 每日日报 API 客户端。
 *
 * 与其他读接口一致：**没有假数据兜底** —— 日报里的数字是「某一天的账」，
 * 编不出来也不能编；拿不到就抛错，页面显示错误态。
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
    const error = new Error(detail || `HTTP ${res.status}`)
    error.status = res.status
    throw error
  }
  return await res.json()
}

/**
 * 历史日报列表（不含 payload，只够渲染列表行）。
 * @param {{reportType: 'portfolio'|'watchlist', portfolioId?: number|null, limit?: number, offset?: number}} p
 */
export async function getReports({ reportType, portfolioId = null, limit = 30, offset = 0 }) {
  const params = new URLSearchParams({
    report_type: reportType,
    limit: String(limit),
    offset: String(offset),
  })
  // 观察池是全局的，不需要组合 id；组合日报必须带
  if (reportType === 'portfolio' && portfolioId !== null && portfolioId !== undefined) {
    params.set('portfolio_id', String(portfolioId))
  }
  return await request(`${API_BASE}/api/reports?${params}`)
}

/** 最近一份日报（带 payload）。没有生成过时抛 404。 */
export async function getLatestReport({ reportType, portfolioId = null }) {
  const params = new URLSearchParams({ report_type: reportType })
  if (reportType === 'portfolio' && portfolioId !== null && portfolioId !== undefined) {
    params.set('portfolio_id', String(portfolioId))
  }
  return await request(`${API_BASE}/api/reports/latest?${params}`)
}

/** 单份日报详情（带 payload）。 */
export async function getReport(id) {
  return await request(`${API_BASE}/api/reports/${id}`)
}

/**
 * 立即生成今天的日报（重复生成会覆盖当天那一份）。
 * portfolio 类型不传 portfolioId 时后端会生成**全部组合**。
 */
export async function generateReport({ reportType, portfolioId = null }) {
  return await request(`${API_BASE}/api/reports/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      report_type: reportType,
      portfolio_id: reportType === 'portfolio' ? portfolioId : null,
    }),
  })
}

/** 删除一份日报（只删这份记录，不动任何资产/观察池数据）。 */
export async function deleteReport(id) {
  return await request(`${API_BASE}/api/reports/${id}`, { method: 'DELETE' })
}
