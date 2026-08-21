/**
 * 前端评分引擎
 * 
 * 功能：
 *   - 三维度评分：技术面（40%）+ 资金面（25%）+ 基本面（35%）
 *   - 信号判定：强烈买入/买入/观望/卖出/强烈卖出
 *   - 与 Python 版本保持一致（误差 < 0.1）
 * 
 * 评分体系：
 *   ┌──────────┬──────┬──────────────────────────────────────────────┐
 *   │ 维度     │ 权重 │ 包含的子指标                                 │
 *   ├──────────┼──────┼──────────────────────────────────────────────┤
 *   │ 技术面   │ 40%  │ MA均线 / MACD / RSI / KDJ / 布林带           │
 *   │ 资金面   │ 25%  │ 量价配合 / 涨跌动量 / 换手率 / 成交额强度    │
 *   │ 基本面   │ 35%  │ PE估值 / PB估值 / 市值规模                   │
 *   └──────────┴──────┴──────────────────────────────────────────────┘
 */

// ── 权重配置 ──
const W_TECHNICAL = 0.40
const W_CAPITAL = 0.25
const W_FUNDAMENTAL = 0.35

// ── 工具函数 ──

function clamp(v, lo = 0, hi = 100) {
  return Math.max(lo, Math.min(hi, v))
}

function round1(v) {
  return Math.round(v * 10) / 10
}

/**
 * 评分单只股票
 * @param {Object} params
 * @param {string} params.code - 股票代码
 * @param {string} params.name - 股票名称
 * @param {Array} params.technicalData - 技术指标数组（来自 Worker）
 * @param {Object} params.stockInfo - 实时行情 { price, change_pct, pe, pb, market_cap, turnover_rate, ... }
 * @returns {Object} 评分结果
 */
export function scoreStock({ code, name, technicalData, stockInfo }) {
  technicalData = technicalData || []
  stockInfo = stockInfo || {}

  // 三维度评分
  const dimTech = scoreTechnical(technicalData)
  const dimCap = scoreCapital(technicalData, stockInfo)
  const dimFund = scoreFundamental(stockInfo)

  // 加权求和
  const total = round1(
    dimTech.score * W_TECHNICAL +
    dimCap.score * W_CAPITAL +
    dimFund.score * W_FUNDAMENTAL
  )

  // 信号判定
  const { signal, signalLevel } = deriveSignal(total)

  // 生成摘要
  const summary = generateSummary(total, signal, dimTech, dimCap, dimFund)

  return {
    code,
    name,
    total_score: total,
    signal,
    signal_level: signalLevel,
    dimensions: {
      technical: dimTech,
      capital: dimCap,
      fundamental: dimFund,
    },
    summary,
    change_pct: stockInfo.change_pct || 0,
  }
}

/**
 * 技术面评分（满分 100）
 */
function scoreTechnical(techData) {
  if (!techData || techData.length < 30) {
    return { score: 50, details: { 说明: '数据不足，中性评分' } }
  }

  const latest = techData[techData.length - 1]
  const prev = techData[techData.length - 2] || latest
  const details = {}
  let total = 0

  // 1. MA 均线趋势（25 分）
  const maScore = scoreMA(latest, techData)
  details['MA趋势'] = { 分值: maScore, 满分: 25 }
  total += maScore

  // 2. MACD 动量（25 分）
  const macdScore = scoreMACD(latest, prev, techData)
  details['MACD动量'] = { 分值: macdScore, 满分: 25 }
  total += macdScore

  // 3. RSI 强弱（20 分）
  const rsiScore = scoreRSI(latest)
  details['RSI强弱'] = { 分值: rsiScore, 满分: 20 }
  total += rsiScore

  // 4. KDJ 指标（15 分）
  const kdjScore = scoreKDJ(latest, prev)
  details['KDJ指标'] = { 分值: kdjScore, 满分: 15 }
  total += kdjScore

  // 5. BOLL 布林带（15 分）
  const bollScore = scoreBOLL(latest)
  details['BOLL布林带'] = { 分值: bollScore, 满分: 15 }
  total += bollScore

  return {
    score: clamp(round1(total)),
    details,
  }
}

