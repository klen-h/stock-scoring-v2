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
  // 确定性四舍五入：+1e-9 抵消浮点累加噪声（真值恰在 .x5 平局点时，
  // 纯 Math.round 会因累加误差方向不同而与后端结果不一致；
  // 合法分数间隔 ≥ 0.0001，1e-9 不会误伤）
  return Math.floor(v * 10 + 0.5 + 1e-9) / 10
}

function round2(v) {
  if (v === null || v === undefined || isNaN(v)) return null
  return Math.round(v * 100) / 100
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

  // 加权求和（与后端严格一致：每个维度分先乘权重并保留 1 位小数，再求和取 1 位）
  const total = round1(
    round1(dimTech.score * W_TECHNICAL) +
    round1(dimCap.score * W_CAPITAL) +
    round1(dimFund.score * W_FUNDAMENTAL)
  )

  // 信号判定（与后端一致：检查是否有维度极差）
  const anyExtremeLow = [dimTech, dimCap, dimFund].some(d => d.score < 20)
  const { signal, signalLevel } = deriveSignal(total, anyExtremeLow)

  // 提取加分/扣分因素（与后端一致）
  const { factorsUp, factorsDown } = extractFactors(dimTech, dimCap, dimFund)

  // 计算买入时机（与后端一致）
  const buyPoint = calcBuyPoint(technicalData)

  // 生成摘要（与后端一致）
  const summary = generateSummary(name, total, signal, factorsUp, factorsDown)

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
    factors_up: factorsUp,
    factors_down: factorsDown,
    buy_point: buyPoint,
    change_pct: stockInfo.change_pct || 0,
  }
}

/**
 * 技术面总评（与后端一致）
 * 5 个子指标：MA趋势(25) + MACD(25) + RSI(20) + KDJ(15) + 布林带(15)
 * 子分均为 0~100 尺度（中性 50），按满分占比折算后求和
 */
function scoreTechnical(techData) {
  if (!techData || techData.length < 30) {
    return { score: 50, details: { 说明: '数据不足，中性评分' } }
  }

  const latest = techData[techData.length - 1]
  const prev = techData[techData.length - 2] || latest
  const details = {}
  const subScores = []

  // 1. MA 均线趋势（25 分）
  const maScore = scoreMA(latest, techData)
  details['MA趋势'] = { 分值: maScore, 满分: 25 }
  subScores.push([maScore, 25])

  // 2. MACD 动量（25 分）
  const macdScore = scoreMACD(latest, prev, techData)
  details['MACD动量'] = { 分值: macdScore, 满分: 25 }
  subScores.push([macdScore, 25])

  // 3. RSI 强弱（20 分）
  const rsiScore = scoreRSI(latest)
  details['RSI强弱'] = { 分值: rsiScore, 满分: 20 }
  subScores.push([rsiScore, 20])

  // 4. KDJ 指标（15 分）
  const kdjScore = scoreKDJ(latest, prev)
  details['KDJ指标'] = { 分值: kdjScore, 满分: 15 }
  subScores.push([kdjScore, 15])

  // 5. 布林带（15 分）
  const bollScore = scoreBOLL(latest)
  details['布林带'] = { 分值: bollScore, 满分: 15 }
  subScores.push([bollScore, 15])

  // 加权：每个子分 × (子满分/100)，再求和 → 技术面 0~100 分（与后端一致）
  const raw = subScores.reduce((sum, [s, w]) => sum + s * w / 100, 0)

  return {
    score: clamp(round1(raw)),
    details,
  }
}

/**
 * MA 均线趋势评分（满分 25，0~100 尺度，与后端一致）
 * 多空对称：多头加分、空头等额扣分，中性基准 50
 */
