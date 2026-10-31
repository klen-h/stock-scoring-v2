"""
================================================================================
【文件作用】ETF 信号跟踪（移植自 flash-monitor 的 tracker.js + pro-trader.js + data-layer.js）
================================================================================

包含：
  - ETF 持仓池（22 只，宏观周期映射）+ 行情获取（复用 tencent.py）
  - 风控门槛：持仓数 / 相关性组 / 总风险 / 技术分（RSI+均线）
  - 信号状态机：waiting → active → closed（入场/止损/止盈/触阻力，含上升沿检测）
  - 绩效统计（胜率/盈亏比/回撤/夏普）+ 专业交易员报告（Markdown）

【修复原项目缺陷】信号不再来自正则解析 LLM 的 Markdown，而是消费
llm.extract_structured_signals() 输出的结构化信号（build_signal_from_llm）。

数据文件 tracking.json 与原项目 schema 兼容——旧项目的 public/data/tracking.json
可直接拷到 backend/data/ 续用。
================================================================================
"""

import copy
import json
import os
import time
from datetime import datetime

from app.flash import rules, store
from app.database import db
from app.tencent import _fetch_tencent

# ================================================================
#  一、ETF 持仓池与风控配置
# ================================================================

# 28 只 ETF（名称 → 腾讯代码）。名称即信号匹配键。
# 定位：宏观传导映射表（事件簇 → 宏观因子 → ETF），不是全市场筛选器——
# 刻意保持小池子：LLM 受限选择更可靠、prompt 更省、相关性分组可手工维护。
HOLDINGS_MAP = {
    # 宽基/权重
    "沪深300ETF": "sh510300", "中证1000ETF": "sh560010", "科创板50ETF": "sh588000",
    "创业板ETF": "sz159915", "中证500ETF": "sh510500",
    # 海外/跨境
    "纳斯达克ETF": "sh513100", "恒生科技ETF": "sh513130", "日经225ETF": "sh513880",
    "中概互联网ETF": "sh513050",
    # 行业/产业
    # 注：军工ETF 驱动来自国防政策/军贸订单，对海外冲突事件联动弱（勿当地缘对冲推荐）
    "半导体ETF": "sh512480", "人工智能ETF": "sh515070", "军工ETF": "sh512660",
    "新能源车ETF": "sh515030", "电力ETF": "sz159611", "医药ETF": "sh512010",
    "消费ETF": "sz159928", "证券ETF": "sh512880", "银行ETF": "sh512800",
    "煤炭ETF": "sh515220", "有色ETF": "sh512400",
    "地产ETF": "sh512200", "稀土ETF": "sh516150",
    "农业ETF": "sz159825", "养殖ETF": "sz159865",   # 农业粮食（2026-08 补：粮价共振+农业法催化，事件簇已建）
    # 商品/避险（白银用 LOF：国内暂无场内白银现货 ETF，LOF 流动性可用）
    "黄金ETF": "sh518880", "白银LOF": "sz161226", "标普油气ETF": "sz159518", "豆粕ETF": "sz159985",
    # 债券/货币
    "国债ETF": "sh511010", "短融ETF": "sh511360",
}

# 分类分组（用于 prompt 展示和相关性判断）
ETF_CATEGORIES = {
    "宽基/权重": ["沪深300ETF", "中证500ETF", "中证1000ETF", "创业板ETF", "科创板50ETF"],
    "海外/跨境": ["纳斯达克ETF", "中概互联网ETF", "恒生科技ETF", "日经225ETF"],
    "行业/产业": ["半导体ETF", "人工智能ETF", "军工ETF", "新能源车ETF", "电力ETF",
              "医药ETF", "消费ETF", "证券ETF", "银行ETF", "煤炭ETF", "有色ETF",
              "地产ETF", "稀土ETF", "农业ETF", "养殖ETF"],
    "商品/避险": ["黄金ETF", "白银LOF", "标普油气ETF", "豆粕ETF"],
    "债券/货币": ["国债ETF", "短融ETF"],
}

# 相关性组（同组最多 2 个持仓）
CORRELATION_GROUPS = [
    ["纳斯达克ETF", "中概互联网ETF", "恒生科技ETF"],   # 海外中概科技
    ["半导体ETF", "人工智能ETF"],                      # 科技硬件
    ["新能源车ETF", "电力ETF"],
    ["消费ETF", "医药ETF"],
    ["银行ETF", "证券ETF", "地产ETF"],                 # 金融地产
    ["黄金ETF", "白银LOF"],                            # 贵金属
    ["标普油气ETF", "煤炭ETF"],                        # 能源
    ["有色ETF", "稀土ETF"],                            # 上游资源
    ["农业ETF", "养殖ETF", "豆粕ETF"],                 # 农业链条（饲料→养殖）
]

# 复盘 prompt 里重点报点位的 ETF（覆盖全部事件簇的主要映射）
CORE_ETFS = ["沪深300ETF", "中证500ETF", "创业板ETF", "中证1000ETF", "科创板50ETF",
             "纳斯达克ETF", "恒生科技ETF", "中概互联网ETF",
             "半导体ETF", "人工智能ETF", "军工ETF",
             "消费ETF", "医药ETF", "证券ETF", "银行ETF", "地产ETF",
             "黄金ETF", "白银LOF", "标普油气ETF", "农业ETF"]

RISK_CONFIG = {
    "max_risk_per_trade": 0.015,       # 单笔最大风险 1.5%
    "max_total_risk": 0.06,            # 总风险上限 6%
    "max_positions": 5,                # 最大持仓（含等待中）数
    "max_correlated_positions": 2,     # 同相关性组最大持仓数
    "signal_expire_days": 5,           # 安全网：waiting 超 N 天强制过期（正常情况由论点失效机制管控）
    # ★ 2026-11-01 新增：最小风险回报比（信号质量门槛）。
    #   依据（去重后 18 笔真实成交，2026-08~10）：
    #     RR < 0.5   → 2 笔  0 胜  累计 -5.26%
    #     RR 0.5~1.0 → 4 笔  0 胜  累计 -11.20%
    #     RR 1.0~1.5 → 4 笔  1 胜  累计 -4.86%
    #     RR >= 1.5  → 8 笔  2 胜  累计 -6.46%
    #   ⇒ **RR < 1 的 6 笔一笔没赢**，属纯负期望区，直接拒绝。
    #   注：止损固定为 3%（`build_signal_from_llm`），所以 RR=1 等价于"支撑阻力
    #   间距 ≥ 3%"。间距过窄的信号（如黄金 9.50→9.55，仅 0.53%）必被拦。
    "min_rr": 1.0,
    # ★ 2026-11-01 新增：LLM `position_sizing` → 单笔风险比例映射。
    #   此前该字段被 `build_signal_from_llm` 整段丢弃，`calculate_position_size`
    #   恒按 1.5% 算 ⇒ LLM 说"轻仓试探"，代码照标准仓位下单，建议与执行脱节。
    #   ⚠️ "重仓"默认**封顶在 max_risk_per_trade（1.5%）**，不突破单笔上限：
    #     当前系统期望为负（去重后 18 笔累计 -27.78%），放大单笔风险 = 放大亏损。
    #     真要放开重仓，改成 0.025，并同步评估 `max_total_risk`。
    "position_risk": {
        "轻仓试探": 0.0075,
        "标准仓位": 0.0150,
        "重仓": 0.0150,
    },
}
ACCOUNT_SIZE = 100000                  # 默认账户规模（元）

