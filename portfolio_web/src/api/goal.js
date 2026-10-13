/**
 * 年收入目标（Goal）API 客户端。
 *
 * 与 portfolio.js 的读接口不同，这里**没有假数据兜底**：
 * 目标是用户自己录入的数据，编造一个假目标比直接报错更糟。
 * 失败一律 throw，由调用方决定 toast 还是空态。
 *
 * 错误文案优先透传后端 `detail`（中文、可直接给用户看），
 * 例如重名时后端返回 409 + `同名目标已存在`，
 * 前端 toast.error(e.message) 即可原样展示。
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

/** GET /api/portfolios/{id}/goals */
export async function getGoals(portfolioId) {
  return request(`${API_BASE}/api/portfolios/${portfolioId}/goals`)
}

/** POST /api/portfolios/{id}/goals → 201 GoalResponse */
export async function createGoal(portfolioId, payload) {
  return request(`${API_BASE}/api/portfolios/${portfolioId}/goals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

/** PUT /api/goals/{id} → GoalResponse */
export async function updateGoal(goalId, payload) {
  return request(`${API_BASE}/api/goals/${goalId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

/** DELETE /api/goals/{id} → { message } */
export async function deleteGoal(goalId) {
  return request(`${API_BASE}/api/goals/${goalId}`, { method: 'DELETE' })
}

/**
 * GET /api/portfolios/{pid}/goals/{gid}/progress
 *
 * `assumedYield` 为 null / undefined / NaN 时**不拼**该查询参数，
 * 让后端回落到默认的「组合加权平均 TTM 收益率」。
 */
export async function getGoalProgress(portfolioId, goalId, assumedYield) {
  const base = `${API_BASE}/api/portfolios/${portfolioId}/goals/${goalId}/progress`
  const value = Number(assumedYield)
  const hasAssumedYield =
    assumedYield !== null &&
    assumedYield !== undefined &&
    assumedYield !== '' &&
    Number.isFinite(value)
  if (!hasAssumedYield) return request(base)
  return request(`${base}?assumed_yield=${encodeURIComponent(value)}`)
}