function scoreMA(latest, techData) {
  const price = latest.close || 0
  const ma5 = latest.ma5
  const ma10 = latest.ma10
  const ma20 = latest.ma20
  const ma60 = latest.ma60

  // 缺少关键均线数据，无法判断
  if (!ma5 || !ma10 || !ma20) return 50

  let score = 50
  // 价格 vs MA5
  if (price > ma5) score += 5
  else if (price < ma5) score -= 5
  // 价格 vs MA20
  if (price > ma20) score += 5
  else if (price < ma20) score -= 5
  // 多头 / 空头排列
  if (ma5 > ma10 && ma10 > ma20) score += 8        // 完美多头
  else if (ma5 > ma10) score += 4                    // 部分多头
  else if (ma5 < ma10 && ma10 < ma20) score -= 8    // 完美空头
  else if (ma5 < ma10) score -= 4                    // 部分空头
  // MA60 支撑 / 压制
  if (ma60) {
    if (price > ma60) score += 4
    else if (price > ma60 * 0.97) score += 2        // 接近 MA60（差 3% 以内）
    else if (price < ma60) score -= 4
  }
  // MA5 金叉 / 死叉 MA10（看前一天 → 今天的变化）
  if (techData.length >= 2) {
    const prev = techData[techData.length - 2]
    const pMa5 = prev.ma5, pMa10 = prev.ma10
    if (pMa5 && pMa10 && ma5 && ma10) {
      if (pMa5 <= pMa10 && ma5 > ma10) score += 3
      else if (pMa5 >= pMa10 && ma5 < ma10) score -= 3
    }
  }

  return clamp(score, 0, 100)
}

/**
 * MACD 动量评分（满分 25，0~100 尺度，与后端一致）
 */
function scoreMACD(latest, prev, techData) {
  const dif = latest.dif
  const dea = latest.dea
  const macd = latest.macd

  if (dif === null || dif === undefined || dea === null || dea === undefined) return 50

  let score = 50

  // DIF 在零轴上方（中期多头）
  if (dif > 0) score += 5
  else score -= 5

  // MACD 柱状体（红绿柱）
  if (macd !== null && macd !== undefined && macd > 0) {
    score += 4
    // 红柱放大（动能增强）
    const prevMacd = prev.macd
    if (prevMacd !== null && prevMacd !== undefined && macd > prevMacd) score += 3
  } else if (macd !== null && macd !== undefined) {
    score -= 4   // 绿柱扣分
  }

  // DIF > DEA（短期多头）
  if (dif > dea) score += 4
  else score -= 4

  // 金叉/死叉判定（看前一天 → 今天的变化）
  const pDif = prev.dif, pDea = prev.dea
  if (pDif !== null && pDif !== undefined && pDea !== null && pDea !== undefined) {
    if (pDif <= pDea && dif > dea) score += 5   // 金叉
    else if (pDif >= pDea && dif < dea) score -= 5   // 死叉
  }

  // 连续 N 日 MACD 为正（趋势确认）
  if (techData.length >= 5) {
    let positiveDays = 0
    for (let i = techData.length - 5; i < techData.length; i++) {
      const m = techData[i].macd
      if (m !== null && m !== undefined && m > 0) positiveDays++
    }
    if (positiveDays >= 5) score += 4   // 连续 5 天红柱
    else if (positiveDays >= 3) score += 2
  }

  return clamp(score, 0, 100)
}

/**
 * RSI 强弱评分（满分 20，0~100 尺度，与后端一致）—— 趋势跟随口径
 */
function scoreRSI(latest) {
  const rsi = latest.rsi
  if (rsi === null || rsi === undefined) return 50

  // 按区间给分：强势区高分，弱势区低分（与后端一致）
  if (rsi >= 80) return 80   // 极强，但接近阶段性过热，略低于 70~80
  if (rsi >= 70) return 90   // 强势多头（最佳）
  if (rsi >= 60) return 78   // 偏强
  if (rsi >= 50) return 62   // 中性偏强
  if (rsi >= 40) return 50   // 中性
  if (rsi >= 30) return 38   // 偏弱
  if (rsi >= 20) return 22   // 弱势
  return 15                  // 极弱
}

