"""
================================================================================
【文件作用】Coach 监控循环（W1）：硬警报 + 关键时点体检卡 + 预承诺剧本
================================================================================

流程（简报 §3.2）：
  持仓轮询 → 规则引擎求值（纯代码，app/coach/rules.py）
     ├─ 硬警报触发（止损/浮亏/持仓到期…）→ 直接推企微，**LLM 不参与**
     ├─ 未触发 → 静默（"不动"是合法且默认的输出）
     └─ 关键时点（10:30 / 14:45）→ 生成"教练体检卡"

★ 评审 ②（不放大 egress）：本循环**只读全市场行情内存缓存**（`routers/market._cache`，
  由 scheduler.stock_cache_refresh_loop 每 120s 刷新），**不逐股拉新浪/腾讯**；
  需要读 K 线的重规则（ret20 / price_pos）只在体检卡与收盘回写两条低频路径跑。

★ 评审 ③（挂点）：本循环属"盘中保留循环"，与 `paper_track_loop` 同级挂在
  `asyncio.create_task`（**不受 RENDER_READ_ONLY 关闭**）——教练推的是纪律，
  生产只读模式下恰恰最需要它。日批只挂"收盘回写 + 结果回填"两处。

★ 评审 ①（事前无权）：本模块推送的每条文案的数字都由 `rules.py` 注入，
  W2 的 LLM 层只能"渲染"，不能"决定"。
================================================================================
"""

import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional

from app.flash import rules as flash_rules
from app.coach import audit
from app.coach import rules as coach_rules

# ── 开关与参数 ────────────────────────────────────────────────────────────────
COACH_ENABLED = (os.environ.get("COACH_ENABLED", "on").strip() != "off")
POLL_SECONDS = 30                      # 轮询间隔（只读内存缓存，零额外网络）
CARD_TIMES = ((10, 30), (14, 45))      # 关键时点体检卡（简报 §3.3）
CARD_WINDOW_MIN = 5                    # 窗口宽度（错过则不补，避免午休后连推）
TRAIL_TRIGGER_PCT = 15.0               # 浮盈移动止损缺省值（实际读 rules.yaml plan.trail_trigger_pct）
MAX_HOLD_REVIEW_DAYS = 3               # 强制评估日数缺省值（实际读 rules.yaml hold_3d_review.hold_days）


def _push(title: str, body: str) -> None:
    if not body.strip():
        return
    try:
        from app.flash.wechat import push_markdown_batched
        # force=True：教练警报是"触发式硬通知"，与模拟盘风控同级，不受业务开关限制
        push_markdown_batched(title, body, force=True)
    except Exception as e:
        print(f"[coach] 推送失败: {e}")


# ==============================================================================
#  单轮：light 规则（30s 高频路径）
# ==============================================================================

def coach_tick() -> dict:
    """求值 light 规则 → 落库去重 → 仅对**新触发且 push=True** 的推企微。

    30s 轮询天然会重复命中同一条件（止损位一破就一直破），靠
    `audit.record_advices` 的 dedupe_key（日|规则|标的）保证**一天只推一次**。
    """
    if not COACH_ENABLED:
        return {"enabled": False}
    advices = [a.to_dict() for a in coach_rules.evaluate_all("light")]
    if not advices:
        return {"total": 0, "pushed": 0}
    pushable = [a for a in advices if a.get("push")]
    silent = [a for a in advices if not a.get("push")]
    fresh = audit.record_advices(pushable, push=True) if pushable else []
    if silent:
        audit.record_advices(silent, push=False)     # 只落库攒样本（第一周不推）
    if fresh:
        _push("🎓 教练警报", coach_rules.format_batch(fresh))
    return {"total": len(advices), "pushed": len(fresh), "silent": len(silent)}


# ==============================================================================
#  关键时点体检卡（10:30 / 14:45）
# ==============================================================================

