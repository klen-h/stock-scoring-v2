"""
================================================================================
【文件作用】仓位建议引擎（W1.5，2026-09-13）—— 数据联动，替代前端写死的 calcPositionSize
================================================================================

背景：前端 `usePortfolio.calcPositionSize` 用「温度档位 × 评分 × 持仓数」写死规则，
  未联动项目真实数据。本引擎改为**三层、全数据驱动、可解释**：

  市场层（总仓位上限 0~80%，档位化）
    起点 = `trade_gate.REGIME_POSITION[regime]`（defensive 0 / nb 20 / neutral 50 / offensive 80）
    降档因子（命中即乘）：
      10Y > 5%              ×0.8（全球贴现率压制）
      两融 5 日净减         ×0.85（杠杆撤离）
      情绪温度计过热(≥80)   ×0.7（追高风险）
      跌停 ≥100 家          ×0.7（恐慌）
      涨跌比 <0.3           ×0.7（普跌）

  个股层（单股档位）
    直接复用 `mainforce.trade_gate.evaluate(code)`（已含 regime + 筹码拥挤 +
    主力根据 + 当日矛盾），零新增网络（读 mainforce_state 落库缓存）。

  组合层（归一化到总上限）
    suggested_pct = snap(total × 个股pct / Σ个股pct)，向下取整到档位。

数据源全部现有、零新增网络请求（宏观/两融/情绪有缓存，主力/筹码读落库表）。
================================================================================
"""

from typing import Dict, List, Optional

from app.mainforce.trade_gate import REGIME_POSITION, evaluate as gate_evaluate

POSITION_STEPS = (0, 5, 10, 20, 30, 40, 50, 60, 80)


def _snap(pct: float) -> int:
    """向下落到最近的档位（保守方向，避免 7% 这类伪精度）。"""
    out = 0
    for v in POSITION_STEPS:
        if pct >= v:
            out = v
    return out


# ── 数据源适配：复用 Coach 的（各带缓存 / fail-open）───────────────────────────

def _ctx() -> dict:
    from app.coach import rules as cr
    return {
        "regime": cr._regime(),
        "macro": cr._macro(),
        "margin": cr._margin(),
        "breadth": cr._breadth(),
    }


def _market_total(ctx: dict) -> tuple:
    """市场层：总仓位上限 + 依据。返回 (total_pct, reasons)。"""
    regime = (ctx.get("regime") or {}).get("state")
    total = float(REGIME_POSITION.get(regime, 30))
    reasons = [f"regime={regime or '未知'} → 基准 {int(total)}%"]

    macro = ctx.get("macro") or {}
    us10y = macro.get("us10y")
    if us10y is not None and us10y > 5.0:
        total *= 0.8
        reasons.append(f"美债10Y {us10y:.2f}% >5% → ×0.8（全球贴现率压制）")

    margin = ctx.get("margin") or {}
    chg5 = margin.get("chg5")
    if chg5 is not None and chg5 < 0:
        total *= 0.85
        reasons.append(f"两融5日净减 {abs(chg5):.0f}亿 → ×0.85（杠杆撤离）")
    score_t = margin.get("score")
    if score_t is not None and score_t >= 80:
        total *= 0.7
        reasons.append(f"情绪温度计 {score_t:.0f} 过热 → ×0.7（追高风险）")

    breadth = ctx.get("breadth") or {}
    ld = breadth.get("limit_down")
    if ld is not None and ld >= 100:
        total *= 0.7
        reasons.append(f"跌停 {ld} 家 → ×0.7（恐慌）")
    ratio = breadth.get("up_down_ratio")
    if ratio is not None and ratio < 0.3:
        total *= 0.7
        reasons.append(f"涨跌比 {ratio:.2f} → ×0.7（普跌）")

    return _snap(total), reasons