# ★ 2026-09-13 P1-5 午盘信号治理（带开关，可回滚）：
#   LLM 信号实测盘前 75%（8 笔）vs 午盘 **0%**（10 笔）——09-09 高开低走式轮动市里
#   午盘追高即坟场（上午情绪高点已过，午后回落概率高）。
#   规则：午盘（source=lunchbreak）信号**仅 offensive 市放行**，其它市况拒绝
#   （进 rejectedSignals 攒样本、保留审计可见性，不是丢弃）。
#   开关 MIDDAY_REGIME_GATE=0 回滚。
MIDDAY_REGIME_GATE_ENABLED = (os.environ.get("MIDDAY_REGIME_GATE", "1") or "1").strip() \
    not in ("0", "false", "no", "off")
MIDDAY_ALLOWED_STATES = ("offensive",)


def _midday_regime_gate(signal: dict) -> dict:
    """午盘信号 regime 闸门（P1-5）：仅 offensive 市放行，其它市况拒绝。

    fail-open：读不到 regime 缓存/状态时不拦截——绝不因规则引擎故障挡掉信号记录。
    """
    if not MIDDAY_REGIME_GATE_ENABLED:
        return {"pass": True}
    if (signal.get("source") or "") != "lunchbreak":
        return {"pass": True}
    try:
        from app.backtest.market_regime import (get_regime_cache,
                                                restore_regime_cache_from_db)
        cache = get_regime_cache() or {}
        if not cache.get("state"):
            restore_regime_cache_from_db()
            cache = get_regime_cache() or {}
        state = cache.get("state")
    except Exception:
        return {"pass": True}
    if not state or state in MIDDAY_ALLOWED_STATES:
        return {"pass": True}
    return {"pass": False,
            "reason": f"午盘信号在 {state} 市不放行（仅进攻市；"
                      f"午盘追高实测 0% 胜率）"}


def holdings_text() -> str:
    """ETF 池分类文本（喂给 LLM 的持仓映射列表）。"""
    lines = []
    for cat, names in ETF_CATEGORIES.items():
        lines.append(f"{cat}: [{', '.join(names)}]")
    return "\n".join(lines)


# ================================================================
#  二、ETF 行情（复用 tencent.py）
# ================================================================

def get_etf_quotes() -> list:
    """
    抓取全部 22 只 ETF 实时行情（腾讯）。
    返回 [{name, code, price, prevClose, change, changeStr}]；失败返回 []。
    注意休市时也能拿到静态数据（与原项目 isForce=True 行为一致），是否使用由调用方决定。
    """
    codes = ",".join(HOLDINGS_MAP.values())
    code_to_name = {v: k for k, v in HOLDINGS_MAP.items()}
    try:
        data = _fetch_tencent(codes)
    except Exception as e:
        print(f"[signals] ETF 行情获取失败: {e}")
        from app import health
        health.record("tencent_etf", False, str(e))
        return []
    out = []
    for qt_code, info in data.items():
        if info.get("price", 0) <= 0:
            continue
        chg = info.get("change_pct", 0) or 0
        out.append({
            # 腾讯返回的是全称（如"沪深300ETF华泰柏瑞"），用池子里的短名
            "name": code_to_name.get(qt_code, info.get("name", "")),
            "code": info.get("code", ""),
            "price": round(info["price"], 3),
            "prevClose": round(info.get("prev_close", 0), 3),
            "change": round(chg, 2),
            "changeStr": f"{chg:.2f}",
        })
    from app import health
    health.record("tencent_etf", len(out) >= 20, "" if len(out) >= 20 else f"仅解析 {len(out)} 只")
    return out


def get_market_data(force: bool = False) -> dict:
    """
    统一数据层：市场状态 + ETF 行情 + 布伦特（取自新浪宏观面板缓存）。
    A 股休市且非 force → holdings 为 []（与原项目一致：避免无意义请求+标注数据静态性）。
    """
    status = rules.get_china_market_status()
    holdings = []
    if force or status["is_open"]:
        holdings = get_etf_quotes()
    oil = {"price": "未知", "change": 0, "changeStr": "0"}
    try:
        from app.macro import get_macro_panel
        brent = get_macro_panel().get("brent") or {}
        if brent.get("price"):
            oil = {"price": brent["price"], "change": brent.get("change_pct", 0),
                   "changeStr": f"{brent.get('change_pct', 0):.2f}"}
    except Exception:
        pass
    return {"marketStatus": status, "isAOpen": status["is_open"], "oil": oil,
            "holdings": holdings, "timestamp": rules.beijing_now_iso()}


# ================================================================
#  三、技术分析与风控门槛（移植 pro-trader.js）
# ================================================================

def calculate_rsi(prices: list, period: int = 14):
    """简单 RSI（不足周期返回 None）。"""
    if len(prices) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        chg = prices[i] - prices[i - 1]
        if chg > 0:
            gains += chg
        else:
            losses -= chg
    avg_gain, avg_loss = gains / period, losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def check_trend(prices: list, short: int = 5, long: int = 20) -> str:
    """均线趋势状态：strong_up/up/sideways/down/strong_down/unknown。"""
    if len(prices) < long:
        return "unknown"
    ma_s = sum(prices[-short:]) / short
    ma_l = sum(prices[-long:]) / long
    p = prices[-1]
    if p > ma_s > ma_l:
        return "strong_up"
    if p < ma_s < ma_l:
        return "strong_down"
    if p > ma_l:
        return "up"
    if p < ma_l:
        return "down"
    return "sideways"