/**
 * MA 均线趋势评分
 */
function scoreMA(latest, techData) {
  let score = 0
  const price = latest.close
  const ma5 = latest.ma5
  const ma10 = latest.ma10
  const ma20 = latest.ma20
  const ma60 = latest.ma60

  if (!ma5 || !ma10 || !ma20) return 12  // 数据不足，中性

  // 价格在均线之上（多头排列）
  if (price > ma5) score += 5
  if (price > ma10) score += 5
  if (price > ma20) score += 5
  if (ma60 && price > ma60) score += 5

  // 均线多头排列（短期 > 长期）
  if (ma5 > ma10 && ma10 > ma20) score += 5

  return clamp(score, 0, 25)
}

/**
 * MACD 动量评分
 */
function scoreMACD(latest, prev, techData) {
  let score = 12  // 中性基础分
  const dif = latest.dif
  const dea = latest.dea
  const macd = latest.macd
  const prevDif = prev.dif
  const prevDea = prev.dea

  if (dif === null || dea === null || macd === null) return 12

  // DIF 在零轴之上（强势）
  if (dif > 0) score += 5
  else score -= 3

  // 金叉（DIF 上穿 DEA）
  if (prevDif <= prevDea && dif > dea) score += 8
  // 死叉（DIF 下穿 DEA）
  else if (prevDif >= prevDea && dif < dea) score -= 5

  // MACD 柱由负转正
  if (macd > 0 && prev.macd <= 0) score += 5

  return clamp(score, 0, 25)
}

/**
 * RSI 强弱评分
 */
function scoreRSI(latest) {
  const rsi = latest.rsi
  if (rsi === null || rsi === undefined) return 10

  // RSI 在 50-70 区间最佳（强势但不超买）
  if (rsi >= 50 && rsi <= 70) return 20
  if (rsi >= 40 && rsi < 50) return 15
  if (rsi > 70 && rsi <= 80) return 12  // 超买风险
  if (rsi >= 30 && rsi < 40) return 10
  if (rsi > 80) return 5  // 严重超买
  return 8  // rsi < 30，超卖
}

/**
 * KDJ 指标评分
 */
function scoreKDJ(latest, prev) {
  let score = 7  // 中性基础分
  const k = latest.k
  const d = latest.d
  const j = latest.j
  const prevK = prev.k
  const prevD = prev.d

  if (k === null || d === null) return 7

  // K 在 20-80 区间较健康
  if (k >= 20 && k <= 80) score += 3

  // 金叉（K 上穿 D）
  if (prevK <= prevD && k > d) score += 5
  // 死叉
  else if (prevK >= prevD && k < d) score -= 3

  // J 值超买超卖
  if (j > 100) score -= 2  // 超买
  if (j < 0) score += 2  // 超卖反弹机会

  return clamp(score, 0, 15)
}

/**
 * BOLL 布林带评分
 */
function scoreBOLL(latest) {
  const price = latest.close
  const upper = latest.boll_upper
  const mid = latest.boll_mid
  const lower = latest.boll_lower

  if (!upper || !mid || !lower) return 7

  // 价格在中轨和上轨之间较佳
  if (price >= mid && price <= upper) return 15
  if (price > upper) return 8  // 突破上轨，可能回调
  if (price < lower) return 10  // 跌破下轨，可能反弹
  return 10  // 中轨下方
}

/**
 * 资金面评分（满分 100）
 */