/**
 * KDJ 指标评分（满分 15，0~100 尺度，与后端一致）—— 趋势跟随口径
 */
function scoreKDJ(latest, prev) {
  const k = latest.k
  const d = latest.d
  const j = latest.j

  if (k === null || k === undefined || d === null || d === undefined) return 50

  let score = 50

  // K vs D（趋势方向）
  if (k > d) score += 3
  else score -= 3

  // 金叉 / 死叉
  const pK = prev.k, pD = prev.d
  if (pK !== null && pK !== undefined && pD !== null && pD !== undefined) {
    if (pK <= pD && k > d) score += 5   // 金叉（多头）
    else if (pK >= pD && k < d) score -= 5   // 死叉（空头）
  }

  // K/D 绝对位置（趋势强度）：高位=强势，低位=弱势
  if (k > 80 && d > 80) score += 3   // 强势区
  else if (k < 20 && d < 20) score -= 3   // 弱势区

  // J 值极端：强势上攻给小分，极弱扣分（与后端一致）
  if (j !== null && j !== undefined) {
    if (j > 100) score += 2
    else if (j < 0) score -= 2
  }

  return clamp(score, 0, 100)
}

/**
 * 布林带评分（满分 15，0~100 尺度，与后端一致）—— 趋势跟随口径
 */
function scoreBOLL(latest) {
  const price = latest.close || 0
  const upper = latest.boll_upper
  const mid = latest.boll_mid
  const lower = latest.boll_lower

  if (!upper || !mid || !lower || price <= 0) return 50

  const bandwidth = upper - lower   // 带宽
  if (bandwidth <= 0) return 50

  let score = 50

  // 价格在布林带中的相对位置：0=下轨(弱), 1=上轨(强)
  const position = (price - lower) / bandwidth

  // 接近上轨加分（强势），接近下轨扣分（弱势）
  if (position > 0.8) score += 6    // 接近/突破上轨，强势
  else if (position > 0.6) score += 4   // 偏强
  else if (position > 0.4) score += 1   // 中轨附近
  else if (position > 0.2) score -= 3   // 偏弱
  else if (position > 0) score -= 5     // 接近下轨，弱势
  else score -= 6                       // 跌破下轨，破位

  // 价格在中轨上方 = 强势
  if (price > mid) score += 3

  return clamp(score, 0, 100)
}

/**
 * 资金面总评（与后端一致）
 * 4 个子指标：量价配合(30) + 涨跌动量(25) + 换手率(20) + 成交额(25)
 */
function scoreCapital(techData, stockInfo) {
  const details = {}
  const subScores = []

  // 1. 量价配合（30 分）
  const volPriceScore = scoreVolumePrice(techData, stockInfo)
  details['量价配合'] = { 分值: volPriceScore, 满分: 30 }
  subScores.push([volPriceScore, 30])

  // 2. 涨跌动量（25 分）
  const momentumScore = scoreMomentum(techData, stockInfo)
  details['涨跌动量'] = { 分值: momentumScore, 满分: 25 }
  subScores.push([momentumScore, 25])

  // 3. 换手率活跃度（20 分）
  const turnoverScore = scoreTurnover(stockInfo)
  details['换手率'] = { 分值: turnoverScore, 满分: 20 }
  subScores.push([turnoverScore, 20])

  // 4. 成交额强度（25 分）
  const amountScore = scoreAmount(techData, stockInfo)
  details['成交额'] = { 分值: amountScore, 满分: 25 }
  subScores.push([amountScore, 25])

  const raw = subScores.reduce((sum, [s, w]) => sum + s * w / 100, 0)

  return {
    score: clamp(round1(raw)),
    details,
  }
}

