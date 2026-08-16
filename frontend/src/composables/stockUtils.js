/**
 * 股票相关工具函数
 */

/**
 * 根据股票纯数字代码推断交易所前缀。
 *   - 6 / 9 开头 → SH（沪市主板 / 科创板 688 / B股 900）
 *   - 0 / 2 / 3 开头 → SZ（深市主板 / 中小板 002 / 创业板 300）
 * 默认按沪市处理（与后端 tencent.py 的兜底逻辑一致）。
 */
export function getMarketPrefix(code) {
  if (!code) return 'SH'
  const c = String(code)
  if (c.startsWith('6') || c.startsWith('9')) return 'SH'
  if (c.startsWith('0') || c.startsWith('2') || c.startsWith('3')) return 'SZ'
  return 'SH'
}

/**
 * 生成雪球个股页面 URL。
 * 雪球格式：https://xueqiu.com/S/{SH|SZ}{代码}
 * 例：getXueqiuUrl('600578') → 'https://xueqiu.com/S/SH600578'
 */
export function getXueqiuUrl(code) {
  return `https://xueqiu.com/S/${getMarketPrefix(code)}${code}`
}