def position_sizing(codes: Optional[List[str]] = None,
                    ctx: Optional[dict] = None) -> dict:
    """
    计算仓位建议。

    codes：持仓代码列表（缺省只算市场层，不含个股）。
    返回 {
      total_limit_pct,     # 总仓位上限（市场层档位）
      market_reasons,      # 市场层依据（中文，可解释）
      positions: [{code, position_pct, position_label, suggested_pct, reasons}],
    }
    """
    if ctx is None:
        ctx = _ctx()
    total, market_reasons = _market_total(ctx)

    positions: List[Dict] = []
    if codes:
        mf_map = {}
        try:
            from app.mainforce.state import load_latest
            mf_map = load_latest(codes) or {}
        except Exception as e:
            print(f"[position_sizing] mainforce_state 加载失败（降级）: {e}")
        for code in codes:
            try:
                g = gate_evaluate(str(code), mf=mf_map.get(str(code)))
            except Exception as e:
                g = {"position_pct": 0, "position_label": "数据缺",
                     "reasons": [f"个股评估失败: {e}"]}
            positions.append({
                "code": str(code),
                "position_pct": int(g.get("position_pct") or 0),
                "position_label": g.get("position_label") or "",
                "reasons": g.get("reasons") or [],
            })

    # 组合层：每只建议 = min(个股档位, 总上限)。
    # ★ 不按持仓数归一化——个股 position_pct 是「这只票该不该重仓」的档位，
    #   不因持仓多少而稀释（否则总上限 10% 摊到 5 只 → 每只 2% → snap 到 0，
    #   全部归零、毫无信息量）。总上限是「天花板」、个股档位是「意愿」，
    #   最终 = min(意愿, 天花板)，交给用户判断"哪几只该砍"。
    for p in positions:
        p["suggested_pct"] = _snap(min(p["position_pct"], total))

    return {"total_limit_pct": total, "market_reasons": market_reasons,
            "positions": positions}


# ══════════════════════════════════════════════════════════════════════════
#  组合回撤纪律（周回撤熔断）—— 2026-09-25（用户需求 2）
# ══════════════════════════════════════════════════════════════════════════
# 【需求原话】交易方法论落地计划 A6："G 系列补**周回撤熔断**（周内净值回撤 ≥X% 降仓，
#   X 用现有 G2 的 5% 对齐）"。框架纪律："达到日/周最大回撤停止交易"。
#
# 【★★ 为什么落在**用户真实账户**而不是模拟盘】（先量后做的实测结论）：
#   · 模拟盘 `paper_positions` 当前 **0 持仓**、最后信号 **2026-09-11**，且战法白名单为空
#     ⇒ **它已经静止**（不开新仓）⇒ 给它加熔断**没有实际作用**；且它只有
#     `peak_equity`（无日度净值序列）⇒ 周维度还得先攒数据。
#   · 用户账户 `user_portfolio` 有 `shares`/`cost`/`created_at`，历史收盘价实测可取
#     （`tencent.get_kline` 与 `backtest_prices` 逐日一致）⇒ **立刻可算**。
#   ⇒ 保护用户真金白银才是要点。模拟盘 G5 留待它恢复开仓后再议。
#
# 【口径】组合净值(d) = Σ(shares_i × close_i(d))，即**持仓市值**（不含现金）。
#   · 建仓日之前不计该票（用 `created_at` 的日期部分，避免"凭空持仓"）。
#   · 某日缺价做**前向填充**（取该日之前最近收盘）；整只票无价则剔除并记入 `missing`。
#   · 回撤 = (窗口内峰值 − 最新) / 峰值 × 100。
#   ⚠️⚠️ **近似，必须明示**：按「**当前持仓**」回算历史 ⇒ 若窗口内有过加/减仓，
#      曲线与真实净值不同（加仓会推高市值、看起来像"上涨"）⇒ 输出 `note`，前端要显示。
#
# 【阈值】对齐已有的 **G2**（`paper_trading.DRAWDOWN_FREEZE_PCT = 5.0`）——
#   同一条纪律不该有两套数值；这里引用而非另写常量（防漂移）。
# 【动作】框架说"**降仓**"（比 G2 的"冻结"轻）⇒ 触发时把组合总仓位上限打对折，
#   并同步下调每只的 `suggested_pct`。★ 为什么不直接冻结：周回撤是**短周期**波动，
#   直接冻结会把正常周内回撤放大成"停摆"；降仓既守纪律又保留机会。
#   （该判断可被未来数据推翻 ⇒ 系数写成常量、可配。）
PORTFOLIO_DD_WINDOW = 5            # 回看交易日数（≈一周）
DD_TOTAL_SCALE = 0.5               # 触发后总仓位上限系数（降仓）
_DD_CACHE = {"ts": 0.0, "val": None}
_DD_TTL = 180                      # 3 分钟：历史价有 KLINE_CACHE，此层避免轮询重复回算


def _dd_threshold() -> float:
    """回撤阈值（%）—— **引用 G2 的常量**，单一事实源（防两套数值漂移）。"""
    try:
        from app.strategies.paper_trading import DRAWDOWN_FREEZE_PCT
        return float(DRAWDOWN_FREEZE_PCT)
    except Exception:
        return 5.0