/**
 * 量价配合评分（满分 30，0~100 尺度，与后端一致）
 * 核心逻辑：“放量上涨、缩量下跌”是好形态
 */
function scoreVolumePrice(techData, stockInfo) {
  if (!techData || techData.length < 10) return 50

  let score = 50
  const recent = techData.slice(-5)   // 最近 5 天

  let upVol = 0, downVol = 0, upDays = 0, downDays = 0

  // 统计最近 5 天的涨跌量（第一个没有前一天可比，跳过）
  for (let i = 1; i < recent.length; i++) {
    const chg = recent[i].close - recent[i - 1].close
    const vol = recent[i].volume
    if (chg > 0) { upVol += vol; upDays++ }
    else if (chg < 0) { downVol += vol; downDays++ }
  }

  // 放量上涨判断（上涨日量 vs 下跌日量）
  if (upDays > 0 && downDays > 0 && downVol > 0) {
    const ratio = upVol / downVol
    if (ratio > 2.0) score += 15      // 上涨日量是下跌日的 2 倍以上（强势）
    else if (ratio > 1.5) score += 10
    else if (ratio > 1.0) score += 5
    else score -= 5                    // 下跌日量更大（弱势）
  } else if (upDays > 0 && downDays === 0) {
    score += 10                        // 5 天全涨
  } else if (downDays > 0 && upDays === 0) {
    score -= 10                        // 5 天全跌
  }

  // 今天是否放量（vs 前 5 日均量）
  if (techData.length >= 6) {
    const volToday = techData[techData.length - 1].volume
    const volAvg5 = techData.slice(-6, -1).reduce((s, d) => s + d.volume, 0) / 5
    if (volAvg5 > 0) {
      const volRatio = volToday / volAvg5
      if (volRatio > 2.0) score += 8   // 爆量
      else if (volRatio > 1.5) score += 5
      else if (volRatio < 0.5) score -= 3   // 严重缩量
    }
  }

  return clamp(score, 0, 100)
}

/**
 * 涨跌动量评分（满分 25，0~100 尺度，与后端一致）
 * 综合 3 个时间维度：今日 + 近 5 日 + 近 20 日涨跌幅
 */
function scoreMomentum(techData, stockInfo) {
  let score = 50

  // 今日涨跌幅（来自实时数据）
  const changePct = stockInfo.change_pct || 0
  if (changePct > 0) score += Math.min(changePct * 2, 10)
  else if (changePct < 0) score += Math.max(changePct * 2, -10)

  const n = techData ? techData.length : 0

  // 5 日涨跌幅
  if (n >= 6) {
    const chg5d = (techData[n - 1].close - techData[n - 6].close) / techData[n - 6].close * 100
    if (chg5d > 5) score += 8
    else if (chg5d > 2) score += 5
    else if (chg5d > 0) score += 2
    else if (chg5d < -5) score -= 8
    else if (chg5d < -2) score -= 5
  }

  // 20 日涨跌幅
  if (n >= 21) {
    const chg20d = (techData[n - 1].close - techData[n - 21].close) / techData[n - 21].close * 100
    if (chg20d > 10) score += 7
    else if (chg20d > 5) score += 4
    else if (chg20d < -10) score -= 7
    else if (chg20d < -5) score -= 4
  }

  return clamp(score, 0, 100)
}

/**
 * 换手率活跃度评分（满分 20，0~100 尺度，与后端一致）
 */
function scoreTurnover(stockInfo) {
  const turnover = stockInfo.turnover_rate || 0
  if (!turnover) return 50

  // 按区间直接返回固定分（与后端一致）
  if (turnover >= 1.0 && turnover <= 3.0) return 90   // 温和换手（最佳）
  if (turnover > 3.0 && turnover <= 5.0) return 80    // 活跃
  if (turnover > 5.0 && turnover <= 8.0) return 65    // 较活跃
  if (turnover > 8.0 && turnover <= 15.0) return 45   // 偏高（注意风险）
  if (turnover > 15.0) return 25                      // 异常换手（可能出货）
  if (turnover >= 0.3 && turnover < 1.0) return 60    // 偏低但可接受
  return 30                                           // 极低
}

