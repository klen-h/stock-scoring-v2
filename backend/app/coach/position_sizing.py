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


def position_sizing_for_portfolio() -> dict:
    """读 user_portfolio 全部持仓 → 仓位建议（供 /api/user/position-sizing）。"""
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
    return position_sizing(codes)