def filter_signal_by_technical(signal: dict, price_history: list) -> dict:
    """技术分：RSI 极端 / 逆势扣分，顺势加分；≥50 通过。"""
    warnings = []
    score = 100
    if len(price_history) >= 20:
        rsi = calculate_rsi(price_history)
        trend = check_trend(price_history)
        if rsi is not None:
            if signal["direction"] == "long":
                if rsi > 70:
                    warnings.append("RSI超买 (>70)，追高需谨慎")
                    score -= 25
                elif rsi < 30:
                    score += 15
            else:
                if rsi < 30:
                    warnings.append("RSI超卖 (<30)，追空需谨慎")
                    score -= 25
                elif rsi > 70:
                    score += 15
        if signal["direction"] == "long":
            if trend == "strong_down":
                warnings.append("趋势向下，逆势做多")
                score -= 30
            elif trend == "strong_up":
                score += 20
        else:
            if trend == "strong_up":
                warnings.append("趋势向上，逆势做空")
                score -= 30
            elif trend == "strong_down":
                score += 20
    else:
        warnings.append("价格历史数据不足，无法进行技术分析")
        score -= 10
    return {"passed": score >= 50, "score": score, "warnings": warnings,
            "grade": "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"}


def resolve_risk_pct(signal: dict) -> float:
    """解析本笔交易的**单笔风险比例**（占账户）。

    ★ 2026-11-01 新增：把 LLM 的 `position_sizing` 真正接进来（原来恒 1.5%）。
    三道封顶，宁可低估风险也不高估：
      1. 未识别的取值 → 标准仓位（不猜"重仓"）
      2. 不超过 `max_risk_per_trade`（风控硬上限，配置里的"重仓"也封顶）
      3. `confidence == "低"` 时强制降到轻仓——prompt 本就要求"低置信度必须轻仓试探"，
         但 LLM 不总遵守，这里做代码兜底。
    """
    default = RISK_CONFIG["max_risk_per_trade"]
    sizing = (signal.get("positionSizing") or "").strip()
    pct = RISK_CONFIG["position_risk"].get(sizing, default)
    # confidence 低 → 强制轻仓（取更保守的一档）
    conf = (signal.get("confidence") or "").strip()
    if conf == "低":
        pct = min(pct, RISK_CONFIG["position_risk"]["轻仓试探"])
    # 硬上限
    return min(pct, default)


def calculate_position_size(signal: dict, account_size: int = ACCOUNT_SIZE) -> dict:
    """固定比例仓位：风险金额 / 单份风险 = 份数。"""
    try:
        entry = float(signal["entryCondition"]["targetPrice"])
        stop = float(signal["stopLoss"])
    except (KeyError, TypeError, ValueError):
        return {"shares": 0, "positionValue": 0, "riskAmount": 0, "riskPercent": 0}
    if not entry or not stop:
        return {"shares": 0, "positionValue": 0, "riskAmount": 0, "riskPercent": 0}
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return {"shares": 0, "positionValue": 0, "riskAmount": 0, "riskPercent": 0}
    risk_pct = resolve_risk_pct(signal)
    risk_amount = account_size * risk_pct
    shares = int(risk_amount / risk_per_share)
    return {"shares": shares, "positionValue": round(shares * entry, 2),
            "riskAmount": round(risk_amount, 2),
            "riskPercent": round(risk_pct * 100, 2),
            # ★ 留存判定依据，便于审计"为什么这单只有 0.75%"
            "riskBasis": {"positionSizing": signal.get("positionSizing") or "",
                          "confidence": signal.get("confidence") or ""}}


def _check_position_count(active: list) -> dict:
    n = len([s for s in active if s.get("status") in ("active", "waiting")])
    return {"canAdd": n < RISK_CONFIG["max_positions"], "currentCount": n,
            "maxAllowed": RISK_CONFIG["max_positions"]}


def _check_correlation(new_signal: dict, active: list) -> dict:
    correlated = 0
    for group in CORRELATION_GROUPS:
        if new_signal["etfName"] in group:
            correlated = sum(1 for s in active if s["etfName"] in group)
            break
    return {"canAdd": correlated < RISK_CONFIG["max_correlated_positions"],
            "currentCorrelated": correlated, "maxAllowed": RISK_CONFIG["max_correlated_positions"]}


def _check_total_risk(active: list, new_signal: dict, account_size: int = ACCOUNT_SIZE,
                      new_position: dict = None) -> dict:
    """总风险门槛：**已持仓**的风险金额之和 + 新信号风险金额 ≤ 账户 × max_total_risk。

    ★★ 2026-11-01 修正（原式量纲错误，门槛从未生效）：
      原式 `abs(entry-stop)/entry * account_size * 0.015`
        = 止损幅度(3%) × 账户(10万) × 1.5% = **45 元**
      而上限是 `account_size * 0.06` = **6000 元**
      ⇒ 需要 133 个持仓才触发，可 `max_positions` 只有 5
      ⇒ 实测 `rejectedSignals` 50 条里 **「总风险超限」0 次**，门槛形同虚设。

      现式按每笔**实际风险金额**（`positionSize.riskAmount` = 账户 × 1.5%）累加：
        4 个持仓 = 6% ⇒ 第 5 个被拒，门槛真正生效。

    ⚠️ 生效后与 `max_positions=5` 存在配置冲突（5 × 1.5% = 7.5% > 6%）：
       实际有效上限变成 **4 个持仓**。若想保留 5 个，把 `max_total_risk` 调到 7.5%。
    """
    def _risk_of(s: dict) -> float:
        ps = s.get("positionSize") or {}
        try:
            amt = float(ps.get("riskAmount") or 0)
            if amt > 0:
                return amt
        except (TypeError, ValueError):
            pass
        # 老记录没有 positionSize：按固定单笔风险估算（与新信号同口径）
        return account_size * RISK_CONFIG["max_risk_per_trade"]

    used = sum(_risk_of(s) for s in active if s.get("status") == "active")
    # ★ 2026-11-01：新信号按**实际解析出的风险比例**计（轻仓 0.75% / 标准 1.5%），
    #   不再固定 1.5%——否则"轻仓试探"的信号在额度上被当成标准仓位，
    #   总风险门槛会比真实情况更早触发（也可能更晚，取决于档位）。
    if new_position and new_position.get("riskAmount"):
        try:
            new_risk = float(new_position["riskAmount"])
        except (TypeError, ValueError):
            new_risk = account_size * resolve_risk_pct(new_signal)
    else:
        new_risk = account_size * resolve_risk_pct(new_signal)
    total = used + new_risk
    return {"canAdd": total <= account_size * RISK_CONFIG["max_total_risk"],
            "totalRiskPercent": round(total / account_size * 100, 2),
            "maxAllowed": RISK_CONFIG["max_total_risk"] * 100}


