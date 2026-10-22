/**
 * 「收入日历」专用配色与文案常量 —— 全站唯一来源。
 *
 * 为什么单独一个文件：
 *   - spec D10 把 8 类来源的颜色写死（股/ETF/债/OTC/REITs/自定义/自建/补录），
 *     年视图的状态点、月视图的堆叠柱、图例、月 Sheet 的类型 chip 必须**同色**，
 *     否则「图例认色」失去意义；
 *   - 集中后单测可以直接引用，不用在断言里抄一遍魔法值。
 *
 * 注意：这里只放颜色 / 展示文案，不放任何计算逻辑（计算口径见 utils/calendar.js）。
 */

/** 来源大类的**固定展示顺序**（堆叠柱自下而上 + 图例从左到右，spec D10） */
export const SOURCE_ORDER = ['stock', 'etf', 'bond', 'otc', 'reit', 'custom', 'self', 'manual']

/** 8 类来源固定配色（spec D10，不得随意更改） */
export const SOURCE_COLORS = {
  stock: '#3b82f6',
  etf: '#22c55e',
  bond: '#f59e0b',
  otc: '#8b5cf6',
  reit: '#ec4899',
  custom: '#06b6d4',
  self: '#d946ef',
  manual: '#94a3b8',
}

/** 图例用短名（年视图/月视图图例） */
export const SOURCE_LABELS = {
  stock: '股',
  etf: 'ETF',
  bond: '债',
  otc: 'OTC',
  reit: 'REITs',
  custom: '自定义',
  self: '自建',
  manual: '补录',
}

/** 月 Sheet 行内「类型 chip」文案（spec §7.4.4：ETF / 股票 / 债 / 手动） */
export const SOURCE_CHIP_LABELS = {
  stock: '股票',
  etf: 'ETF',
  bond: '债',
  otc: 'OTC',
  reit: 'REITs',
  custom: '自定义',
  self: '自建',
  manual: '手动',
}

/** 年度累计折线：深绿实线（spec D10） */
export const CUMULATIVE_LINE_COLOR = '#059669'

/**
 * 状态 chip 配色。
 *   predicted 预测（橙）/ announced 待到账（黄）/ paid 已到账（白底绿字）/ manual 补录（灰带边框）
 * 文案取 spec §7.4.4（「待到账 / 预测」）与 D5（已到账 / 补录）。
 */
export const STATUS_COLORS = {
  predicted: { label: '预测', background: '#ffedd5', text: '#c2410c', border: 'transparent' },
  announced: { label: '待到账', background: '#fef9c3', text: '#a16207', border: 'transparent' },
  // 已到账：近白/浅灰底 + 绿字（D5「白底」+ §7.4.4「灰色 chip」）
  paid: { label: '已到账', background: '#f1f5f9', text: '#059669', border: '#e2e8f0' },
  manual: { label: '补录', background: '#f8fafc', text: '#475569', border: '#94a3b8' },
}

/** 年视图每格左下角状态小圆点（spec §7.4.2） */
export const STATUS_DOT_COLORS = {
  paid: '#22c55e',
  announced: '#eab308',
  predicted: '#f97316',
  manual: '#3b82f6',
  none: '#cbd5e1',
  // 未来月份且 akshare 尚未收录：不比「无记录」更像有数据，但空心点提示「待收录」
  pending: '#e2e8f0',
}

/** 未来月份尚未收录时，年视图格子上的 hover 说明 */
export const PENDING_MONTH_TITLE =
  '该月尚未被 akshare 收录，也没有可参照的去年同期记录：日历不做外推，所以显示 ¥0.00'

/** 未来月份含「去年同期预估」时，年视图格子上的 hover 说明 */
export const ESTIMATED_MONTH_TITLE =
  '该月 akshare 尚未收录，金额 = 去年同月实际收到的分红（预估，不是已到账）；「预估」部分不计入「已收录」与「近 12 个月」'

/** 选中月份：2px 主色边框 + 浅主色底（spec D9 / §7.4.2） */
export const SELECTED_MONTH_BORDER = '#2563eb'
export const SELECTED_MONTH_BACKGROUND = 'rgba(37, 99, 235, 0.06)'

/** 模拟价 chip（spec §8-21：黄色，模拟价仅供参考） */
export const MOCK_CHIP = {
  label: 'mock',
  background: '#fef3c7',
  text: '#b45309',
  border: 'transparent',
  title: '模拟价，仅供参考',
}

/**
 * 「去年同期预估」chip。
 * akshare 只收录已发生的分红，未来月份用去年同月实际收到的那笔投影；
 * 这类数字必须一眼可辨，且**不能**混进「已收录 / 已到账」口径。
 */
export const ESTIMATED_CHIP = {
  label: '预估',
  background: '#f1f5f9',
  text: '#64748b',
  border: '#cbd5e1',
  title: '根据去年同月实际收到的分红预估（akshare 尚未收录该月），不是已到账金额',
}
