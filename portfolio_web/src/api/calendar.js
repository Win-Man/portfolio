/**
 * 收入日历（Calendar / Dividend）API 客户端。
 *
 * 与 `api/goal.js` 同风格：**没有假数据兜底**。
 * 分红记录是账实数据，编造一笔假分红比直接报错更糟；失败一律 throw，
 * 由调用方决定 toast 还是空态。
 *
 * 错误文案优先透传后端 `detail`（中文、可直接给用户看），例如
 *   - 404 `找不到该笔分红记录`
 *   - 404 `补录记录不存在`
 *   - 422 `组合内不存在该标的`
 *   - 422 `不能补未来日期；预告请等待 akshare 收录`
 * Pydantic 的 422 是数组，取第一条的 `msg`。
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? '' // dev: vite proxy; prod: 留空走相对路径

/**
 * 尽力从错误响应体里取出可读文案。
 * FastAPI 的 detail 可能是字符串（HTTPException）或数组（Pydantic 422）。
 * 拿不到就返回空串，由调用方退回 `HTTP ${status}`。
 */
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
    /* 响应体不是 JSON：忽略，退回 HTTP 状态码 */
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

/**
 * GET /api/portfolios/{pid}/calendar?year=2026
 * → CalendarResponse（12 个月合计 + total_year / total_count / asset_count）
 */
export async function getCalendar(portfolioId, year) {
  const qs = `?year=${encodeURIComponent(year)}`
  return request(`${API_BASE}/api/portfolios/${portfolioId}/calendar${qs}`)
}

/**
 * GET /api/portfolios/{pid}/calendar/{year}/{month}
 * → MonthDividendsResponse（该月逐笔明细）
 */
export async function getMonthDividends(portfolioId, year, month) {
  return request(`${API_BASE}/api/portfolios/${portfolioId}/calendar/${year}/${month}`)
}

/**
 * POST /api/portfolios/{pid}/dividends/confirm
 * body {code, ex_date} → 200 {code, ex_date, status, confirmed_at}（幂等）
 */
export async function confirmDividend(portfolioId, code, exDate) {
  return request(`${API_BASE}/api/portfolios/${portfolioId}/dividends/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, ex_date: exDate }),
  })
}

/**
 * POST /api/portfolios/{pid}/dividends/manual
 * body {code, ex_date, amount_per_share, note?} → 201 ManualDividendResponse
 */
export async function createManualDividend(portfolioId, payload) {
  return request(`${API_BASE}/api/portfolios/${portfolioId}/dividends/manual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/**
 * DELETE /api/portfolios/{pid}/dividends/manual/{record_id} → 200 {message}
 */
export async function deleteManualDividend(portfolioId, recordId) {
  return request(`${API_BASE}/api/portfolios/${portfolioId}/dividends/manual/${recordId}`, {
    method: 'DELETE',
  })
}

/**
 * GET /api/portfolios/{pid}/dividends/manual → ManualDividendResponse[]
 *
 * 月明细接口（MonthDividendItem）里**没有补录记录的 id**，
 * 删补录前先用本接口按 (code, ex_date) 反查 id。
 */
export async function getManualDividends(portfolioId) {
  return request(`${API_BASE}/api/portfolios/${portfolioId}/dividends/manual`)
}