# ================================================================
#  四、tracking.json 读写 + 状态机
# ================================================================

# ★ 读缓存（2026-09-12 egress 治理③）：tracking JSON 97KB（压缩后过网 16.6KB），
#   被通知轮询/信号页/对账反复整份读。写入侧 save_tracking 立即失效 → 同进程永远读到最新；
#   TTL 仅作跨进程兜底（当前唯一写入方是 Render 自身）。
_TRACK_CACHE = {"ts": 0.0, "data": None}
# ★ 2026-10-19（egress 实测）：整份状态 ~16KB/次，被通知轮询/信号页/对账反复读
#   （32h 实测 256 次 ≈4MB）。写入侧 `save_tracking` 立即失效 ⇒ 同进程永远读到最新，
#   跨进程兜底 TTL 放宽到 5 分钟（这一份是跟踪快照，不要求秒级同步）。
_TRACK_TTL = 300

_DEFAULT_TRACKING = {
    "activeSignals": [], "history": [], "priceHistory": {},
    "performance": {"total": 0, "wins": 0, "losses": 0, "winRate": 0},
}


def load_tracking() -> dict:
    """从数据库加载跟踪状态。

    ★ 2026-09-12（egress 治理③）：这份 JSON 有 97KB（含 priceHistory 45KB /
      rejectedSignals 29KB / history 20KB），压缩后过网仍约 16.6KB，却被通知轮询、
      信号页、对账等路径反复整份读（实测 2 万次 ≈ 15MB/天）。
      这里加进程内缓存：**写入侧 save_tracking 立即失效**，所以同进程读到的永远是最新
      （跟踪状态的写入方只有 Render 自身：flash service 的 poll/add_signal/update_signals）；
      120 秒 TTL 只是跨进程兜底（当前无其它写入方）。返回深拷贝，避免调用方原地改写污染缓存。
    """
    if _TRACK_CACHE["data"] is not None and time.time() - _TRACK_CACHE["ts"] <= _TRACK_TTL:
        return copy.deepcopy(_TRACK_CACHE["data"])
    row = db.fetch_one("SELECT data_json FROM tracking_state WHERE id = 1")
    data = None
    if row:
        try:
            data = json.loads(row["data_json"])
        except (json.JSONDecodeError, KeyError):
            data = None
    if data is None:
        data = dict(_DEFAULT_TRACKING)
        _TRACK_CACHE["ts"] = time.time()
        _TRACK_CACHE["data"] = data
        return dict(data)
    _TRACK_CACHE["ts"] = time.time()
    _TRACK_CACHE["data"] = data
    return copy.deepcopy(data)


def save_tracking(tracking: dict) -> None:
    """保存跟踪状态到数据库（写入即失效读缓存，见 load_tracking）"""
    try:
        _TRACK_CACHE["ts"] = 0.0
        _TRACK_CACHE["data"] = None
        db.upsert("tracking_state", {
            "id": 1,
            "data_json": json.dumps(tracking, ensure_ascii=False),
            "updated_at": rules.beijing_now_iso()
        }, conflict_columns=["id"])
    except Exception as e:
        print(f"[signals] 保存跟踪状态失败: {e}")


def build_signal_from_llm(llm_signal: dict, source: str) -> dict:
    """
    LLM 结构化信号 → 完整信号对象（入场/止损/止盈由支撑/阻力推导，口径同原项目：
    做多在支撑附近入场、止损 3%、止盈阻力；做空镜像）。
    """
    support, resistance = float(llm_signal["support"]), float(llm_signal["resistance"])
    is_long = llm_signal["direction"] == "long"
    entry = support if is_long else resistance
    stop = round(support * 0.97, 3) if is_long else round(resistance * 1.03, 3)
    take = resistance if is_long else support
    return {
        "etfName": llm_signal["etfName"],
        "direction": llm_signal["direction"],
        "trend": "up" if is_long else "down",
        "support": str(support),
        "resistance": str(resistance),
        "entryCondition": {"type": "price", "targetPrice": str(round(entry, 3))},
        "stopLoss": str(stop),
        "takeProfit": str(round(take, 3)),
        "reasoning": llm_signal.get("reasoning", ""),
        "source": source,
        # ★ 2026-11-01（规则同步第 1 步：先留存，不改行为）：
        #   LLM 明明给了这三个字段，`build_signal_from_llm` 却**整段丢弃**——
        #   这正是"买入规则与 LLM 不同步"的核心：
        #     · invalidation   "跌破6.95失效"  vs  代码一律 support*0.97
        #     · position_sizing "轻仓试探/重仓" vs  代码恒按 1.5% 风险
        #     · confidence     "低/中/高"       vs  代码不读
        #   留存后：审计可见、前端可展示、后续接入不必再回头改结构。
        "invalidation": llm_signal.get("invalidation", ""),
        "positionSizing": llm_signal.get("position_sizing", ""),
        "confidence": llm_signal.get("confidence", ""),
        "timeframe": llm_signal.get("timeframe", ""),
    }