/**
 * 成交额强度评分（满分 25，0~100 尺度，与后端一致）
 * 今天成交额 vs 近 10 日均额 + 近 5 日均额 vs 前 5 日均额（趋势）
 */
function scoreAmount(techData, stockInfo) {
  if (!techData || techData.length < 10) return 50

  let score = 50
  // 近 10 日每日成交额（近似：close × volume）
  const amounts = techData.slice(-10).map(d => d.close * d.volume)
  const avgAmount = amounts.reduce((s, a) => s + a, 0) / amounts.length
  const todayAmount = amounts[amounts.length - 1]

  // 今天 vs 近 10 日均额
  if (avgAmount > 0) {
    const ratio = todayAmount / avgAmount
    if (ratio > 2.0) score += 12
    else if (ratio > 1.5) score += 8
    else if (ratio > 1.0) score += 3
    else if (ratio < 0.5) score -= 8
  }

  // 金额趋势：近 5 日 vs 前 5 日
  if (amounts.length >= 10) {
    const recentAvg = amounts.slice(-5).reduce((s, a) => s + a, 0) / 5
    const prevAvg = amounts.slice(0, 5).reduce((s, a) => s + a, 0) / 5
    if (prevAvg > 0) {
      const trendRatio = recentAvg / prevAvg
      if (trendRatio > 1.3) score += 8    // 资金持续流入
      else if (trendRatio > 1.1) score += 4
      else if (trendRatio < 0.7) score -= 8   // 资金持续流出
      else if (trendRatio < 0.9) score -= 3
    }
  }

  return clamp(score, 0, 100)
}

/**
 * 基本面评分（满分 100）
 * 与后端保持一致：PE(30) + PB(20) + 市值(25) + 振幅(25)
 */
function scoreFundamental(stockInfo) {
  const details = {}
  const subScores = []

  // 1. PE 估值（30 分）- 按市值分层
  const peScore = scorePE(stockInfo.pe, stockInfo.market_cap)
  details['PE估值'] = { 分值: peScore, 满分: 30 }
  subScores.push([peScore, 30])

  // 2. PB 估值（20 分）
  const pbScore = scorePB(stockInfo.pb)
  details['PB估值'] = { 分值: pbScore, 满分: 20 }
  subScores.push([pbScore, 20])

  // 3. 市值规模（25 分）
  const capScore = scoreMarketCap(stockInfo.market_cap)
  details['市值规模'] = { 分值: capScore, 满分: 25 }
  subScores.push([capScore, 25])

  // 4. 振幅（25 分）
  const ampScore = scoreVolatility(stockInfo)
  details['振幅'] = { 分值: ampScore, 满分: 25 }
  subScores.push([ampScore, 25])

  // 加权计算（与后端一致）
  const raw = subScores.reduce((sum, [score, weight]) => sum + score * weight / 100, 0)

  return {
    score: clamp(round1(raw)),
    details,
  }
}

/**
 * PE 估值评分（30 分）- 按市值分层（成长股/价值股）
 */
function scorePE(pe, marketCap) {
  if (!pe || pe <= 0) return 40  // 亏损或无数据，中性偏低

  // 市值（万元 → 亿元）
  const capYi = (marketCap || 0) / 10000
  const isGrowthOriented = capYi > 0 && capYi < 200  // 中小盘按成长股对待

  if (isGrowthOriented) {
    // 成长股曲线：PE 中高段不重罚
    if (pe < 15) return 90
    if (pe < 30) return 80
    if (pe < 60) return 70
    if (pe < 100) return 50
    return 30
  } else {
    // 价值股/大盘股曲线：严格标准
    if (pe < 10) return 95
    if (pe < 15) return 85
    if (pe < 25) return 75
    if (pe < 40) return 55
    if (pe < 60) return 40
    if (pe < 100) return 25
    return 15
  }
}