def health_card() -> str:
    """体检卡正文：每票一行（成本/现价/盈亏/距止损/持有天数/规则状态），3 秒看完。"""
    ctx = coach_rules.build_context(tier="all")
    poss = ctx.get("positions") or []
    if not poss:
        return ""
    advices = coach_rules.evaluate_all("all", ctx=ctx)
    hit_by_code = {}
    for a in advices:
        if a.code:
            hit_by_code.setdefault(a.code, []).append(a.label)
    lines = [f"**持仓 {len(poss)} 只 ｜ regime="
             f"{(ctx.get('regime') or {}).get('state') or '未知'}**", "",
             "| 标的 | 成本 | 现价 | 盈亏 | 距止损 | 持有 | 规则 |",
             "|---|---|---|---|---|---|---|"]
    for p in poss:
        tag = "[真]" if p.get("source") == "real" else "[模]"
        d = p.get("dist_stop_pct")
        lines.append(
            f"| {tag}{p['name']} | {p['fill_price']:.2f} | {p['price']:.2f} | "
            f"{p['pnl_pct']:+.1f}% | {('—' if d is None else f'{d:+.1f}%')} | "
            f"{p['hold_days']}d | {'、'.join(hit_by_code.get(p['code']) or ['—'])} |")
    # 市场级规则（闸门）单列
    market = [a for a in advices if not a.code]
    if market:
        lines += ["", "**环境**："] + [f"- {a.message.splitlines()[0]}" for a in market]
    return "\n".join(lines)


def push_health_card() -> bool:
    body = health_card()
    if not body:
        return False
    _push("🎓 教练体检卡", body)
    return True


# ==============================================================================
#  预承诺退出计划（开仓瞬间写死"什么时候走"—— 简报 §3.3 第 1 条）
# ==============================================================================

def _nth_trading_day(start: datetime, n: int) -> str:
    d, cnt = start, 0
    while cnt < n:
        d += timedelta(days=1)
        try:
            if flash_rules.is_trading_day(d):
                cnt += 1
        except Exception:
            cnt += 1          # 日历不可用则退化为自然日
    return d.strftime("%Y-%m-%d")


def _yaml_param(rule_id: str, key: str, default):
    """读 rules.yaml 单个规则参数（审查 P2-12：阈值一律 yaml，代码不留死值）。"""
    try:
        for r in (coach_rules.load_config() or {}).get("rules") or []:
            if r.get("id") == rule_id:
                return (r.get("params") or {}).get(key, default)
    except Exception:
        pass
    return default


def _yaml_plan(key: str, default):
    """读 rules.yaml 顶层 plan 段（剧本阈值）。"""
    try:
        return ((coach_rules.load_config() or {}).get("plan") or {}).get(key, default)
    except Exception:
        return default


def write_plan(position_id: int, code: str, name: str, entry_price: float,
               stop_loss: float, fill_date: Optional[str] = None) -> dict:
    """写入入场剧本（幂等：同 code+日期只写一条）。

    剧本内容全部来自**规则**（不临场判断）：
      1. 止损价（v2 = 介入价×(1-WARFARE_STOP_PCT_V2)，与退出 v2 同口径）
      2. 第 N 个交易日强制评估（N = rules.yaml hold_3d_review.hold_days）
      3. 浮盈 ≥ trail_trigger_pct 后止损上移至成本（提醒口径）
      4. price_pos>plan.price_pos_cut 且主力出货 → 不等止损直接离场
    ★ 审查 P2-12：此前剧本三处与规则/执行打架——评估日数代码写死 3（yaml 改了
      不跟随）、price_pos 文本写死 0.80（yaml 是 0.75）、移动止损是无人执行的
      空头支票（现标注提醒口径）。
    """
    audit.ensure_tables()
    today = fill_date or audit._today()
    try:
        base = datetime.strptime(today, "%Y-%m-%d")
    except (ValueError, TypeError):
        base = datetime.now()
    review_days = int(_yaml_param("hold_3d_review", "hold_days", MAX_HOLD_REVIEW_DAYS))
    trail = float(_yaml_plan("trail_trigger_pct", TRAIL_TRIGGER_PCT))
    pp_cut = float(_yaml_plan("price_pos_cut", 0.75))
    conditions = {
        "止损": (f"跌破 {stop_loss:.2f} 离场" if stop_loss and stop_loss > 0
                 else "信号未提供止损位（★ 无止损纪律保护，请手动设定）"),
        "强制评估": f"第 {review_days} 个交易日（{_nth_trading_day(base, review_days)}）"
                    f"无条件复核：到期/破位/移动止损任一未触发即离场",
        "移动止损": f"浮盈达 {trail:.0f}% 后止损上移至成本 {entry_price:.2f}"
                    f"（教练提醒口径，不自动改单）",
        "提前退出": f"price_pos>{pp_cut:.2f} 且命中主力出货 → 不等止损直接离场",
    }
    try:
        exist = audit.db.fetch_one(
            "SELECT id FROM coach_plans WHERE code=%s AND plan_date=%s", (code, today))
        if exist:
            return {"ok": True, "existing": True}
        import json
        audit.db.execute(
            "INSERT INTO coach_plans (code, name, position_id, plan_date, entry_price, "
            "stop_loss, review_date, trail_trigger_pct, exit_conditions, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (code, name, position_id, today, entry_price, stop_loss,
             _nth_trading_day(base, review_days), trail,
             json.dumps(conditions, ensure_ascii=False), audit._now()))
        print(f"[coach] 已写入入场剧本 {code} {name}（止损 {stop_loss:.2f}）")
        return {"ok": True, "existing": False}
    except Exception as e:
        print(f"[coach] 剧本写入失败 {code}: {e}")
        return {"ok": False, "error": str(e)}