def add_signal_with_validation(signal: dict, account_size: int = ACCOUNT_SIZE) -> dict:
    """
    入场门槛校验并写入 tracking：
      持仓数 / 相关性组 / 总风险 / 技术分。通过 → activeSignals(waiting)，
      拒绝 → rejectedSignals（保留 50 条）。
    """
    tracking = load_tracking()
    validation = {"passed": True, "warnings": [], "reasons": []}

    # ★ 2026-09-13 P1-5：午盘信号 regime 闸门（仅 offensive 放行，其它市况进 rejected）
    mid = _midday_regime_gate(signal)
    if not mid["pass"]:
        validation["passed"] = False
        validation["reasons"].append(mid["reason"])

    pos = _check_position_count(tracking["activeSignals"])
    if not pos["canAdd"]:
        validation["passed"] = False
        validation["reasons"].append(f"持仓数超限 ({pos['currentCount']}/{pos['maxAllowed']})")
    corr = _check_correlation(signal, tracking["activeSignals"])
    if not corr["canAdd"]:
        validation["passed"] = False
        validation["reasons"].append(f"相关性超限 ({corr['currentCorrelated']}/{corr['maxAllowed']})")
    # ★ 2026-11-01：先算仓位（依赖 position_sizing / confidence），再校验总风险。
    #   否则总风险按"假设固定 1.5%"估算，与真实下单风险不一致。
    position = calculate_position_size(signal, account_size)

    risk = _check_total_risk(tracking["activeSignals"], signal, account_size, position)
    if not risk["canAdd"]:
        validation["passed"] = False
        validation["reasons"].append(f"总风险超限 ({risk['totalRiskPercent']}%/{risk['maxAllowed']}%)")

    # ★★ 2026-11-01 新增：风险回报比门槛（**信号质量**门槛，区别于上面三条的仓位门槛）
    #   止损固定 3%（build_signal_from_llm），止盈 = 阻力 ⇒ RR = 间距 / 3%。
    #   实测 RR<1 的 6 笔 0 胜、累计 -16.46%，是纯负期望区。
    #   ★ 这是唯一直接作用于胜率的门槛——上面三条只管"买多少"，不管"买得对不对"，
    #     所以 `rejectedSignals` 里 32 次午盘 / 11 次持仓数 / 7 次相关性，
    #     而信号质量层面一次都没有拦过。
    rr = 0.0
    try:
        # ★ 用 takeProfit 而非 resistance：做空时 entry == resistance，
        #   拿 resistance 当止盈端会恒得 0（2026-11-01 测试发现的 bug）。
        _entry = float(signal["entryCondition"]["targetPrice"])
        _take = float(signal["takeProfit"])
        _stop = float(signal["stopLoss"])
        _reward = abs(_take - _entry)
        _risk = abs(_entry - _stop)
        rr = round(_reward / _risk, 2) if _risk > 0 else 0.0
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        rr = 0.0
    if rr < RISK_CONFIG["min_rr"]:
        validation["passed"] = False
        validation["reasons"].append(
            f"风险回报比不足 ({rr:.2f} < {RISK_CONFIG['min_rr']})："
            f"支撑阻力间距过窄，止盈空间小于 3% 止损幅度")

    tech = {"passed": True, "score": 100, "warnings": [], "grade": "A"}
    hist = tracking.get("priceHistory", {}).get(signal["etfName"])
    if hist:
        tech = filter_signal_by_technical(signal, [p["price"] for p in hist])
        validation["warnings"] = tech["warnings"]
        # ★★ 2026-11-01 修正（技术分从未参与拦截）：
        #   原代码只取 `tech["warnings"]`，`tech["passed"]` **从未被读取**
        #   ⇒ RSI 超买(-25) / 逆势(-30) 只写进 warnings，照常放行
        #   ⇒ 实测 rejectedSignals 50 条里「技术分不达标」**0 次**。
        #   这里让它真正生效：score < 50 ⇒ 拒绝。
        #   （无 priceHistory 的老 ETF 仍走默认 100 分 fail-open，不做历史假设。）
        if not tech["passed"]:
            validation["passed"] = False
            why = "；".join(tech["warnings"]) or "综合评分不足"
            validation["reasons"].append(f"技术分不达标 ({tech['score']}/50)：{why}")

    signal = dict(signal)
    signal.update({
        "id": str(int(time.time() * 1000)),
        # ★ 2026-09-12：统一北京时间（原来写服务器本地时间 = UTC，与 flash 侧不一致）
        "createdAt": rules.beijing_now_iso(),
        "status": "waiting" if validation["passed"] else "rejected",
        "entries": [], "exits": [],
        "validation": validation, "techScore": tech["score"], "techGrade": tech["grade"],
        "positionSize": position,
    })
    if validation["passed"]:
        tracking["activeSignals"].append(signal)
    else:
        rejected = tracking.setdefault("rejectedSignals", [])
        rejected.insert(0, signal)
        tracking["rejectedSignals"] = rejected[:50]
    save_tracking(tracking)
    return {"signal": signal, "validation": validation, "techCheck": tech, "positionSize": position}


def record_price_history(market_data: dict) -> dict:
    """
    每日记录一次各 ETF 价格（技术分析的数据来源，每 ETF 保留 60 天）。
    返回 {etf_name: prev_close}——记录前的上一次收盘价（用于跨日论点失效检测）。
    """
    tracking = load_tracking()
    today = store._bj_date()
    ph = tracking.setdefault("priceHistory", {})
    prev_close_map = {}
    for h in market_data.get("holdings", []):
        hist = ph.setdefault(h["name"], [])
        # 记录前，最后一条就是昨日收盘（尚未写入今日数据）
        if hist and hist[-1].get("date") != today:
            prev_close_map[h["name"]] = hist[-1]["price"]
        if not hist or hist[-1].get("date") != today:
            hist.append({"date": today, "price": float(h["price"]),
                         "timestamp": rules.beijing_now_iso()})
            ph[h["name"]] = hist[-60:]
    save_tracking(tracking)
    return prev_close_map


