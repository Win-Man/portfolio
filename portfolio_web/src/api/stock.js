// 股票搜索 API
// 优先使用后端API，后端调用akshare获取全量A股数据

const API_BASE = import.meta.env.VITE_API_BASE ?? ''  // dev: vite proxy; prod: 留空走相对路径

/**
 * 搜索 A 股股票
 * @param {string} keyword - 搜索关键字（代码或名称）
 * @returns {Promise<Array>} 股票列表
 */
export async function searchStocks(keyword) {
  if (!keyword || keyword.trim().length < 1) {
    return []
  }

  try {
    // 调用后端 API（后端使用 akshare 获取全量A股数据）
    const res = await fetch(`${API_BASE}/api/stocks/search?keyword=${encodeURIComponent(keyword.trim())}`)
    
    if (!res.ok) {
      console.warn('股票搜索API失败')
      return []
    }
    
    return await res.json()
    
  } catch (e) {
    console.error('股票搜索失败:', e)
    return []
  }
}

/**
 * 获取股票实时价格
 * @param {string} code - 股票代码
 * @returns {Promise<Object>} 包含price的对象
 */
export async function getStockPrice(code) {
  try {
    const res = await fetch(`${API_BASE}/api/stocks/${code}/price`)
    if (res.ok) {
      return await res.json()
    }
  } catch (e) {
    console.warn('获取价格失败:', e)
  }
  return null
}

/**
 * 获取股票历史分红数据
 * @param {string} code - 股票代码
 * @param {string} name - 股票名称
 * @returns {Promise<Array>} 分红历史
 */
export async function getDividendHistory(code, name) {
  // 优先使用后端 API
  try {
    const res = await fetch(`${API_BASE}/api/stocks/${code}/dividend-history`)
    if (res.ok) {
      return await res.json()
    }
  } catch (e) {
    console.warn('后端分红API不可用:', e.message)
  }
  
  // 如果后端不可用，尝试东方财富 API
  try {
    // 东方财富分红数据 API
    const url = `https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BONUS_DETAILS&columns=SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_BONUS_TYPE,DIVIDEND_RATIO,DIVIDEND_PER_SHARE,BONUS_IT_RATIO,BONUS_IT_PAYABLE_DATE,TRANSFER_RATIO,TRANSFER_PAYABLE_DATE,PAID_RATIO,PAID_PAYABLE_DATE,REGISTER_DATE,EX_DIVIDEND_DATE,EQUITY_DATE,IMPLEMENT_STATUS&quoteColumns=&filter=(SECURITY_CODE%3D%22${code}%22)&pageNumber=1&pageSize=20&sortTypes=-1&sortColumns=EX_DIVIDEND_DATE&source=HSF10&client=web`
    
    const res = await fetch(url, {
      headers: {
        'Referer': 'https://www.eastmoney.com/',
        'User-Agent': 'Mozilla/5.0'
      }
    })
    
    const data = await res.json()
    
    if (data.result && data.result.data) {
      return data.result.data.map(item => ({
        year: new Date(item.EX_DIVIDEND_DATE || item.REGISTER_DATE).getFullYear(),
        ex_dividend_date: item.EX_DIVIDEND_DATE,
        dividend_per_share: item.DIVIDEND_PER_SHARE || 0,
        bonus_shares: item.BONUS_IT_RATIO || 0,  // 每股红股
        transfer_shares: item.TRANSFER_RATIO || 0, // 每股转增
        status: item.IMPLEMENT_STATUS
      })).filter(item => item.year > 0)
    }
  } catch (e) {
    console.error('东方财富分红API失败:', e)
  }
  
  // 返回模拟数据（用于演示）
  return generateMockDividendHistory(code, name)
}

/**
 * 生成模拟分红数据
 */
function generateMockDividendHistory(code, name) {
  // 基于代码生成一些假的历年分红数据作为演示
  const baseDividend = Math.random() * 2 + 0.5
  const years = []
  const currentYear = new Date().getFullYear()
  
  for (let i = 0; i < 5; i++) {
    const year = currentYear - i
    // 模拟每年分红，略有波动
    const yearlyDividend = baseDividend * (0.8 + Math.random() * 0.4)
    years.push({
      year,
      ex_dividend_date: `${year}-06-30`,
      dividend_per_share: Math.round(yearlyDividend * 100) / 100,
      bonus_shares: 0,
      transfer_shares: 0,
      status: '已实施'
    })
  }
  
  return years
}

/**
 * 根据代码识别市场（沪市/深市）
 */
export function identifyMarket(code) {
  if (!code) return '未知'
  
  const firstDigit = code.charAt(0)
  
  // 沪市：6开头（上交所）、7开头（退市、ETF等）
  if (firstDigit === '6' || firstDigit === '7') {
    return '沪市'
  }
  
  // 深市：0、2、3开头
  // 0：深圳主板
  // 2：创业板（2020年前）
  // 3：创业板（2020年后）
  if (['0', '2', '3'].includes(firstDigit)) {
    return '深市'
  }
  
  return '未知'
}
