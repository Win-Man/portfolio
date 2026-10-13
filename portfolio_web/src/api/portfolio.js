const API_BASE = import.meta.env.VITE_API_BASE ?? ''  // dev: vite proxy; prod: 留空走相对路径

// 假数据兜底
const mockPortfolios = [
  { id: 1, name: '我的投资组合', created_at: '2024-01-01' }
]

const mockAssets = [
  { id: 1, portfolio_id: 1, name: '招商银行', code: '600036', quantity: 1000, price: 35.50, dividend_per_share: 1.20, ttm_yield: 3.38, holding_value: 35500 },
  { id: 2, portfolio_id: 1, name: '中国平安', code: '601318', quantity: 500, price: 48.20, dividend_per_share: 2.45, ttm_yield: 5.08, holding_value: 24100 },
  { id: 3, portfolio_id: 1, name: '万科A', code: '000002', quantity: 2000, price: 8.15, dividend_per_share: 0.50, ttm_yield: 6.13, holding_value: 16300 },
  { id: 4, portfolio_id: 1, name: '贵州茅台', code: '600519', quantity: 50, price: 1680.00, dividend_per_share: 25.00, ttm_yield: 1.49, holding_value: 84000 },
  { id: 5, portfolio_id: 1, name: '格力电器', code: '000651', quantity: 800, price: 38.90, dividend_per_share: 1.80, ttm_yield: 4.63, holding_value: 31120 }
]

let nextId = 6

async function fetchWithFallback(url, fallback) {
  try {
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (e) {
    console.warn(`API 请求失败，使用假数据: ${e.message}`)
    return fallback()
  }
}

export async function getPortfolios() {
  return fetchWithFallback(`${API_BASE}/api/portfolios`, () => mockPortfolios)
}

export async function getPortfolioSummary(portfolioId) {
  return fetchWithFallback(`${API_BASE}/api/portfolios/${portfolioId}/summary`, () => {
    const assets = mockAssets.filter(a => a.portfolio_id === Number(portfolioId))
    const totalMarketValue = assets.reduce((sum, a) => sum + a.quantity * a.price, 0)
    const totalDividend = assets.reduce((sum, a) => sum + a.quantity * a.dividend_per_share, 0)
    const weightedTTM = assets.length > 0 
      ? assets.reduce((sum, a) => sum + (a.quantity * a.price / totalMarketValue) * a.ttm_yield, 0)
      : 0
    return {
      total_market_value: totalMarketValue,
      asset_count: assets.length,
      weighted_ttm_yield: weightedTTM,
      expected_annual_income: totalDividend,
      description: '基于当前持股计算的汇总数据'
    }
  })
}

export async function getAssets(portfolioId) {
  return fetchWithFallback(`${API_BASE}/api/portfolios/${portfolioId}/assets`, () => {
    return mockAssets.filter(a => a.portfolio_id === Number(portfolioId))
  })
}

export async function createAsset(portfolioId, asset) {
  try {
    const res = await fetch(`${API_BASE}/api/portfolios/${portfolioId}/assets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(asset)
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (e) {
    console.warn(`API 请求失败: ${e.message}`)
    // Fallback to mock
    const newAsset = { ...asset, id: nextId++, portfolio_id: Number(portfolioId) }
    mockAssets.push(newAsset)
    return newAsset
  }
}

export async function updateAsset(assetId, asset) {
  try {
    const res = await fetch(`${API_BASE}/api/assets/${assetId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(asset)
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (e) {
    console.warn(`API 请求失败: ${e.message}`)
    // Fallback to mock
    const index = mockAssets.findIndex(a => a.id === Number(assetId))
    if (index !== -1) {
      mockAssets[index] = { ...mockAssets[index], ...asset }
      return mockAssets[index]
    }
    throw new Error('Asset not found')
  }
}

export async function deleteAsset(assetId) {
  try {
    const res = await fetch(`${API_BASE}/api/assets/${assetId}`, {
      method: 'DELETE'
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (e) {
    console.warn(`API 请求失败: ${e.message}`)
    // Fallback to mock
    const index = mockAssets.findIndex(a => a.id === Number(assetId))
    if (index !== -1) {
      mockAssets.splice(index, 1)
      return { success: true }
    }
    throw new Error('Asset not found')
  }
}

// 刷新单个资产价格（调用后端真实API）
export async function refreshAssetPrice(asset) {
  try {
    const res = await fetch(`${API_BASE}/api/assets/${asset.id}/refresh-price`, {
      method: 'POST'
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const updatedAsset = await res.json()
    
    // 更新本地 mock 数据
    const index = mockAssets.findIndex(a => a.id === asset.id)
    if (index !== -1) {
      mockAssets[index] = updatedAsset
    }
    
    return updatedAsset
  } catch (e) {
    console.error('刷新价格失败:', e)
    throw e
  }
}

// ---------- 组合的写接口 ----------
// 与上面的读接口不同：组合是用户自己维护的数据，失败必须报错，**不做假数据兜底**
// （凭空造一个组合比直接报错更糟）。错误文案优先透传后端 detail，
// 这样 404「Portfolio not found」之类可以直接 toast 给用户。

/** 尽力从错误响应体里取出可读文案（detail 可能是字符串，也可能是 Pydantic 422 的数组） */
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

async function writeRequest(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const detail = await readErrorDetail(res)
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

/** POST /api/portfolios → PortfolioResponse */
export async function createPortfolio(name) {
  return writeRequest(`${API_BASE}/api/portfolios`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  })
}

/** PUT /api/portfolios/{id} → PortfolioResponse（重命名） */
export async function updatePortfolio(portfolioId, name) {
  return writeRequest(`${API_BASE}/api/portfolios/${portfolioId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  })
}

/** DELETE /api/portfolios/{id} → { message }（后端级联删除其资产与年收入目标） */
export async function deletePortfolio(portfolioId) {
  return writeRequest(`${API_BASE}/api/portfolios/${portfolioId}`, {
    method: 'DELETE'
  })
}

// 批量刷新所有资产价格
export async function refreshAllPrices(portfolioId) {
  try {
    const res = await fetch(`${API_BASE}/api/portfolios/${portfolioId}/assets/refresh-all-prices`, {
      method: 'POST'
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (e) {
    console.error('批量刷新价格失败:', e)
    throw e
  }
}