def update_signals(market_data: dict) -> dict:
    """
    信号状态机（每 15 分钟跑一次）：
      waiting → active：价格触及入场目标（上升沿：上一刻未触发、本刻触发）
      active → closed：止损 / 止盈 / 触阻力（优先级：止损 > 止盈 > 阻力）
      接近提醒：距目标 0.1%~1.0%

    论点失效检测（替代固定阈值跳空守卫）：
      用信号自身的止损位作为论点有效边界——若昨日收盘已跌破止损位，
      说明交易论点在隔夜已被市场否定，信号自动过期。
      优势：自适应各 ETF 波动率、自适应每笔交易的具体支撑/阻力结构。
    返回 {tracking, alerts}。
    """
    prev_close_map = record_price_history(market_data)
    tracking = load_tracking()
    holdings_map = {h["name"]: h for h in market_data.get("holdings", [])}
    now = rules.beijing_now_iso()      # ★ 统一北京时间（入场/出场事件时间，通知管道直接比较它）
    alerts = {"entries": [], "exits": [], "updates": []}

    # ★★ 2026-11-01 并发去重：本函数（15 分钟）、`add_signal_with_validation`
    #   （快讯轮询触发）与 `save_tracking` 都是「读-改-写」且**无锁** ⇒ 同一信号
    #   可能被处理两次。实测 `tracking_state.data_json`：
    #     history 52 条 / 唯一 34 条 ⇒ **重复率 34.6%**
    #   而 perf 在**每次**关闭时都 `+=1`（见下方 `perf["total"] += 1`）
    #   ⇒ 胜率/盈亏比/期望全部被重复样本污染。
    #   以 id 为键：处理前短路（不累加 perf），入历史前去重。
    _hist_ids = {h.get("id") for h in tracking["history"]}

    for signal in tracking["activeSignals"]:
        if signal.get("status") not in ("waiting", "active"):
            continue
        holding = holdings_map.get(signal["etfName"])
        if not holding:
            continue
        price = float(holding["price"])
        prev_price = float(signal.get("lastCheckedPrice") or price)
        signal["lastCheckedPrice"] = price

        # ── waiting 过期检查（安全网：正常情况下由论点失效机制管控，此处防止极端情况）──
        if signal["status"] == "waiting":
            try:
                # ★ 统一北京时间：老记录没有时区标记（当时写的是 UTC），用 to_beijing 归一化
                created = rules.to_beijing(signal.get("createdAt"))
                age_days = (rules.beijing_now() - created).days if created else 0
                if age_days >= RISK_CONFIG["signal_expire_days"]:
                    signal["status"] = "expired"
                    signal["expireReason"] = f"等待超过{age_days}天未触发，自动过期"
                    alerts["updates"].append({
                        "signal": signal, "currentPrice": price,
                        "message": f"⏰ 【信号过期】{signal['etfName']} 等待{age_days}天未触发，"
                                   f"原入场价 {signal['entryCondition']['targetPrice']}，"
                                   f"已过期，请在下次复盘中重新评估"})
                    continue
            except (ValueError, KeyError):
                pass

        # ── waiting → active（入场触发，上升沿检测 + 论点失效检测）──
        if signal["status"] == "waiting" and signal["entryCondition"].get("type") == "price":
            target = float(signal["entryCondition"]["targetPrice"])
            is_long = signal["direction"] == "long"
            triggered = (is_long and price <= target) or (not is_long and price >= target)
            prev_triggered = (is_long and prev_price <= target) or (not is_long and prev_price >= target)
            if triggered and not prev_triggered:
                # ── 论点失效检测：昨日收盘已跌破止损位 → 隔夜已否定交易论点 ──
                prev_close = prev_close_map.get(signal["etfName"])
                if prev_close is not None:
                    try:
                        sl = float(signal["stopLoss"])
                        thesis_broken = ((is_long and prev_close < sl)
                                         or (not is_long and prev_close > sl))
                    except (TypeError, ValueError):
                        thesis_broken = False
                    if thesis_broken:
                        signal["status"] = "expired"
                        signal["expireReason"] = (f"昨日收盘 {prev_close} 已跌破止损位 {sl}，"
                                                  f"交易论点隔夜失效")
                        alerts["updates"].append({
                            "signal": signal, "currentPrice": price,
                            "message": f"🚫 【论点失效】{signal['etfName']} 昨日收盘 {prev_close} "
                                       f"已跌破止损 {sl}，入场分析隔夜失效，自动过期"})
                        continue
                signal["status"] = "active"
                signal["entries"].append({"price": price, "time": now, "reason": "触发入场价格"})
                signal["entryPrice"] = price
                signal["entryTime"] = now
                alerts["entries"].append({
                    "signal": signal, "currentPrice": price,
                    "message": f"🚀 【买入信号】{signal['etfName']} 在 {price} 触发入场"})

        # ── active → closed（止损/止盈/触阻力）──
        if signal["status"] == "active":
            is_long = signal["direction"] == "long"
            exit_reason = None
            try:
                sl = float(signal["stopLoss"])
                if (is_long and price <= sl) or (not is_long and price >= sl):
                    exit_reason = "触发止损"
            except (TypeError, ValueError):
                pass
            if not exit_reason:
                try:
                    tp = float(signal["takeProfit"])
                    if (is_long and price >= tp) or (not is_long and price <= tp):
                        exit_reason = "触发止盈"
                except (TypeError, ValueError):
                    pass
            if not exit_reason:
                try:
                    res = float(signal["resistance"])
                    if (is_long and price >= res) or (not is_long and price <= res):
                        exit_reason = "触及阻力位"
                except (TypeError, ValueError):
                    pass
            if exit_reason:
                # ★ 2026-11-01：并发重复处理 → 已在历史里 ⇒ 只清理状态，不再累加 perf
                _sid = signal.get("id")
                if _sid and _sid in _hist_ids:
                    signal["status"] = "closed"
                    signal["lastStatus"] = "closed"
                    continue
                signal["status"] = "closed"
                signal["lastStatus"] = "closed"
                signal["exits"].append({"price": price, "time": now, "reason": exit_reason})
                signal["exitPrice"] = price
                signal["exitTime"] = now
                entry = float(signal.get("entryPrice") or price)
                profit = ((price - entry) / entry * 100 if is_long
                          else (entry - price) / entry * 100)
                signal["profit"] = round(profit, 2)
                signal["isWin"] = profit > 0
                perf = tracking["performance"]
                perf["total"] += 1
                perf["wins"] += 1 if signal["isWin"] else 0
                perf["losses"] += 0 if signal["isWin"] else 1
                perf["winRate"] = round(perf["wins"] / perf["total"] * 100, 1) if perf["total"] else 0
                tracking["history"].insert(0, signal)
                # ★★ 2026-11-01 关键修复：这里已经插过一次，函数末尾的清理循环
                #   （`for s in activeSignals: if status in (closed,expired): insert`）
                #   会**再插一次** ⇒ 每条平仓记录在 history 里都有 2 份。
                #   实测 history 52 条 / 唯一 34 条（重复率 34.6%）的根因就是这里，
                #   不是并发。记住 id 让末尾循环跳过。
                _hist_ids.add(signal.get("id"))
                emoji = "💰" if signal["isWin"] else "💸"
                alerts["exits"].append({
                    "signal": signal, "currentPrice": price, "profit": signal["profit"],
                    "isWin": signal["isWin"],
                    "message": f"{emoji} 【卖出信号】{signal['etfName']} 在 {price} {exit_reason} "
                               f"({'+' if profit > 0 else ''}{profit:.2f}%)"})

        # ── 接近提醒（距目标 0.1%~1.0%）──
        if signal["status"] in ("waiting", "active"):
            try:
                target = (float(signal["entryCondition"]["targetPrice"]) if signal["status"] == "waiting"
                          else (float(signal["takeProfit"] or signal["resistance"])
                                if signal["direction"] == "long"
                                else float(signal["stopLoss"] or signal["support"])))
                if target:
                    distance = abs((price - target) / target * 100)
                    if 0.1 < distance <= 1.0:
                        alerts["updates"].append({
                            "signal": signal, "currentPrice": price, "targetPrice": target,
                            "distance": round(distance, 2),
                            "message": f"⚡ 【接近提醒】{signal['etfName']} 当前 {price}，"
                                       f"距目标 {target} 还有 {distance:.2f}%"})
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    # 过期信号移入历史（供审计追踪），已平仓同理
    # ★ 2026-11-01：这里必须用 `_hist_ids` 跳过**上面已经插入过**的记录
    #   （平仓分支 line ≈704 已 insert 一次），否则每条平仓记录会写 2 份。
    #   实测：history 52 条 / 唯一 34 条（重复率 34.6%）。
    for s in tracking["activeSignals"]:
        if s.get("status") not in ("closed", "expired"):
            continue
        sid = s.get("id")
        if sid and sid in _hist_ids:
            continue
        _hist_ids.add(sid)
        tracking["history"].insert(0, s)
    tracking["activeSignals"] = [s for s in tracking["activeSignals"]
                                 if s.get("status") not in ("closed", "expired")]
    # 历史保留最近 200 条
    tracking["history"] = tracking["history"][:200]
    save_tracking(tracking)
    return {"tracking": tracking, "alerts": alerts}