def _recent_closes(code: str, count: int) -> Dict[str, float]:
    """{date: close}（最近 count 个交易日）。失败返回 {}（fail-open）。"""
    try:
        from app.tencent import get_kline
        ks = get_kline(str(code), period="day", count=count)
        return {str(k.get("date")): float(k.get("close") or 0)
                for k in (ks or []) if k.get("date") and (k.get("close") or 0) > 0}
    except Exception as e:
        print(f"[position_sizing] kline failed {code}: {e}")        # ASCII（铁律⑥）
        return {}


def _px_at(px: Dict[str, float], day: str) -> Optional[float]:
    """该票在 day 的收盘价（**前向填充**：取 day 及之前最近一天）；无则 None。"""
    best = None
    for d in sorted(px):
        if d <= day:
            best = px[d]
        else:
            break
    return best


def _portfolio_drawdown_uncached(window: int) -> Dict:
    out = {"available": False, "window": window, "triggered": False, "drawdown_pct": None,
           "threshold_pct": _dd_threshold(), "curve": [], "positions": [],
           "missing": [], "nav_latest": None, "nav_peak": None, "peak_date": None,
           "advice": None, "note": None}
    try:
        from app.database import db
        from app.portfolio_scope import portfolio_where
        w, p = portfolio_where()
        rows = db.fetch("SELECT code, name, shares, cost, created_at FROM user_portfolio "
                        f"{w}", p) or []
    except Exception as e:
        print(f"[position_sizing] portfolio read failed: {e}")       # ASCII（铁律⑥）
        out["note"] = "读取持仓失败"
        return out

    holds = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        try:
            sh = float(r.get("shares") or 0)
        except (TypeError, ValueError):
            sh = 0.0
        if not code or sh <= 0:
            continue
        holds.append({"code": code, "name": r.get("name") or code, "shares": sh,
                      "cost": float(r.get("cost") or 0),
                      "since": str(r.get("created_at") or "")[:10]})
    if not holds:
        out["note"] = "暂无持仓（无数据可算）"
        return out

    for h in holds:
        h["px"] = _recent_closes(h["code"], window + 3)
        if not h["px"]:
            out["missing"].append(h["code"])
    live = [h for h in holds if h["px"]]
    if not live:
        out["note"] = "持仓均无历史价格，无法回算"
        return out

    # 交易日轴 = **各票日期的并集**，取最近 window 个。
    #   ⚠️ 用并集而非"全市场交易日历"：好处是零额外查询、且不会引入持仓之外的日期；
    #      代价是**某票长期停牌时曲线会少几个点**（该票停牌期间无价、也无前向填充点）。
    #      对回撤判定影响有限（峰值/最新都取已有采样点），但极端情况可能低估
    #      ⇒ 已在 note 里声明"按当前持仓回算"的近似性质。
    dates = sorted({d for h in live for d in h["px"]})[-window:]
    curve = []
    for d in dates:
        nav, used = 0.0, 0
        for h in live:
            if h["since"] and d < h["since"]:
                continue                      # 该日尚未建仓
            px = _px_at(h["px"], d)
            if px is None:
                continue
            nav += h["shares"] * px
            used += 1
        if nav > 0:
            curve.append({"date": d, "nav": round(nav, 2), "n": used})
    if len(curve) < 2:
        # ⚠️ 数据不足是**常见且正常的**（如"今天刚建仓"）：必须说清"为什么空、什么时候有"，
        #   否则用户看到空白只会怀疑功能坏了（本项目已多次踩"空白无解释"的坑）。
        since_min = min((h["since"] for h in live if h["since"]), default=None)
        out["note"] = (f"可回算的交易日不足（{len(curve)} 天，至少需 2 天）"
                       + (f"；当前持仓最早建仓日 {since_min}" if since_min else "")
                       + "——需持仓跨越 2 个交易日以上，下一个交易日盘后即可见")
        return out

    latest = curve[-1]
    peak = max(curve, key=lambda x: x["nav"])
    dd = (peak["nav"] - latest["nav"]) / peak["nav"] * 100
    out.update({
        "available": True, "curve": curve,
        "nav_latest": latest["nav"], "nav_peak": peak["nav"], "peak_date": peak["date"],
        "drawdown_pct": round(dd, 2),
        "triggered": dd >= out["threshold_pct"],
        "positions": [
            {"code": h["code"], "name": h["name"], "shares": h["shares"], "cost": h["cost"],
             "value": (round(h["shares"] * _px_at(h["px"], latest["date"]), 2)
                       if _px_at(h["px"], latest["date"]) else None),
             "pnl_pct": (round((_px_at(h["px"], latest["date"]) / h["cost"] - 1) * 100, 2)
                         if h["cost"] > 0 and _px_at(h["px"], latest["date"]) else None)}
            for h in live],
    })
    span = f"{curve[0]['date'][5:]}~{latest['date'][5:]}"
    if out["triggered"]:
        out["advice"] = (f"组合市值自 {peak['date'][5:]} 峰值回撤 {dd:.1f}%"
                         f"（阈值 {out['threshold_pct']:g}%）⇒ 按纪律降仓："
                         f"总仓位上限 ×{DD_TOTAL_SCALE:g}，暂不加新仓")
    else:
        out["advice"] = (f"组合市值自 {peak['date'][5:]} 峰值回撤 {dd:.1f}%，"
                         f"未达 {out['threshold_pct']:g}% 阈值")
    # ⚠️ note 是**给前端插值显示的纯文本** ⇒ 不能含 Markdown 标记
    parts = [f"口径：持仓市值（不含现金）· 窗口 {span}（{len(curve)} 个交易日）· 按当前持仓回算"]
    parts.append("⚠️ 未考虑窗口内加减仓，期间有交易则曲线会失真")
    if out["missing"]:
        parts.append("⚠️ 无历史价已剔除：" + "、".join(out["missing"]))
    out["note"] = "；".join(parts)
    return out