function scoreCapital(techData, stockInfo) {
  if (!techData || techData.length < 10) {
    return { score: 50, details: { 说明: '数据不足，中性评分' } }
  }

  const details = {}
  let total = 0

  // 1. 量价配合（30 分）
  const volPriceScore = scoreVolumePrice(techData, stockInfo)
  details['量价配合'] = { 分值: volPriceScore, 满分: 30 }
  total += volPriceScore

  // 2. 涨跌动量（25 分）
  const momentumScore = scoreMomentum(techData, stockInfo)
  details['涨跌动量'] = { 分值: momentumScore, 满分: 25 }
  total += momentumScore

  // 3. 换手率活跃度（20 分）
  const turnoverScore = scoreTurnover(stockInfo)
  details['换手率'] = { 分值: turnoverScore, 满分: 20 }
  total += turnoverScore

  // 4. 成交额强度（25 分）
  const amountScore = scoreAmount(techData, stockInfo)
  details['成交额'] = { 分值: amountScore, 满分: 25 }
  total += amountScore

  return {
    score: clamp(round1(total)),
    details,
  }
}

/**
 * 量价配合评分
 */
function scoreVolumePrice(techData, stockInfo) {
  let score = 15  // 中性基础分

  const recent = techData.slice(-5)
  if (recent.length < 5) return 15

  // 上涨日成交量 > 下跌日成交量
  let upVol = 0, downVol = 0
  for (const day of recent) {
    if (day.close > day.open) upVol += day.volume
    else downVol += day.volume
  }

  if (upVol > downVol * 1.2) score += 10
  else if (upVol > downVol) score += 5
  else if (downVol > upVol * 1.2) score -= 5

  // 今日放量（相对 5 日均量）
  const today = techData[techData.length - 1]
  const avgVol = recent.slice(0, -1).reduce((s, d) => s + d.volume, 0) / 4
  if (today.volume > avgVol * 1.3) score += 5

  return clamp(score, 0, 30)
}

/**
 * 涨跌动量评分
 */
function scoreMomentum(techData, stockInfo) {
  const changePct = stockInfo.change_pct || 0
  
  // 今日涨跌幅适中（0-3% 最佳）
  if (changePct >= 0 && changePct <= 3) return 25
  if (changePct > 3 && changePct <= 5) return 20
  if (changePct > 5) return 12  // 涨太多，可能回调
  if (changePct >= -2 && changePct < 0) return 18
  if (changePct >= -5 && changePct < -2) return 10
  return 5  // 跌幅过大
}

/**
 * 换手率活跃度评分
 */
function scoreTurnover(stockInfo) {
  const turnover = stockInfo.turnover_rate || 0

  // 换手率 2-8% 较活跃
  if (turnover >= 2 && turnover <= 8) return 20
  if (turnover >= 1 && turnover < 2) return 15
  if (turnover > 8 && turnover <= 15) return 12  // 过于活跃
  if (turnover > 15) return 5  // 异常活跃，风险
  return 10  // 换手率过低
}

/**
 * 成交额强度评分
 */
function scoreAmount(techData, stockInfo) {
  const recent = techData.slice(-10)
  if (recent.length < 10) return 12

  // 计算近 10 日平均成交额
  const amounts = recent.map(d => d.close * d.volume)
  const avgAmount = amounts.reduce((s, a) => s + a, 0) / amounts.length

  // 成交额越大越好（相对全市场）
  // 这里简化处理，实际应该对比全市场排名
  if (avgAmount > 1e9) return 25  // 10 亿以上
  if (avgAmount > 5e8) return 20  // 5 亿以上
  if (avgAmount > 1e8) return 15  // 1 亿以上
  if (avgAmount > 5e7) return 10  // 5000 万以上
  return 5
}

/**
 * 基本面评分（满分 100）
 */
function scoreFundamental(stockInfo) {
  const details = {}
  let total = 0

  // 1. PE 估值（35 分）
  const peScore = scorePE(stockInfo.pe)
  details['PE估值'] = { 分值: peScore, 满分: 35 }
  total += peScore

  // 2. PB 估值（30 分）
  const pbScore = scorePB(stockInfo.pb)
  details['PB估值'] = { 分值: pbScore, 满分: 30 }
  total += pbScore

  // 3. 市值规模（35 分）
  const capScore = scoreMarketCap(stockInfo.market_cap)
  details['市值规模'] = { 分值: capScore, 满分: 35 }
  total += capScore

  return {
    score: clamp(round1(total)),
    details,
  }
}