def get_active_signals() -> list:
    return load_tracking()["activeSignals"]


def get_history(limit: int = 20) -> list:
    return load_tracking()["history"][:limit]


def get_performance() -> dict:
    return load_tracking()["performance"]


# ================================================================
#  五、绩效指标 + 专业报告（Markdown）
# ================================================================

def calculate_advanced_metrics(history: list):
    """胜率/平均盈亏/盈亏比/最大回撤/夏普/期望/连胜连败。"""
    if not history:
        return None
    # ★ 2026-11-01：按 id 去重后再统计。
    #   修复前每条平仓记录会在 history 里存两份（见 update_signals），
    #   导致胜率/盈亏比/期望/连败全部被重复样本污染（36 笔 vs 真实 18 笔）。
    #   存量脏数据无法自愈，这里在**消费侧**兜底，不改写历史。
    _seen = set()
    _uniq = []
    for _h in history:
        _hid = _h.get("id")
        if _hid and _hid in _seen:
            continue
        if _hid:
            _seen.add(_hid)
        _uniq.append(_h)
    history = _uniq
    profits = [h.get("profit") or 0 for h in history]
    wins = [h for h in history if h.get("isWin")]
    losses = [h for h in history if not h.get("isWin")]
    avg_win = sum(h["profit"] for h in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(h.get("profit") or 0 for h in losses) / len(losses)) if losses else 0
    profit_factor = (avg_win * len(wins)) / (avg_loss * len(losses)) if avg_loss > 0 and losses else 999
    max_dd = peak = cumulative = 0.0
    for p in profits:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    max_wins = max_losses = cur_w = cur_l = 0
    for h in history:
        if h.get("isWin"):
            cur_w, cur_l = cur_w + 1, 0
            max_wins = max(max_wins, cur_w)
        else:
            cur_l, cur_w = cur_l + 1, 0
            max_losses = max(max_losses, cur_l)
    mean = sum(profits) / len(profits)
    std = (sum((p - mean) ** 2 for p in profits) / len(profits)) ** 0.5
    sharpe = mean / std * (252 ** 0.5) if std > 0 else 0
    return {
        "totalTrades": len(history),
        "winRate": f"{len(wins) / len(history) * 100:.1f}",
        "avgWin": f"{avg_win:.2f}", "avgLoss": f"{avg_loss:.2f}",
        "profitFactor": f"{profit_factor:.2f}", "maxDrawdown": f"{max_dd:.2f}",
        "maxConsecutiveWins": max_wins, "maxConsecutiveLosses": max_losses,
        "sharpeRatio": f"{sharpe:.2f}",
        "expectancy": f"{(avg_win * len(wins) / len(history)) - (avg_loss * len(losses) / len(history)):.2f}",
    }


def generate_pro_trader_report(account_size: int = ACCOUNT_SIZE) -> str:
    """专业交易员报告（Markdown，供推送与前端展示）。"""
    metrics = calculate_advanced_metrics(
        [s for s in get_history(100) if s.get("status") == "closed"])
    active = [s for s in get_active_signals() if s.get("status") in ("active", "waiting")]
    report = "# 🎯 专业交易员报告\n"
    if metrics:
        report += f"""
## 📊 绩效仪表盘
- 总交易 {metrics['totalTrades']} 次 | 胜率 {metrics['winRate']}% | 盈亏比 {metrics['profitFactor']}
- 平均盈利 +{metrics['avgWin']}% / 平均亏损 -{metrics['avgLoss']}%
- 最大回撤 -{metrics['maxDrawdown']}% | 夏普 {metrics['sharpeRatio']} | 期望 {metrics['expectancy']}%
- 最长连胜 {metrics['maxConsecutiveWins']} / 连败 {metrics['maxConsecutiveLosses']}
"""
    report += f"""
## ⚙️ 风控状态
- 单笔最大风险 {RISK_CONFIG['max_risk_per_trade'] * 100:.1f}% | 总风险上限 {RISK_CONFIG['max_total_risk'] * 100:.1f}%
- 最大持仓 {RISK_CONFIG['max_positions']} | 同组最大 {RISK_CONFIG['max_correlated_positions']}

## 🎯 当前持仓/待入场（{len(active)}）
"""
    for s in active:
        icon = "🟢" if s["status"] == "active" else "⏳"
        d = "做多" if s["direction"] == "long" else "做空"
        report += (f"{icon} **{s['etfName']}**（{d}）入场 {s['entryCondition']['targetPrice']} "
                   f"止损 {s['stopLoss']} 止盈 {s['takeProfit']} "
                   f"技术分 {s.get('techScore')}/{s.get('techGrade')}\n")
    return report


# ================================================================
#  六、LLM 复盘对账（预测 vs 实际）
# ================================================================