def portfolio_drawdown(window: int = PORTFOLIO_DD_WINDOW) -> Dict:
    """用户组合「近 window 个交易日」净值回撤（周回撤熔断数据源）。带 3 分钟进程缓存。"""
    import time
    now = time.time()
    c = _DD_CACHE.get("val")
    if c is not None and now - _DD_CACHE["ts"] < _DD_TTL:
        return c
    val = _portfolio_drawdown_uncached(window)
    if val.get("available"):            # 失败不写缓存 ⇒ 下次重试
        _DD_CACHE.update(ts=now, val=val)
    return val


def position_sizing_for_portfolio() -> dict:
    """读 user_portfolio 全部持仓 → 仓位建议（供 /api/user/position-sizing）。

    ★ 2026-09-25（用户需求 2）：叠加**组合周回撤降仓** —— 触发时下调总上限与每只建议，
      理由写进 `market_reasons`（与既有降档因子同一套"可解释"机制）。
    """
    codes = []
    try:
        from app.database import db
        # ★ 2026-09-17：多用户表，按主用户过滤（见 app/portfolio_scope.py）
        from app.portfolio_scope import portfolio_where
        _w, _p = portfolio_where()
        rows = db.fetch(
            f"SELECT code FROM user_portfolio {_w} ORDER BY created_at ASC", _p)
        seen = set()
        for r in rows or []:
            c = str(r.get("code") or "").strip()
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
    except Exception:
        pass
    res = position_sizing(codes)
    # ★ 2026-09-25（用户需求 2）：组合周回撤熔断 —— 触发则**降仓**（框架："周内净值回撤 ≥X% 降仓"）。
    #   fail-open：拿不到数据就不加这条因子（绝不因辅助信息拖垮仓位建议）。
    try:
        dd = portfolio_drawdown()
        res["portfolio_drawdown"] = dd
        if dd.get("triggered"):
            old = res.get("total_limit_pct") or 0
            new_total = _snap(old * DD_TOTAL_SCALE)
            res["total_limit_pct"] = new_total
            res.setdefault("market_reasons", []).append(
                f"组合近 {dd['window']} 个交易日回撤 {dd['drawdown_pct']}% "
                f"≥ {dd['threshold_pct']:g}% → 总上限 ×{DD_TOTAL_SCALE:g}"
                f"（{old}% → {new_total}%）")
            # ★ 上限降了，每只建议必须同步下调 —— 否则出现"总上限 15% 但单只建议 30%"的自相矛盾
            for p in res.get("positions") or []:
                p["suggested_pct"] = _snap(min(p.get("position_pct") or 0, new_total))
    except Exception as e:
        print(f"[position_sizing] drawdown overlay failed: {e}")     # ASCII（铁律⑥）
    return res