/**
 * PE 估值评分
 */
function scorePE(pe) {
  if (!pe || pe <= 0) return 10  // 亏损或无数据

  // PE 10-25 较合理
  if (pe >= 10 && pe <= 25) return 35
  if (pe >= 5 && pe < 10) return 28  // 低估值
  if (pe > 25 && pe <= 40) return 22
  if (pe > 40 && pe <= 60) return 15
  if (pe > 60) return 8  // 高估值风险
  return 15
}

/**
 * PB 估值评分
 */
function scorePB(pb) {
  if (!pb || pb <= 0) return 10

  // PB 1-3 较合理
  if (pb >= 1 && pb <= 3) return 30
  if (pb > 0 && pb < 1) return 25  // 破净
  if (pb > 3 && pb <= 5) return 20
  if (pb > 5 && pb <= 8) return 12
  if (pb > 8) return 5
  return 15
}

/**
 * 市值规模评分
 */
function scoreMarketCap(marketCap) {
  if (!marketCap) return 15

  // 市值单位：万元
  const capYi = marketCap / 10000  // 转换为亿

  // 100-1000 亿较佳
  if (capYi >= 100 && capYi <= 1000) return 35
  if (capYi > 1000 && capYi <= 5000) return 28  // 大盘股
  if (capYi > 5000) return 20  // 超大盘
  if (capYi >= 50 && capYi < 100) return 25
  if (capYi >= 20 && capYi < 50) return 18
  if (capYi < 20) return 10  // 小盘股风险
  return 15
}

/**
 * 信号判定
 */
function deriveSignal(totalScore) {
  if (totalScore >= 80) return { signal: '强烈买入', signalLevel: 2 }
  if (totalScore >= 65) return { signal: '买入', signalLevel: 1 }
  if (totalScore >= 45) return { signal: '观望', signalLevel: 0 }
  if (totalScore >= 35) return { signal: '卖出', signalLevel: -1 }
  return { signal: '强烈卖出', signalLevel: -2 }
}

/**
 * 生成评分摘要
 */
function generateSummary(total, signal, dimTech, dimCap, dimFund) {
  const parts = []
  
  if (dimTech.score >= 70) parts.push('技术面强势')
  else if (dimTech.score <= 40) parts.push('技术面偏弱')
  
  if (dimCap.score >= 70) parts.push('资金流入明显')
  else if (dimCap.score <= 40) parts.push('资金面疲软')
  
  if (dimFund.score >= 70) parts.push('估值合理')
  else if (dimFund.score <= 40) parts.push('估值偏高')

  const summary = parts.length > 0 
    ? `${signal}，${parts.join('，')}`
    : `${signal}，综合评分 ${total}`

  return summary
}

/**
 * 简化评分（用于快速排序，不计算完整指标）
 * @param {Object} stockInfo - 实时行情
 * @returns {number} 简化评分（0-100）
 */
export function roughScore(stockInfo) {
  const changePct = stockInfo.change_pct || 0
  const turnover = stockInfo.turnover_rate || 0
  const pe = stockInfo.pe || 0

  // 动量分（涨跌幅）
  let momentum = 50
  if (changePct > 0) momentum = 50 + Math.min(changePct * 5, 30)
  else momentum = 50 + Math.max(changePct * 5, -30)

  // 换手率分
  let turnoverScore = 50
  if (turnover >= 2 && turnover <= 8) turnoverScore = 80
  else if (turnover >= 1 && turnover <= 10) turnoverScore = 65
  else turnoverScore = 40

  // PE 分
  let peScore = 50
  if (pe > 0 && pe <= 25) peScore = 80
  else if (pe > 25 && pe <= 40) peScore = 60
  else if (pe > 40) peScore = 30

  return clamp(round1(momentum * 0.4 + turnoverScore * 0.3 + peScore * 0.3))
}