def _safe_dt(s):
    """ISO → 北京时间（tz-aware）；无法解析返回 None（历史 naive 值按 UTC 解释）。"""
    return rules.to_beijing(s)


def build_audit() -> dict:
    """
    LLM 复盘对账：把「复盘提议 → 风控门槛 → 跟踪 → 平仓」整条漏斗和分组胜率算清楚。

    数据全部来自 tracking.json（每个信号的 source 字段记录它来自哪个复盘阶段，
    提议数 = 通过(active+history) + 被拒(rejected)，自洽；rejected 只保留最近 50 条）。

    用它回答：LLM 推荐到底靠不靠谱？哪个阶段/哪个 ETF 推得最差？
    注意：已平仓样本 < 20 时结论仅供方向参考，别当统计显著。
    """
    from datetime import datetime, timedelta

    tracking = load_tracking()
    active = tracking.get("activeSignals", [])
    history = tracking.get("history", [])
    rejected = tracking.get("rejectedSignals", [])
    closed = [s for s in history if s.get("status") == "closed"]

    wins = [s for s in closed if s.get("isWin")]
    profits = [float(s.get("profit") or 0) for s in closed]
    gross_win = sum(p for p in profits if p > 0)
    gross_loss = abs(sum(p for p in profits if p < 0))

    def _wr(n_w, n_c):
        return round(n_w / n_c * 100, 1) if n_c else None

    def _ap(pr):
        return round(sum(pr) / len(pr), 2) if pr else None

    # ── 按复盘阶段 ──
    phase_names = {"premarket": "盘前", "lunchbreak": "午盘", "postmarket": "盘后"}
    by_phase = {}
    for phase, cname in phase_names.items():
        passed = [s for s in active + history if (s.get("source") or "") == phase]
        rej = [s for s in rejected if (s.get("source") or "") == phase]
        cl = [s for s in closed if (s.get("source") or "") == phase]
        w = [s for s in cl if s.get("isWin")]
        pr = [float(s.get("profit") or 0) for s in cl]
        by_phase[phase] = {
            "name": cname, "proposed": len(passed) + len(rej),
            "passed": len(passed), "rejected": len(rej),
            "closed": len(cl), "wins": len(w),
            "win_rate": _wr(len(w), len(cl)),
            "avg_profit": _ap(pr),
        }

    # ── 按 ETF（回答"哪个 ETF 推得最差"）──
    etf_map = {}
    for s in active + history + rejected:
        name = s.get("etfName") or "?"
        etf_map.setdefault(name, {"etf": name, "proposed": 0, "passed": 0,
                                  "closed": 0, "wins": 0, "profit_sum": 0.0})
        etf_map[name]["proposed"] += 1
    for s in active + history:
        etf_map[s.get("etfName")]["passed"] += 1
    for s in closed:
        d = etf_map[s.get("etfName")]
        d["closed"] += 1
        if s.get("isWin"):
            d["wins"] += 1
        d["profit_sum"] += float(s.get("profit") or 0)
    by_etf = sorted(
        [{"etf": d["etf"], "proposed": d["proposed"], "passed": d["passed"],
          "closed": d["closed"], "wins": d["wins"],
          "win_rate": _wr(d["wins"], d["closed"]),
          "avg_profit": round(d["profit_sum"] / d["closed"], 2) if d["closed"] else None}
         for d in etf_map.values()],
        key=lambda r: (-r["closed"], -r["proposed"]))

    # ── 按方向（多 vs 空）──
    by_direction = {}
    for direction in ("long", "short"):
        cl = [s for s in closed if s.get("direction") == direction]
        w = [s for s in cl if s.get("isWin")]
        by_direction[direction] = {
            "closed": len(cl), "wins": len(w),
            "win_rate": _wr(len(w), len(cl)),
            "avg_profit": _ap([float(s.get("profit") or 0) for s in cl]),
        }

    # ── 拒绝原因分布（哪道门槛拦得最多）──
    gates = {}
    for s in rejected:
        for r in (s.get("validation") or {}).get("reasons", []):
            key = (r.split("(")[0]).strip() or r[:12]
            gates[key] = gates.get(key, 0) + 1

    # ── 僵尸等待：waiting 超过 5 天没触发的信号（理论上已被自动过期，此处做安全网）──
    cutoff = rules.beijing_now() - timedelta(days=5)
    stale_waiting = [s.get("etfName") for s in active
                     if s.get("status") == "waiting"
                     and (_safe_dt(s.get("createdAt")) or rules.beijing_now()) < cutoff]

    # ── 过期信号统计（最近 30 天）──
    expire_cutoff = rules.beijing_now() - timedelta(days=30)
    recent_expired = [s for s in history
                      if s.get("status") == "expired"
                      and (_safe_dt(s.get("createdAt")) or rules.beijing_now()) > expire_cutoff]

    closed_trades = [{
        "etfName": s.get("etfName"), "direction": s.get("direction"),
        "source": s.get("source"),
        "entryPrice": s.get("entryPrice"), "exitPrice": s.get("exitPrice"),
        "profit": s.get("profit"), "isWin": s.get("isWin"),
        "reason": (s.get("exits") or [{}])[-1].get("reason", ""),
        "exitTime": s.get("exitTime"),
    } for s in history if s.get("status") == "closed"][:30]

    return {
        "summary": {
            "proposed": len(active) + len(history) + len(rejected),
            "waiting": len([s for s in active if s.get("status") == "waiting"]),
            "holding": len([s for s in active if s.get("status") == "active"]),
            "closed": len(closed), "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": _wr(len(wins), len(closed)),
            "avg_profit": _ap(profits),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0
            else (999 if gross_win > 0 else None),
            "rejected": len(rejected),
            "stale_waiting": stale_waiting,
            "expired_count": len(recent_expired),
            "expired_details": [{"etf": s.get("etfName"), "reason": s.get("expireReason", ""),
                                 "created": s.get("createdAt", "")}
                                for s in recent_expired[:10]],
        },
        "by_phase": by_phase,
        "by_etf": by_etf,
        "by_direction": by_direction,
        "rejection_gates": gates,
        "closed_trades": closed_trades,
        "note": None if len(closed) >= 20 else
        f"已平仓仅 {len(closed)} 笔，样本太小，胜率仅供方向参考（建议积累 20 笔以上再下结论）",
        "generated_at": rules.beijing_now_iso(),
    }