/**
 * PB 估值评分（20 分）
 */
function scorePB(pb) {
  if (!pb || pb <= 0) return 40

  if (pb < 1.0) return 95   // 破净
  if (pb < 1.5) return 80
  if (pb < 2.5) return 65
  if (pb < 4.0) return 50
  if (pb < 7.0) return 35
  return 20
}

/**
 * 市值规模评分（25 分）- 偏好中小盘
 */
function scoreMarketCap(marketCap) {
  const cap = marketCap || 0
  const capYi = cap / 10000  // 万元 → 亿

  if (capYi <= 0) return 50
  if (capYi < 20) return 75   // 小盘，弹性大
  if (capYi < 50) return 85   // 中小盘（最佳区间）
  if (capYi < 200) return 70  // 中盘
  if (capYi < 1000) return 55 // 大盘
  return 40                    // 超大盘，弹性不足
}

/**
 * 振幅/波动评分（满分 25，0~100 尺度，与后端一致）
 */
function scoreVolatility(stockInfo) {
  const amp = stockInfo.amplitude || 0

  // 无振幅数据时给中性分（与后端一致）
  if (amp <= 0) return 50

  if (amp < 1.0) return 40   // 极低波动，缺乏机会
  if (amp < 2.0) return 65   // 低波动
  if (amp < 4.0) return 85   // 适中波动（最佳）
  if (amp < 6.0) return 70   // 偏高波动
  if (amp < 9.0) return 50   // 高波动
  return 30                  // 极高波动，风险大
}

/**
 * 信号判定（与后端一致）
 */
function deriveSignal(totalScore, anyExtremeLow) {
  if (totalScore >= 80 && !anyExtremeLow) return { signal: '强烈买入', signalLevel: 2 }
  if (totalScore >= 65) return { signal: '买入', signalLevel: 1 }
  if (totalScore >= 45) return { signal: '观望', signalLevel: 0 }
  if (totalScore <= 20) return { signal: '强烈卖出', signalLevel: -2 }
  if (totalScore <= 35) return { signal: '卖出', signalLevel: -1 }
  return { signal: '观望', signalLevel: 0 }
}

/**
 * 提取加分/扣分因素（与后端一致）
 */
function extractFactors(dimTech, dimCap, dimFund) {
  const ups = []
  const downs = []

  const labelMap = {
    'MA趋势': '均线多头排列',
    'MACD动量': 'MACD金叉',
    'RSI强弱': 'RSI处于强势区间',
    'KDJ指标': 'KDJ金叉走强',
    '布林带': '价格突破布林上轨',
    '量价配合': '量价齐升',
    '涨跌动量': '短期趋势向好',
    '换手率': '换手率健康',
    '成交额': '资金持续流入',
    'PE估值': '估值合理偏低',
    'PB估值': 'PB处于低位',
    '市值规模': '市值适中弹性好',
    '振幅': '波动率适中',
  }

  const downMap = {
    'MA趋势': '均线空头排列',
    'MACD动量': 'MACD死叉',
    'RSI强弱': 'RSI处于弱势区间',
    'KDJ指标': 'KDJ死叉走弱',
    '布林带': '价格跌破布林中轨',
    '量价配合': '量价背离',
    '涨跌动量': '短期趋势走弱',
    '换手率': '换手率异常偏高',
    '成交额': '资金持续流出',
    'PE估值': '估值偏高',
    'PB估值': 'PB偏高',
    '市值规模': '市值过大弹性不足',
    '振幅': '波动率过高风险大',
  }

  // 遍历每个维度的每个子项，按得分率分类（与后端一致）
  // 注意：子项「分值」统一在 0~100 尺度（满分字段仅作权重/分值占比，不再当上限），
  // 所以得分率直接用 分值/100。
  for (const dim of [dimTech, dimCap, dimFund]) {
    if (!dim.details) continue
    for (const [name, detail] of Object.entries(dim.details)) {
      if (name === '说明') continue
      const pct = (detail.分值 ?? 50) / 100   // 得分率：子项分统一 0~100 尺度（与后端一致）
      if (pct >= 0.75 && labelMap[name]) ups.push(labelMap[name])
      else if (pct <= 0.35 && downMap[name]) downs.push(downMap[name])
    }
  }

  return { factorsUp: ups.slice(0, 5), factorsDown: downs.slice(0, 5) }   // 最多各 5 个
}

