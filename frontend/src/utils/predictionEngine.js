/**
 * 今日预测引擎
 * 
 * 基于个股历史走势（技术指标 + 行情数据），生成开盘前综合预测，
 * 包含多种可能走向、对应策略，以及偏离度监控阈值。
 * 
 * 用法：
 *   const prediction = generatePrediction(technicalData, stockInfo, position)
 *   // technicalData: 技术指标数组，每项含 { date, close, ma5, ma10, ma20, ma60, dif, dea, macd, rsi, k, d, j, boll_upper, boll_mid, boll_lower, volume }
 *   // stockInfo: 实时行情 { price, change_pct, pe, pb, market_cap, turnover_rate, amplitude }
 *   // position (可选): 持仓 { cost, shares, high_water_mark }
 */

function round2(v) {
  if (v === null || v === undefined || isNaN(v)) return null
  return Math.round(v * 100) / 100
}

function clamp(v, lo = 0, hi = 100) {
  return Math.max(lo, Math.min(hi, v))
}

/**
 * 生成今日多场景预测
 */
export function generatePrediction(technicalData, stockInfo, position) {
  if (!technicalData || technicalData.length < 30) {
    return { error: '技术指标数据不足，无法生成预测' }
  }

  const latest = technicalData[technicalData.length - 1]
  const prev = technicalData[technicalData.length - 2] || latest
  const price = stockInfo?.price || latest.close || 0
  const n = technicalData.length

  // ── 1. 提取关键指标 ──
  const ma5 = latest.ma5
  const ma10 = latest.ma10
  const ma20 = latest.ma20
  const ma60 = latest.ma60
  const dif = latest.dif
  const dea = latest.dea
  const macd = latest.macd
  const rsi = latest.rsi
  const k = latest.k
  const d = latest.d
  const j = latest.j
  const bollUpper = latest.boll_upper
  const bollMid = latest.boll_mid
  const bollLower = latest.boll_lower
  const volume = latest.volume
  const turnover = stockInfo?.turnover_rate || 0
  const amplitude = stockInfo?.amplitude || 0

  // 近 5 日均量（用于量比）
  let volAvg5 = 0
  if (n >= 6) {
    let sum = 0
    for (let i = n - 6; i < n - 1; i++) sum += technicalData[i].volume || 0
    volAvg5 = sum / 5
  }
  const volRatio = volAvg5 > 0 ? (volume || 0) / volAvg5 : 1

  // 5日涨跌幅
  let chg5d = 0
  if (n >= 6) {
    chg5d = (latest.close - technicalData[n - 6].close) / technicalData[n - 6].close * 100
  }

  // 20日涨跌幅
  let chg20d = 0
  if (n >= 21) {
    chg20d = (latest.close - technicalData[n - 21].close) / technicalData[n - 21].close * 100
  }

  // ── 2. 计算多空得分 ──
  let bullish = 50  // 看涨基准分
  let bearish = 50  // 看跌基准分

  // ─── MA 均线系统 ───
  if (ma5 && ma10 && ma20) {
    if (ma5 > ma10 && ma10 > ma20) { bullish += 12; bearish -= 4 }       // 完美多头
    else if (ma5 > ma10) { bullish += 6; bearish -= 2 }                   // 部分多头
    else if (ma5 < ma10 && ma10 < ma20) { bullish -= 4; bearish += 12 }  // 完美空头
    else if (ma5 < ma10) { bullish -= 2; bearish += 6 }                  // 部分空头
  }
  if (price > ma5) { bullish += 4 } else if (price < ma5) { bearish += 4 }
  if (ma5 > ma20) { bullish += 4 } else if (ma5 < ma20) { bearish += 4 }
  // 金叉/死叉
  if (prev.ma5 && prev.ma10 && ma5 && ma10) {
    if (prev.ma5 <= prev.ma10 && ma5 > ma10) bullish += 5   // 金叉
    else if (prev.ma5 >= prev.ma10 && ma5 < ma10) bearish += 5  // 死叉
  }
  // MA60 支撑/压制
  if (ma60) {
    if (price > ma60) bullish += 4
    else if (price < ma60 * 0.97) bearish += 4
  }

  // ─── MACD 动量 ───
  if (dif !== null && dif !== undefined) {
    if (dif > 0) { bullish += 5 } else { bearish += 5 }
    if (dea !== null && dea !== undefined) {
      if (dif > dea) { bullish += 5 } else { bearish += 5 }
      // 金叉/死叉
      if (prev.dif !== null && prev.dif !== undefined && prev.dea !== null && prev.dea !== undefined) {
        if (prev.dif <= prev.dea && dif > dea) bullish += 6
        else if (prev.dif >= prev.dea && dif < dea) bearish += 6
      }
    }
  }
  if (macd !== null && macd !== undefined) {
    if (macd > 0) { bullish += 4 } else { bearish += 4 }
    // 红柱/绿柱放大
    if (prev.macd !== null && prev.macd !== undefined) {
      if (macd > 0 && macd > prev.macd) bullish += 3
      else if (macd < 0 && macd < prev.macd) bearish += 3
    }
  }

  // ─── RSI 强弱 ───
  if (rsi !== null && rsi !== undefined) {
    if (rsi > 70) { bullish += 8; bearish -= 3 }   // 强势但注意过热
    else if (rsi > 60) { bullish += 8; bearish -= 2 }
    else if (rsi > 50) { bullish += 4 }
    else if (rsi > 40) { bearish += 4 }
    else if (rsi > 30) { bearish += 6; bullish -= 2 }
    else { bearish += 8; bullish -= 4 }
  }

  // ─── KDJ ───
  if (k !== null && k !== undefined && d !== null && d !== undefined) {
    if (k > d) { bullish += 4 } else { bearish += 4 }
    if (prev.k !== null && prev.k !== undefined && prev.d !== null && prev.d !== undefined) {
      if (prev.k <= prev.d && k > d) bullish += 5   // KDJ 金叉
      else if (prev.k >= prev.d && k < d) bearish += 5  // KDJ 死叉
    }
    if (j !== null && j !== undefined) {
      if (j > 100) { bullish += 3 }  // 超买区
      else if (j < 0) { bearish += 3 }  // 超卖区
    }
  }

  // ─── 布林带 ───
  if (bollMid && bollUpper && bollLower) {
    const bandwidth = bollUpper - bollLower
    if (bandwidth > 0) {
      const position = (price - bollLower) / bandwidth
      if (position > 0.8) { bullish += 5 }      // 接近/突破上轨
      else if (position > 0.6) { bullish += 3 }
      else if (position > 0.4) { bullish += 1 }
      else if (position > 0.2) { bearish += 3 }
      else { bearish += 5 }                      // 接近/跌破下轨
    }
    if (price > bollMid) { bullish += 3 } else { bearish += 3 }
  }

  // ─── 量价关系 ───
  if (volRatio > 1.5) {
    if (chg5d > 0) bullish += 5   // 放量上涨 → 强势
    else bearish += 5              // 放量下跌 → 危险
  } else if (volRatio < 0.6) {
    if (chg5d > 0) bullish += 2   // 缩量上涨 → 可能无量上涨
    else bearish += 2              // 缩量下跌 → 卖盘枯竭，不一定坏
  }

  // ─── 中期趋势（5日/20日涨幅）───
  if (chg5d > 5) { bullish += 4 } else if (chg5d < -5) { bearish += 4 }
  if (chg20d > 10) { bullish += 4 } else if (chg20d < -10) { bearish += 4 }

  // ─── 振幅（波动率）───
  if (amplitude > 0) {
    if (amplitude < 1.5) { bearish += 2 }     // 极低波动，交投不活跃
    else if (amplitude > 6) { bearish += 3 }  // 高波动，风险大
    else if (amplitude >= 2 && amplitude <= 4) { bullish += 2 } // 适中波动，健康
  }

  // ─── 换手率 ───
  if (turnover > 0) {
    if (turnover > 15) { bearish += 3 }       // 异常换手
    else if (turnover >= 1 && turnover <= 5) { bullish += 2 }  // 温和换手
  }

  // ─── 3. 归一化概率 ───
  bullish = clamp(bullish, 0, 100)
  bearish = clamp(bearish, 0, 100)

  // 三场景概率分配
  let probBull, probBear, probSide
  const net = bullish - bearish  // -100 ~ 100

  if (net > 20) {
    probBull = Math.round(clamp(bullish, 40, 85))
    probBear = Math.round(100 - probBull)
    probSide = 0
    // 将一部分震荡概率从看涨中分出
    const sideShare = Math.round(probBull * 0.15)
    probBull -= sideShare
    probSide = sideShare
  } else if (net < -20) {
    probBear = Math.round(clamp(bearish, 40, 85))
    probBull = Math.round(100 - probBear)
    probSide = 0
    const sideShare = Math.round(probBear * 0.15)
    probBear -= sideShare
    probSide = sideShare
  } else {
    // 震荡市：多空拉锯，震荡概率最高
    probSide = Math.round(clamp(100 - Math.abs(net) * 1.5, 35, 70))
    const remaining = 100 - probSide
    const totalScore = bullish + bearish
    if (totalScore > 0) {
      probBull = Math.round(remaining * bullish / totalScore)
      probBear = remaining - probBull
    } else {
      probBull = remaining / 2
      probBear = remaining / 2
    }
  }

  // 确保总和 100
  const sum = probBull + probBear + probSide
  if (sum !== 100) {
    if (probSide >= 1) probSide += 100 - sum
    else if (probBull >= 1) probBull += 100 - sum
    else probBear += 100 - sum
  }

  // ─── 4. 关键价位计算 ───
  // 支撑位
  const supports = []
  if (ma20 && ma20 < price) supports.push({ price: round2(ma20), name: 'MA20' })
  if (ma60 && ma60 < price) supports.push({ price: round2(ma60), name: 'MA60' })
  if (bollMid && bollMid < price) supports.push({ price: round2(bollMid), name: '布林中轨' })
  if (bollLower && bollLower < price) supports.push({ price: round2(bollLower), name: '布林下轨' })
  // 20日最低价
  const recentLows = technicalData.slice(-20).filter(k => k.low > 0).map(k => k.low)
  if (recentLows.length) {
    const low20 = Math.min(...recentLows)
    if (low20 < price) supports.push({ price: round2(low20), name: '20日低点' })
  }
  supports.sort((a, b) => b.price - a.price)

  // 阻力位
  const resistances = []
  if (ma20 && ma20 > price) resistances.push({ price: round2(ma20), name: 'MA20' })
  if (ma60 && ma60 > price) resistances.push({ price: round2(ma60), name: 'MA60' })
  if (bollUpper && bollUpper > price) resistances.push({ price: round2(bollUpper), name: '布林上轨' })
  if (bollMid && bollMid > price) resistances.push({ price: round2(bollMid), name: '布林中轨' })
  // 20日最高价
  const recentHighs = technicalData.slice(-20).filter(k => k.high > 0).map(k => k.high)
  if (recentHighs.length) {
    const high20 = Math.max(...recentHighs)
    if (high20 > price) resistances.push({ price: round2(high20), name: '20日高点' })
  }
  resistances.sort((a, b) => a.price - b.price)

  // 成本价价位（用于持仓决策）
  const costPrice = position?.cost || 0
  const profitPct = costPrice > 0 ? (price - costPrice) / costPrice * 100 : 0

  // ─── 5. 场景策略文案 ───
  const scenarios = {}

  // 看涨场景
  if (probBull > 0) {
    const targetPrice = resistances.length > 0 ? resistances[0].price : round2(price * 1.08)
    const targetPrice2 = resistances.length > 1 ? resistances[1].price : round2(price * 1.15)
    const stopLoss = supports.length > 0 ? supports[0].price : round2(price * 0.95)
    const bullTriggers = []
    if (ma5 && ma10 && ma5 > ma10) bullTriggers.push('均线多头维持')
    if (macd > 0) bullTriggers.push('MACD红柱持续')
    if (volRatio > 1) bullTriggers.push('成交量配合')
    const bullStrategy = buildBullishStrategy(probBull, price, targetPrice, stopLoss, profitPct, position)
    scenarios.bullish = {
      probability: probBull,
      targetPrice: round2(targetPrice),
      targetPrice2: round2(targetPrice2),
      stopLoss: round2(stopLoss),
      strategy: bullStrategy,
      triggers: bullTriggers.length > 0 ? bullTriggers : ['技术面整体偏多'],
    }
  }

  // 看跌场景
  if (probBear > 0) {
    const support1 = supports.length > 0 ? supports[0].price : round2(price * 0.95)
    const support2 = supports.length > 1 ? supports[1].price : round2(price * 0.92)
    const bearTriggers = []
    if (ma5 && ma10 && ma5 < ma10) bearTriggers.push('均线交叉走弱')
    if (macd < 0) bearTriggers.push('MACD绿柱')
    if (volRatio < 1) bearTriggers.push('量能不足')
    const bearStrategy = buildBearishStrategy(probBear, price, support1, profitPct, position)
    scenarios.bearish = {
      probability: probBear,
      support1: round2(support1),
      support2: round2(support2),
      strategy: bearStrategy,
      triggers: bearTriggers.length > 0 ? bearTriggers : ['技术面偏弱'],
    }
  }

  // 震荡场景
  if (probSide > 0) {
    const rangeLow = supports.length > 0 ? supports[0].price : round2(price * 0.96)
    const rangeHigh = resistances.length > 0 ? resistances[0].price : round2(price * 1.04)
    const sideStrategy = buildSidewaysStrategy(price, rangeLow, rangeHigh, profitPct, position)
    scenarios.sideways = {
      probability: probSide,
      range: [round2(rangeLow), round2(rangeHigh)],
      strategy: sideStrategy,
      triggers: ['多空力量均衡，方向待选择'],
    }
  }

  // ─── 6. 偏离度监控阈值 ───
  const deviationThresholds = []
  // 向上突破
  if (resistances.length > 0) {
    deviationThresholds.push({
      level: 'alert', direction: 'up',
      breakPrice: resistances[0].price,
      changePct: round2((resistances[0].price - price) / price * 100),
      action: '突破阻力，趋势确认可加仓',
    })
    if (resistances.length > 1) {
      deviationThresholds.push({
        level: 'confirm', direction: 'up',
        breakPrice: resistances[1].price,
        changePct: round2((resistances[1].price - price) / price * 100),
        action: '强势突破，上涨空间打开',
      })
    }
  }
  // 向下突破
  if (supports.length > 0) {
    deviationThresholds.push({
      level: 'alert', direction: 'down',
      breakPrice: supports[0].price,
      changePct: round2((supports[0].price - price) / price * 100),
      action: '跌破支撑，减仓避险',
    })
    if (supports.length > 1) {
      deviationThresholds.push({
        level: 'critical', direction: 'down',
        breakPrice: supports[1].price,
        changePct: round2((supports[1].price - price) / price * 100),
        action: '关键支撑失守，清仓离场',
      })
    }
  }
  // 如果盈亏条件和硬止损重合
  if (costPrice > 0) {
    const stopLossPrice = round2(costPrice * (1 - 8 / 100))  // 按 -8% 止损线
    if (stopLossPrice < price) {
      deviationThresholds.push({
        level: 'critical', direction: 'down',
        breakPrice: stopLossPrice,
        changePct: round2((stopLossPrice - price) / price * 100),
        action: `触及止损线（-8%），强制清仓`,
      })
    }
  }

  // 按价位去重（MA20 与布林中轨可能同值，避免重复阈值占位）
  const seen = new Set()
  const uniqThresholds = deviationThresholds.filter(t => {
    const key = t.direction + t.breakPrice
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  // 排序（按偏离幅度从近到远）
  uniqThresholds.sort((a, b) => Math.abs(a.changePct) - Math.abs(b.changePct))

  // ─── 7. 综合摘要 ───
  const summary = buildSummary(price, probBull, probBear, probSide, net, ma5, ma10, ma20, rsi, dif, dea, technicalData)

  // ─── 8. 置信度 ───
  let confidence = 'medium'
  if (technicalData.length >= 150 && volRatio > 0.5) {
    confidence = probBull >= 65 || probBear >= 65 ? 'high' : 'medium'
  }
  if (technicalData.length < 60) confidence = 'low'

  return {
    date: latest.date,
    price: round2(price),
    prevClose: round2(price),
    scenarios,
    deviation: {
      thresholds: uniqThresholds,
    },
    supportResistance: {
      supports: supports.slice(0, 4),
      resistances: resistances.slice(0, 4),
    },
    summary,
    confidence,
    indicators: {
      ma5: round2(ma5),
      ma10: round2(ma10),
      ma20: round2(ma20),
      ma60: round2(ma60),
      dif: round2(dif),
      dea: round2(dea),
      macd: round2(macd),
      rsi: round2(rsi),
      k: round2(k),
      d: round2(d),
      volume: volume,
      volRatio: round2(volRatio),
      chg5d: round2(chg5d),
    },
  }
}

// ── 辅助：构建各场景策略文案 ──

function buildBullishStrategy(prob, price, target, stopLoss, profitPct, position) {
  const parts = []
  if (prob >= 70) {
    parts.push('强烈看涨，持有为主')
  } else if (prob >= 55) {
    parts.push('偏多看待，持有为主')
  } else {
    parts.push('谨慎看涨')
  }

  if (position) {
    if (profitPct > 0) {
      parts.push(`当前浮盈+${profitPct.toFixed(1)}%，可继续持有`)
      if (profitPct > 15 && prob < 70) {
        parts.push('利润较厚，注意保护')
      }
    } else if (profitPct < 0) {
      parts.push(`虽浮亏${Math.abs(profitPct).toFixed(1)}%，但趋势偏多，可耐心持有`)
    }
  }

  const upside = target > 0 ? round2((target - price) / price * 100) : 0
  parts.push(`上方目标${target}（+${upside}%）`)

  if (stopLoss > 0) {
    const downside = round2((stopLoss - price) / price * 100)
    parts.push(`跌破${stopLoss}（${downside}%）止损`)
  }

  return parts.join('；')
}

function buildBearishStrategy(prob, price, support, profitPct, position) {
  const parts = []
  if (prob >= 70) {
    parts.push('强烈看跌，建议减仓规避')
  } else if (prob >= 55) {
    parts.push('偏空看待，控制仓位')
  } else {
    parts.push('谨慎看跌')
  }

  if (position) {
    if (profitPct > 0) {
      parts.push(`当前浮盈+${profitPct.toFixed(1)}%，可先锁利`)
    } else if (profitPct < 0) {
      parts.push(`浮亏${Math.abs(profitPct).toFixed(1)}%，不宜加仓`)
      if (Math.abs(profitPct) > 5) {
        parts.push('亏损扩大中，考虑止损')
      }
    }
  }

  if (support > 0) {
    const downside = round2((support - price) / price * 100)
    parts.push(`关键支撑${support}（${downside}%），跌破则加剧下跌`)
  }

  return parts.join('；')
}

function buildSidewaysStrategy(price, rangeLow, rangeHigh, profitPct, position) {
  const parts = ['震荡整理，等待方向选择']
  if (rangeLow > 0 && rangeHigh > 0) {
    const rangePct = round2((rangeHigh - rangeLow) / rangeLow * 100)
    parts.push(`震荡区间${rangeLow}~${rangeHigh}（幅度${rangePct}%）`)
    if (position) {
      const mid = (rangeLow + rangeHigh) / 2
      if (price > mid) {
        parts.push('接近区间上沿，可适当减仓')
      } else {
        parts.push('接近区间下沿，可逢低补仓')
      }
    }
  }
  parts.push('放量突破区间则顺势跟随')
  return parts.join('；')
}

function buildSummary(price, probBull, probBear, probSide, net, ma5, ma10, ma20, rsi, dif, dea, techData) {
  const parts = []

  // 趋势判断
  if (net > 30) parts.push('短期趋势偏多')
  else if (net > 10) parts.push('短期趋势略偏多')
  else if (net < -30) parts.push('短期趋势偏空')
  else if (net < -10) parts.push('短期趋势略偏空')
  else parts.push('短期趋势震荡')

  // 均线状态
  if (ma5 && ma10 && ma20) {
    if (ma5 > ma10 && ma10 > ma20) parts.push('均线多头排列')
    else if (ma5 < ma10 && ma10 < ma20) parts.push('均线空头排列')
    else if (ma5 > ma20) parts.push('价格在MA20上方')
    else parts.push('价格在MA20下方')
  }

  // RSI
  if (rsi !== null && rsi !== undefined) {
    if (rsi > 70) parts.push('RSI偏热')
    else if (rsi > 60) parts.push('RSI强势')
    else if (rsi > 40) parts.push('RSI中性')
    else if (rsi > 30) parts.push('RSI偏弱')
    else parts.push('RSI超卖')
  }

  // MACD
  if (dif !== null && dif !== undefined && dea !== null && dea !== undefined) {
    if (dif > dea && dif > 0) parts.push('MACD多头')
    else if (dif < dea && dif < 0) parts.push('MACD空头')
    else if (dif > dea) parts.push('MACD短期偏多')
    else parts.push('MACD短期偏空')
  }

  // 历史支撑参考
  if (techData.length >= 60) {
    const closes60 = techData.slice(-60).map(k => k.close)
    const min60 = Math.min(...closes60)
    const max60 = Math.max(...closes60)
    const rangePct = round2((max60 - min60) / min60 * 100)
    if (rangePct < 15) parts.push(`近60日窄幅震荡（${rangePct}%）`)
    else parts.push(`近60日波动幅度${rangePct}%`)
  }

  // 概率结论
  parts.push(`今日预测：看涨${probBull}% / 看跌${probBear}% / 震荡${probSide}%`)

  return parts.join('，')
}