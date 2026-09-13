/**
 * 趋势健康度（前端本地计算版）
 *
 * ★ 与后端 `engine._calc_trend_health` 完全同口径（5 维度：量能/支撑/深度/动量/均线），
 *   改一处必须同步另一处。输入 `getStockTechnical` 返回的 techData，纯函数、零网络——
 *   持仓页里复用「今日预测」已拉的那次 K 线请求，趋势健康不再单独依赖 getStockScore。
 *
 * 用法：
 *   import { calcTrendHealth } from '../utils/trendHealth'
 *   const th = calcTrendHealth(techData)   // { score, verdict, details }
 */

function _round2(v) {
  if (v === null || v === undefined || isNaN(v)) return 0
  return Math.round(v * 100) / 100
}

export function calcTrendHealth(techData) {
  if (!techData || techData.length < 30) {
    return { score: 0, verdict: '数据不足', details: [] }
  }

  const latest = techData[techData.length - 1]
  const price = latest.close || 0
  const ma5 = latest.ma5 || 0
  const ma20 = latest.ma20 || 0
  const ma60 = latest.ma60 || 0
  const dif = latest.dif || 0
  const details = []

  // ── 维度 1：量能（回调时缩量=健康）──
  const volumes = techData.map(k => k.volume).filter(v => v)
  if (volumes.length >= 20) {
    const vol5 = volumes.slice(-5).reduce((a, b) => a + b, 0) / 5
    const vol20 = volumes.slice(-20).reduce((a, b) => a + b, 0) / 20
    const r = vol20 > 0 ? vol5 / vol20 : 1
    if (r < 0.8) {
      details.push({ dim: '量能', healthy: true, desc: `缩量（近5/20日=${Math.round(r * 100)}%），卖盘枯竭` })
    } else if (r < 1.2) {
      details.push({ dim: '量能', healthy: true, desc: `量能平稳（${Math.round(r * 100)}%）` })
    } else {
      details.push({ dim: '量能', healthy: false, desc: `放量下跌（${Math.round(r * 100)}%），注意出货` })
    }
  } else {
    details.push({ dim: '量能', healthy: true, desc: '数据不足' })
  }

  // ── 维度 2：支撑位（守住 MA20 或 MA60=健康）──
  if (ma20 > 0 && ma60 > 0) {
    if (price >= ma20) {
      details.push({ dim: '支撑', healthy: true, desc: `价格 ${price.toFixed(2)} 站稳 MA20（${ma20.toFixed(2)}）` })
    } else if (price >= ma60) {
      details.push({ dim: '支撑', healthy: true, desc: `回踩 MA60（${ma60.toFixed(2)}）获支撑` })
    } else {
      details.push({ dim: '支撑', healthy: false, desc: '跌破 MA20 和 MA60，支撑失守' })
    }
  } else if (ma20 > 0) {
    const healthy = price >= ma20
    details.push({ dim: '支撑', healthy, desc: `价格${healthy ? '站稳' : '跌破'} MA20（${ma20.toFixed(2)}）` })
  } else {
    details.push({ dim: '支撑', healthy: false, desc: '均线数据不足' })
  }

  // ── 维度 3：回调深度（<8%=健康，>15%=危险）──
  const recentHighs = techData.slice(-60).map(k => k.high).filter(v => v)
  if (recentHighs.length) {
    const recentHigh = Math.max(...recentHighs)
    if (recentHigh > 0) {
      const pullbackPct = (recentHigh - price) / recentHigh * 100
      if (pullbackPct <= 8) {
        details.push({ dim: '深度', healthy: true, desc: `回调 ${pullbackPct.toFixed(1)}%，正常范围` })
      } else if (pullbackPct <= 15) {
        details.push({ dim: '深度', healthy: true, desc: `回调 ${pullbackPct.toFixed(1)}%，偏深但可接受` })
      } else {
        details.push({ dim: '深度', healthy: false, desc: `回调 ${pullbackPct.toFixed(1)}%，深度过大` })
      }
    } else {
      details.push({ dim: '深度', healthy: true, desc: '数据平稳' })
    }
  } else {
    details.push({ dim: '深度', healthy: true, desc: '数据不足' })
  }

  // ── 维度 4：MACD 动量（DIF>0=健康）──
  if (dif > 0) {
    details.push({ dim: '动量', healthy: true, desc: `DIF=${_round2(dif)} > 0，多头动能仍在` })
  } else {
    details.push({ dim: '动量', healthy: false, desc: `DIF=${_round2(dif)} < 0，空头占优` })
  }

  // ── 维度 5：均线结构（MA5>MA20=健康）──
  if (ma5 > 0 && ma20 > 0) {
    if (ma5 >= ma20) {
      details.push({ dim: '均线', healthy: true, desc: `MA5(${ma5.toFixed(2)}) > MA20(${ma20.toFixed(2)})，短期趋势完好` })
    } else {
      details.push({ dim: '均线', healthy: false, desc: `MA5(${ma5.toFixed(2)}) < MA20(${ma20.toFixed(2)})，短期均线死叉` })
    }
  } else {
    details.push({ dim: '均线', healthy: false, desc: '均线数据不足' })
  }

  // ── 汇总 ──
  const healthyCount = details.filter(d => d.healthy).length
  const verdict = healthyCount >= 4 ? '趋势健康' : healthyCount >= 3 ? '趋势偏弱' : '趋势恶化'
  return { score: healthyCount, verdict, details }
}