/**
 * 计算买入时机（与后端一致）
 */
function calcBuyPoint(techData) {
  if (!techData || techData.length < 30) return {}

  const latest = techData[techData.length - 1]
  const price = latest.close || 0
  if (!price) return {}

  const ma20 = latest.ma20
  const ma60 = latest.ma60
  const bollMid = latest.boll_mid
  const bollLower = latest.boll_lower

  const supports = []
  if (ma20 && ma20 < price) supports.push({ name: 'MA20', price: round2(ma20) })
  if (ma60 && ma60 < price) supports.push({ name: 'MA60', price: round2(ma60) })
  if (bollLower && bollLower < price) supports.push({ name: '布林下轨', price: round2(bollLower) })
  if (bollMid && bollMid < price) supports.push({ name: '布林中轨', price: round2(bollMid) })

  const recentLows = techData.slice(-20).filter(k => k.low > 0).map(k => k.low)
  if (recentLows.length) {
    const low20 = Math.min(...recentLows)
    if (low20 < price) supports.push({ name: '20日低点', price: round2(low20) })
  }

  supports.sort((a, b) => b.price - a.price)

  let buyLow = null
  let buyHigh = null

  if (supports.length) {
    buyHigh = supports[0].price
    const strongSupports = supports.filter(s => s.name === 'MA60' || s.name === '20日低点').map(s => s.price)
    buyLow = strongSupports.length ? Math.max(...strongSupports) : supports[supports.length - 1].price
    if (buyLow >= buyHigh) buyLow = round2(buyHigh * 0.97)
  }

  let timing
  let deviation = 0
  if (buyHigh === null) {
    // 没有支撑位（价格已在低位或数据不足）
    timing = '适合介入'
  } else if (price <= buyHigh) {
    // 当前价已跌入买入区间内或以下
    timing = '适合介入'
    deviation = round2((price - buyHigh) / buyHigh * 100)
  } else {
    deviation = round2((price - buyHigh) / buyHigh * 100)
    if (deviation <= 2) timing = '适合介入'   // 离最近支撑 2% 以内，可以介入
    else if (deviation <= 6) timing = '等回调'   // 离支撑还有一段距离
    else timing = '追高风险'   // 远离所有支撑
  }

  // 参考买入价（区间中轨，与后端一致）
  let refPrice = null
  if (buyLow !== null && buyHigh !== null) {
    refPrice = round2((buyLow + buyHigh) / 2)
  }

  return {
    buy_range: buyLow !== null && buyHigh !== null ? [buyLow, buyHigh] : null,
    ref_price: refPrice,
    current_price: round2(price),
    supports: supports.slice(0, 5),   // 最多 5 个支撑位（与后端一致）
    deviation,
    buy_timing: timing,
  }
}

/**
 * 生成评分摘要（与后端一致）
 */
function generateSummary(name, total, signal, factorsUp, factorsDown) {
  const parts = [`${name} 综合评分 ${total}，信号：${signal}。`]
  if (factorsUp.length) parts.push(`加分因素：${factorsUp.join('、')}。`)
  if (factorsDown.length) parts.push(`风险提示：${factorsDown.join('、')}。`)
  return parts.join(' ')
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