# ==============================================================================
#  盘后：收盘回写 + 结果回填（日批挂点，评审 ③）
# ==============================================================================

def daily_close_job() -> dict:
    """盘后一次性：全量（含 heavy）求值落库 + T+5 结果回填。

    与 30s 轮询的区别：这里跑 `tier="all"`，能把需要读 K 线的规则
    （近 20 日涨幅、筹码位置/出货）也算完并落库 —— 每天一次，量可控。
    """
    if not COACH_ENABLED:
        return {"enabled": False}
    advices = [a.to_dict() for a in coach_rules.evaluate_all("all")]
    fresh = audit.record_advices(advices, push=False)   # 盘后只落库不推（避免夜间轰炸）
    filled = audit.backfill_outcome()
    return {"total": len(advices), "new": len(fresh), "outcome_filled": filled}


# ==============================================================================
#  常驻循环（挂 asyncio.create_task，只读模式保留）
# ==============================================================================

def _card_due(now: datetime) -> Optional[str]:
    """当前是否命中体检卡时点且当日未推（返回幂等 key，否则 None）。"""
    for h, m in CARD_TIMES:
        if now.hour == h and m <= now.minute < m + CARD_WINDOW_MIN:
            return f"coach_card_{h:02d}{m:02d}"
    return None


async def coach_loop() -> None:
    """盘中 30s 轮询（light 规则）+ 关键时点体检卡 + 盘后收盘回写。"""
    print(f"[coach] 教练循环启动（enabled={COACH_ENABLED}, 轮询 {POLL_SECONDS}s）")
    while True:
        try:
            if COACH_ENABLED:
                now = flash_rules.beijing_now()
                t = now.hour * 60 + now.minute
                is_open = False
                try:
                    is_open = bool(flash_rules.get_china_market_status().get("is_open"))
                except Exception:
                    pass
                if now.weekday() < 5 and is_open:
                    await asyncio.to_thread(coach_tick)
                    key = _card_due(now)
                    if key:
                        from app.flash import store
                        if not store.is_schedule_done(key):
                            await asyncio.to_thread(push_health_card)
                            store.mark_schedule_done(key)
                elif flash_rules.is_trading_day(now) and 940 <= t < 1440:
                    # ★ W1 补漏（2026-09-13）：盘后一次性收盘回写 = 全量（含 heavy）
                    #   求值落库 + T+5 结果回填。此前 `daily_close_job` 无任何调用点 →
                    #   heavy 规则（拥挤减仓/出货砍）永不落库、outcome_pct 永远空。
                    #   挂 coach_loop 内（评审③：不被 READ_ONLY 关），与
                    #   paper_track_loop 的盘后兜底同模式（schedule_done 幂等）。
                    #   ★ 审查 P2-17：weekday 判定改 is_trading_day（节假日不再落库）。
                    from app.flash import store
                    if not store.is_schedule_done("coach_daily_close"):
                        r = await asyncio.to_thread(daily_close_job)
                        store.mark_schedule_done("coach_daily_close")
                        print(f"[coach] 盘后收盘回写完成: {r}")
        except Exception as e:
            # 循环保命：单轮异常绝不能让教练循环静默死亡（同 review_loop 教训）
            print(f"[coach] 单轮异常（循环继续）: {e}")
        await asyncio.sleep(POLL_SECONDS)
