/**
 * 观察池（watchlist）API 客户端。
 *
 * 与其他读接口不同，这里**没有假数据兜底**：观察池是用户自己的候选清单，
 * 编造一条假标的比直接报错更糟（spec 003 §7.2）。
 *
 * 错误一律 throw，文案优先透传后端 `detail`：
 *   - 409「该标的已在观察池中」
 *   - 404「Watchlist item not found」
 *   - 422 的 detail 可能是数组（Pydantic），取第一条 msg
 * 拿不到就退回 `HTTP ${status}`。
 */
import { buildRangeQuery } from '../utils/watchlist.js'

const API_BASE = import.meta.env.VITE_API_BASE ?? '' // dev: vite proxy; prod: 留空走相对路径

/** 尽力从错误响应体里取出可读文案 */
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

/** GET /api/watchlist → WatchlistItemResponse[]（只读库，不触发抓取） */
export async function getWatchlist() {
  return request(`${API_BASE}/api/watchlist`)
}

/** POST /api/watchlist → 201 */
export async function createWatchlistItem(payload) {
  return request(`${API_BASE}/api/watchlist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** PUT /api/watchlist/{id} → 部分更新（target_price=0 表示清空） */
export async function updateWatchlistItem(itemId, payload) {
  return request(`${API_BASE}/api/watchlist/${itemId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** DELETE /api/watchlist/{id} → { message }（级联删快照） */
export async function deleteWatchlistItem(itemId) {
  return request(`${API_BASE}/api/watchlist/${itemId}`, { method: 'DELETE' })
}

/** POST /api/watchlist/refresh → 刷新后的完整列表（失败项带 refresh_error） */
export async function refreshAllWatchlist() {
  return request(`${API_BASE}/api/watchlist/refresh`, { method: 'POST' })
}

/** POST /api/watchlist/{id}/refresh → 单条刷新 */
export async function refreshWatchlistItem(itemId) {
  return request(`${API_BASE}/api/watchlist/${itemId}/refresh`, { method: 'POST' })
}

/** GET /api/watchlist/{id}/snapshots?days=N | ?start=&end= | ?all=true → trade_date ASC */
export async function getWatchlistSnapshots(itemId, range = 90) {
  return request(`${API_BASE}/api/watchlist/${itemId}/snapshots?${buildRangeQuery(range)}`)
}

/** GET /api/watchlist/{id}/history-range → { first_date, last_date, count } */
export async function getWatchlistHistoryRange(itemId) {
  return request(`${API_BASE}/api/watchlist/${itemId}/history-range`)
}

/**
 * GET /api/watchlist/{id}/dividends?days=N | ?start=&end= | ?all=true
 *   → [{ ex_date, dividend_per_share }]
 *
 * 只用于给走势图打除权日竖线。后端走分红缓存的 6 小时缓存，
 * 且窗口裁剪在前端做（一次拉全部历史，切区间不再请求）。
 */
export async function getWatchlistDividends(itemId, range = 365) {
  return request(`${API_BASE}/api/watchlist/${itemId}/dividends?${buildRangeQuery(range)}`)
}
