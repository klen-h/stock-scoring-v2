# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】推演登记 + 每日确定性对照 + LLM 修正（2026-11-22，用户："开始做 P0"）
================================================================================

【解决什么】
  用户每周末会自己做一轮"情景推演"（3 个情景 + 概率 + 验证清单），
  但推演只活在聊天记录里：**次日要人工再问一遍 LLM 才能修正**。
  本模块把它变成可登记的资产：
    登记（接口/前端） → 每日收盘自动对照实际数据 → LLM 修正 → 推企微 + 落库

【与 `scenario_watch` 的边界（别搞混）】
  · `scenario_watch`（2026-10-23）：**硬编码**的"磨底 2.0"记分卡，
    情景固定 A/B/C/D、验证项固定 8 条。历史数据有价值，**保持不动**。
  · 本模块：**任意推演可登记**，情景/验证项都由用户定义，且带 LLM 修正。
  两者共用底层数据源（market_amount_daily / market_emotion_daily /
  key_level_watch / breadth_divergence），口径不会漂。

【两条硬纪律】
  1. **取不到数据就标 `nodata`，绝不填 0、绝不猜** ——
     0 会被读成"实现了"，比缺数据危险得多。
  2. **LLM 必须回答"什么情况说明我这次修正也错了"** ——
     用户原始推演最大的毛病就是"周一低开几成定局"没有反面。

【红线】纯复盘，不进决策链：不改评分、不进闸门、不碰模拟盘。
================================================================================
"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.database import db

_T_SCEN = "thesis_scenarios"
_T_DAILY = "thesis_daily"
_BJ = timezone(timedelta(hours=8))
_READY = False

# LLM 开关（关了也能跑确定性对照，只是没有"修正建议"）
LLM_ENABLED = (os.environ.get("THESIS_LLM") or "1").strip() != "0"
PUSH_ENABLED = (os.environ.get("THESIS_PUSH") or "1").strip() != "0"
# 只对"仍在有效期内"的推演做每日复盘
MAX_ACTIVE = int(os.environ.get("THESIS_MAX_ACTIVE") or 3)

# 支持的验证项 —— key 是稳定契约，前端/接口都用它
METRIC_LABELS = {
    "sh_close": "上证收盘",
    "sh_chg_pct": "上证涨跌%",
    "amount_yi": "两市成交额(亿)",
    "limit_down": "跌停家数",
    "limit_up": "涨停家数",
    "down_ratio": "下跌占比",
    "breadth_gap": "广度背离(pp)",
    # ★ 2026-11-23（用户周二清单⑥"工行建行若再跌 1%+ 而指数上涨 = 高切低动真格"）：
    #   红利端是跷跷板另一头，必须能量化。用工行 601398 代表（日线在 backtest_prices）。
    #   注意 pass 语义：`icbc_chg_pct <= -1` 成立 = 红利被抛售资金换成长，
    #   对"V 型修复"情景是**支持**证据，不是失败。
    "icbc_chg_pct": "工行涨跌%(红利端)",
    # ★★ 2026-11-25（用户："标准③「盘中低点 ≥3520」用 EOD 快照的日低字段就能测，
    #   数据现成，只差落库"）：
    #   此前只有 close，导致"低开 0.5-1.5% 可承受""盘中低点 ≥3520 为 B 底线"
    #   这两条**判不了** —— 而它们是区分 B（回踩确认）与 C（二次探底）的核心。
    #   `key_level_watch` 已补 open/high/low 落库 ⇒ 现在可测。
    #   ⚠️ 历史行这三列为 NULL ⇒ 回补跑旧日期会返回 None（不是 0）。
    "sh_open": "上证开盘",
    "sh_high": "上证最高",
    "sh_low": "上证最低",
    "sh_open_gap_pct": "上证开盘缺口%",
    # ★★ 2026-11-26（用户新卡验证项："创业板以 2183.58 为锚不再新低"）：
    #   创业板 399006 早已在 `key_level_watch` 里（含 open/high/low），此前无 metric。
    "sz_cyb_close": "创业板收盘",
    "sz_cyb_low": "创业板最低",
    "sz_cyb_chg_pct": "创业板涨跌%",
    # ★★ 2026-11-26：半导体板块当日涨跌（校准 SOX→A股 映射系数用）。
    "bk_semi_chg_pct": "半导体板块涨跌%",
    # ★★ 2026-11-28：布伦特原油（12 月剧本的"阴延长开关"：>92 打开 / <90 回基准）。
    "brent": "布伦特原油(美元)",
    # ★★ 2026-11-28：两融三件套（数据源早已存在，只是没接线）。
    #   ⚠️ T+1 口径：今天看到的是**上一交易日**的余额（margin_date 自证）。
    "margin_total_yi": "两融余额(亿)",
    "margin_add_yi": "两融单日变化(亿)",
    "margin_up_days": "两融连续同向天数(+升/-降)",
}
# 比较符：只支持这四种（避免 eval / 注入）
_OPS = ("<=", ">=", "<", ">", "==")


# ══════════════════════════════════════════════════════════════════════════
#  建表
# ══════════════════════════════════════════════════════════════════════════
def ensure_tables() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_SCEN} (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            horizon_start TEXT,
            horizon_end TEXT,
            scenarios_json TEXT,
            checks_json TEXT,
            invalidators_json TEXT,
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'manual',
            created_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_DAILY} (
            thesis_id TEXT NOT NULL,
            date TEXT NOT NULL,
            checked_json TEXT,
            scenario_now TEXT,
            probs_json TEXT,
            llm_json TEXT,
            pushed INTEGER DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY (thesis_id, date)
        )
    """)
    # ★★ 2026-11-22（用户："日批…还需输出推演文本"）：
    #   企微只发精简版 `_markdown()`，这里存**完整文本**（情景/逐项/修正/证伪全文），
    #   供页面查看与复制。企微消息会被刷掉，全文必须留在库里。
    #   ⚠️ sqlite 的 ADD COLUMN 不支持 IF NOT EXISTS ⇒ 分两支。
    try:
        if db.is_postgres:
            db.execute(f"ALTER TABLE {_T_DAILY} "
                       f"ADD COLUMN IF NOT EXISTS report_text TEXT")
        else:
            cols = [r.get("name") for r in
                    (db.fetch("PRAGMA table_info(%s)" % _T_DAILY) or [])]
            if "report_text" not in cols:
                db.execute(f"ALTER TABLE {_T_DAILY} ADD COLUMN report_text TEXT")
    except Exception as e:
        print(f"[thesis] add report_text failed: {str(e)[:80]}")
    _READY = True


# ══════════════════════════════════════════════════════════════════════════
#  工具
# ══════════════════════════════════════════════════════════════════════════
def _now() -> str:
    return datetime.now(_BJ).isoformat(timespec="seconds")


def _loads(s) -> list:
    if not s:
        return []
    try:
        v = json.loads(s) if isinstance(s, str) else s
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _dumps(v) -> str:
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return "[]"


def _prev_trading_day(day: str) -> Optional[str]:
    """`day` 的上一个交易日（YYYY-MM-DD）；推不出返回 None。

    ★ 2026-11-25（算"开盘缺口%"时必须先确定"上一交易日"）：
      不能用 `date < day ORDER BY date DESC LIMIT 1` 随便取一条 ——
      日批漏跑/长假/停牌时，取到的可能是 3~5 天前的收盘，
      中间指数可能已涨跌 5% ⇒ 缺口失真成假数。
      优先用 `flash.rules.is_trading_day`（含 fuyao 交易日历）；不可用时退化为只跳周末。
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    try:
        d = _dt.strptime(str(day)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    _is_td = None
    try:
        from app.flash.rules import is_trading_day as _is_td
    except Exception:
        _is_td = None
    for _ in range(20):          # 最长春节/国庆连休 + 前后周末，20 天足够
        d -= _td(days=1)
        try:
            ok = bool(_is_td(d)) if _is_td else d.weekday() < 5
        except Exception:
            ok = d.weekday() < 5
        if ok:
            return d.strftime("%Y-%m-%d")
    return None


def _f(v) -> Optional[float]:
    """转 float；转不动返回 None（**不返回 0** —— 0 会被读成"已实现"）。"""
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f_loose(v) -> Optional[float]:
    """宽容数值解析 —— **只给 `parse_text` 用**（解析 LLM 输出）。

    ★ 2026-11-22（审查发现）：LLM 常把概率写成 `"35%"`、阈值写成 `"3500点"`，
    严格 `_f` 对这类一律返回 None ⇒ 情景/验证项被**静默丢弃**，
    用户只看到"解析不出现景"，完全不知道为什么。
    这里剥掉单位、取第一个数字。

    ⚠️ **绝不能用于 `evaluate_day`**：实际值必须严格，
    脏值要显式 `nodata` 而不是"凑一个数出来"（0 会被读成"已实现"）。
    ⚠️ 不做 万/亿 换算（`"1.2万亿"` 会取到 1.2）—— 这种歧义交给用户核对，
       解析后的 `warnings` 会提示"有数值来自带单位文本"。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _cmp(op: str, actual: Optional[float], target: Optional[float]) -> str:
    """pass / fail / nodata。"""
    if actual is None or target is None:
        return "nodata"
    op = op if op in _OPS else "<="
    if op == "<=":
        return "pass" if actual <= target else "fail"
    if op == ">=":
        return "pass" if actual >= target else "fail"
    if op == "<":
        return "pass" if actual < target else "fail"
    if op == ">":
        return "pass" if actual > target else "fail"
    return "pass" if abs(actual - target) < 1e-9 else "fail"


# ══════════════════════════════════════════════════════════════════════════
#  指标取数（确定性，零 LLM）
# ══════════════════════════════════════════════════════════════════════════
def metric_value(key: str, day: str) -> Tuple[Optional[float], str]:
    """取某个验证项的当日实际值 → (value, note)。取不到返回 (None, 原因)。

    ⚠️ 每个分支都必须能回答"为什么没有"—— 空白原因会让人以为指标不存在。
    """
    try:
        if key == "amount_yi":
            r = db.fetch_one(
                "SELECT amount_yi FROM market_amount_daily WHERE date=%s", (day,)) or {}
            v = _f(r.get("amount_yi"))
            return (v, "" if v is not None else "无当日成交额记录")

        if key in ("limit_down", "limit_up", "down_ratio", "up_ratio"):
            r = db.fetch_one("""
                SELECT close_limit_down, close_limit_up, close_up, close_down,
                       up, down
                FROM market_emotion_daily WHERE date=%s
            """, (day,)) or {}
            ld = _f(r.get("close_limit_down"))
            lu = _f(r.get("close_limit_up"))
            up = _f(r.get("close_up"))
            down = _f(r.get("close_down"))
            if key == "limit_down":
                return (ld, "" if ld is not None else "无收盘跌停家数")
            if key == "limit_up":
                return (lu, "" if lu is not None else "无收盘涨停家数")
            if key == "down_ratio":
                if up is None or down is None or (up + down) <= 0:
                    return (None, "无收盘涨跌家数")
                return (round(down / (up + down), 4), "")
            if up is None or down is None or (up + down) <= 0:
                return (None, "无收盘涨跌家数")
            return (round(up / (up + down), 4), "")

        if key in ("sh_close", "sh_chg_pct", "sh_open", "sh_high", "sh_low",
                   "sh_open_gap_pct"):
            # ★ 2026-11-25（用户：'标准③「盘中低点 ≥3520」用 EOD 快照的日低字段就能测'）：
            #   `key_level_watch` 已补 open/high/low 落库 ⇒ 开盘位与日内低点可测。
            #   ⚠️ 历史行（11-25 之前）这三列是 NULL ⇒ 返回 None + 明确原因，
            #      **不返回 0**（0 会被误读为"开在 0 点"，且会污染命中率统计）。
            r = db.fetch_one("""
                SELECT close, chg_pct, open, high, low FROM key_level_watch
                WHERE date=%s AND code='000001'
            """, (day,)) or {}
            if key in ("sh_close", "sh_chg_pct"):
                v = _f(r.get("close") if key == "sh_close" else r.get("chg_pct"))
                return (v, "" if v is not None
                        else "无上证指数记录（key_level_watch 当日未落库）")
            col = {"sh_open": "open", "sh_high": "high", "sh_low": "low"}.get(key)
            if col:
                v = _f(r.get(col))
                if v is not None:
                    return (v, "")
                if not r:
                    return (None, "无上证指数记录（key_level_watch 当日未落库）")
                return (None, f"上证 {col} 未落库（该字段 2026-11-25 起才有）")
            # 开盘缺口% = (open / **上一交易日**收盘 - 1) * 100
            #   用户标准③的"低开 0.5-1.5%"测的就是这个。
            # ⚠️⚠️ **不能**用 `date < day ORDER BY date DESC LIMIT 1` 随便取一条：
            #   若某几天日批没跑（或停牌/长假），取到的可能是 3~5 天前的收盘，
            #   而中间指数可能已涨跌 5% ⇒ 算出来的"缺口"完全是假的。
            #   ⇒ 先算**理论上的上一交易日**，再精确匹配那一天，缺了就返回 None。
            op = _f(r.get("open"))
            prev_td = _prev_trading_day(day)
            if not prev_td:
                return (None, f"无法确定 {day} 的上一交易日")
            prev = db.fetch_one("""
                SELECT close FROM key_level_watch
                WHERE code='000001' AND date=%s
            """, (prev_td,)) or {}
            pc = _f(prev.get("close"))
            if op is None:
                return (None, "上证当日开盘价未落库")
            if not pc:
                return (None, f"上一交易日 {prev_td} 无收盘（该日日批未跑）")
            return (round((op / pc - 1) * 100, 2), "")

        if key in ("sz_cyb_close", "sz_cyb_low", "sz_cyb_chg_pct"):
            # ★★ 2026-11-26（用户："创业板以 2183.58 为锚不再新低"）：创业板指 399006。
            #   `key_level_watch` 本来就把 399006 一起算并落库（含 open/high/low），
            #   之前没有对应的 metric ⇒ 这条验证项只能靠人工看。
            #   ⚠️ 与 sh_* 同口径：历史行 open/high/low 为 NULL ⇒ 返回 None + 原因。
            r = db.fetch_one("""
                SELECT close, chg_pct, open, high, low FROM key_level_watch
                WHERE date=%s AND code='399006'
            """, (day,)) or {}
            if not r:
                return (None, "无创业板指记录（key_level_watch 当日未落库）")
            if key == "sz_cyb_close":
                v = _f(r.get("close"))
            elif key == "sz_cyb_chg_pct":
                v = _f(r.get("chg_pct"))
            else:
                v = _f(r.get("low"))
            if v is not None:
                return (v, "")
            return (None, f"创业板 {key} 未落库（low 字段 2026-11-25 起才有）")

        if key == "bk_semi_chg_pct":
            # ★★ 2026-11-26：半导体板块当日涨跌 —— 用来**校准 SOX→A股 的映射系数**。
            #   11-25 实测：SOX -7.88% → 半导体 -7.86%（系数 0.997），
            #   而"电子"口径只有 -6.12%（含消费电子拖累）⇒ 一刀切系数必然错。
            #   ⇒ 单独拆出板块指标，攒够样本才能把"拍"的系数换成回测值。
            row = db.fetch_one("""
                SELECT change_pct FROM plate_daily_zz
                WHERE date=%s AND kind='industry' AND name=%s
            """, (day, "半导体")) or {}
            v = _f(row.get("change_pct"))
            if v is None:
                # 板块名的口径可能因源变化（zzshare 104 粗分）⇒ 退一步模糊匹配
                row = db.fetch_one("""
                    SELECT change_pct FROM plate_daily_zz
                    WHERE date=%s AND kind='industry' AND name LIKE %s
                    ORDER BY change_pct ASC LIMIT 1
                """, (day, "%半导体%")) or {}
                v = _f(row.get("change_pct"))
            return (v, "" if v is not None
                    else "半导体板块当日无快照（plate_daily_zz 未落库）")

        if key == "brent":
            # ★★ 2026-11-28（用户："按财经日历做提前预案，先推演，如果xxx应该xxx"）：
            #   12 月剧本里布油是"阴延长开关"（>92 打开 / <90 回基准），
            #   宏观面板**早就有**这项，只是 `thesis_scenarios` 没接 —— 与 11-25
            #   补 open/high/low、11-26 补创业板同款：数据现成，缺的是接线。
            try:
                from app.macro import get_macro_panel
                _p = get_macro_panel() or {}
                v = _f((_p.get("brent") or {}).get("price"))
            except Exception:
                v = None
            return (v, "" if v is not None else "宏观面板无布油报价")

        if key in ("margin_total_yi", "margin_add_yi", "margin_up_days"):
            # ★★ 2026-11-28（用户："找"两融数据源）—— **系统早就有了**：
            #   `flash.margin_sentiment.get_margin()`（金十 _vir_96，沪+深，T+1，1 天缓存），
            #   另有东财 `RPTA_RZRQ_LSHJ` 兜底（沪深北口径，约 +70 亿台阶，见 liquidity_watch）。
            #   实测 11-26 = **21276.5 亿**，与用户推演里的 21276.55 完全对上。
            #   ⚠️ 这又是一次"数据现成、只是没接线"：`liquidity_watch` 一直在用，
            #      但 `thesis_scenarios` 的 metric 没接 ⇒ 推演里 6 处两融验证项全不可测。
            #   ⚠️ 我用 `LIKE '%margin%'` 搜表名所以误判"没有两融表"——表叫 `liquidity_daily`。
            try:
                from app.flash.margin_sentiment import get_margin
                _m = get_margin(days=12) or {}
            except Exception:
                _m = {}
            _hist = _m.get("hist") or []          # 倒序：[{date, fund_bal}]，最新在前
            _vals = [_f(h.get("fund_bal")) for h in _hist]
            _vals = [v for v in _vals if v is not None]
            if not _vals:
                return (None, "两融数据不可用（金十 margin 接口失败或未配置 FLASH_COOKIE）")
            if key == "margin_total_yi":
                return (round(_vals[0], 1), "")
            if key == "margin_add_yi":
                if len(_vals) < 2:
                    return (None, "两融历史不足 2 天，无法算单日变化")
                return (round(_vals[0] - _vals[1], 1), "")
            # margin_up_days：连续同向天数（+3 = 连升 3 天；-2 = 连降 2 天）
            _d = 0
            for _i in range(len(_vals) - 1):
                _diff = _vals[_i] - _vals[_i + 1]
                if _diff > 0:
                    if _d >= 0:
                        _d += 1
                    else:
                        break
                elif _diff < 0:
                    if _d <= 0:
                        _d -= 1
                    else:
                        break
                else:
                    break
            return (_d, "")

        if key == "breadth_gap":
            from app import breadth_divergence as bd
            s = bd.snapshot(day)
            v = _f((s or {}).get("gap_pp")) if (s or {}).get("ok") else None
            return (v, "" if v is not None else "广度背离当日无数据")

        if key == "icbc_chg_pct":
            # 工行日线：当日收盘 vs **上一交易日**收盘（不用前收字段，避免口径不一致）
            rows = db.fetch("""
                SELECT date, close FROM backtest_prices
                WHERE code='601398' AND date<=%s
                ORDER BY date DESC LIMIT 2
            """, (day,)) or []
            if not rows:
                return (None, "工行 601398 无日线")
            if str(rows[0].get("date")) != day:
                return (None, f"工行当日无日线（最近 {rows[0].get('date')}）")
            cur = _f(rows[0].get("close"))
            prev = _f(rows[1].get("close")) if len(rows) > 1 else None
            if cur is None or not prev:
                return (None, "工行收盘价缺失或不足两日")
            return (round((cur - prev) / prev * 100, 2), "")
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）
        print(f"[thesis] metric {key} failed: {str(e)[:80]}")
        return (None, f"取数失败: {str(e)[:40]}")
    return (None, f"未知指标 {key}")


# ══════════════════════════════════════════════════════════════════════════
#  登记 / 查询 / 归档
# ══════════════════════════════════════════════════════════════════════════
def _norm_checks(checks) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """校验并规整验证项 → (norm, error)。

    ★ 2026-11-23：`create()` 与 `update_checks()` **共用这一份口径**。
      项目踩过 `_bench_code` / `SPREAD_THRESHOLD` 两处各写一份导致漂移的事故，
      所以这里宁可多抽一层，也不让"能登记但改不了"这种事发生。
    """
    if not isinstance(checks, list) or not checks:
        return None, "checks 不能为空"
    out: List[Dict] = []
    seen = set()
    for c in checks:
        if not isinstance(c, dict):
            return None, "checks 每项需为对象"
        key = str(c.get("key") or "").strip()
        if key not in METRIC_LABELS:
            return None, f"未知指标 {key}；可用: {', '.join(METRIC_LABELS)}"
        if key in seen:
            # 同一指标登记两次会让"命中 n 项"的统计失真
            return None, f"指标 {key} 重复"
        seen.add(key)
        op = str(c.get("op") or "<=").strip()
        if op not in _OPS:
            return None, f"op 只支持 {_OPS}"
        val = _f(c.get("value"))
        if val is None:
            return None, f"checks[{key}] 的 value 需为数字"
        out.append({"key": key,
                    "label": str(c.get("label") or METRIC_LABELS[key]),
                    "op": op, "value": val})
    return out, None


def _norm_invalidators(v) -> List[str]:
    """证伪条件规整 ⇒ **永远是字符串数组**。

    ★★ 2026-11-25（真 BUG，用户实测）：原 `create()` 直接写
      `[str(x).strip() for x in (payload.get("invalidators") or [])]`。
      当调用方传**字符串**时（例如把一整段证伪说明当作单个条件传入），
      `for x in "切A：放量..."` 会**逐字符迭代** ⇒ 落库成
      `["切","A","：","放","量",...]`，LLM prompt 里变成每行一个字，
      前端也显示成一列单字。

      ⚠️ 契约：字符串 = **一个条件**（不要猜分隔符拆句，中文标点混排拆不准）；
         数组 = 多个条件，逐项清洗。两种输入都收敛到 List[str]。
    """
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    if isinstance(v, (list, tuple)):
        # ⚠️ 必须先判 None 再 str()：否则 null 元素会变成字符串 "None"
        #    被当成一条有效证伪条件（LLM 输出的数组里确实可能有 null）
        out = []
        for x in v:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    s = str(v).strip()
    return [s] if s else []


def create(payload: Dict) -> Dict:
    """登记一份推演。返回 {ok, id} 或 {ok:False, error}。

    校验（宁可拒绝也不写脏数据）：
      · title / horizon_start / horizon_end 必填
      · scenarios 非空、每项有 name、prob 合计 100±1
      · checks 非空、每项 key 在 METRIC_LABELS 内、op 合法、value 是数字
    """
    ensure_tables()
    title = str(payload.get("title") or "").strip()
    hs = str(payload.get("horizon_start") or "").strip()[:10]
    he = str(payload.get("horizon_end") or "").strip()[:10]
    if not title:
        return {"ok": False, "error": "title 必填"}
    for nm, v in (("horizon_start", hs), ("horizon_end", he)):
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": f"{nm} 需 YYYY-MM-DD"}
    if hs > he:
        return {"ok": False, "error": "horizon_start 不能晚于 horizon_end"}

    scenarios = payload.get("scenarios") or []
    if not isinstance(scenarios, list) or not scenarios:
        return {"ok": False, "error": "scenarios 不能为空"}
    norm_sc = []
    total = 0.0
    for s in scenarios:
        if not isinstance(s, dict):
            return {"ok": False, "error": "scenarios 每项需为对象"}
        name = str(s.get("name") or "").strip()
        prob = _f(s.get("prob"))
        if not name or prob is None:
            return {"ok": False, "error": "scenarios 每项需有 name 与数字 prob"}
        total += prob
        norm_sc.append({"name": name, "prob": round(prob, 1),
                        "path": str(s.get("path") or "").strip()})
    if abs(total - 100) > 1:
        return {"ok": False, "error": f"情景概率合计需为 100（当前 {total:g}）"}

    norm_ck, err = _norm_checks(payload.get("checks") or [])
    if err:
        return {"ok": False, "error": err}
    norm_inv = _norm_invalidators(payload.get("invalidators"))

    tid = uuid.uuid4().hex[:12]
    try:
        db.insert(_T_SCEN, {
            "id": tid, "title": title,
            "horizon_start": hs, "horizon_end": he,
            "scenarios_json": _dumps(norm_sc),
            "checks_json": _dumps(norm_ck),
            "invalidators_json": _dumps(norm_inv),
            "status": "active",
            "source": str(payload.get("source") or "manual"),
            "created_at": _now(),
        })
    except Exception as e:
        print(f"[thesis] create failed: {str(e)[:100]}")
        return {"ok": False, "error": f"落库失败: {str(e)[:60]}"}

    # 归档旧的同标题推演，避免同一周跑两份
    try:
        db.execute(f"UPDATE {_T_SCEN} SET status='archived' "
                   f"WHERE status='active' AND title=%s AND id<>%s", (title, tid))
    except Exception as e:
        print(f"[thesis] archive same-title failed: {str(e)[:80]}")
    return {"ok": True, "id": tid}


def _row_to_thesis(r: Dict) -> Dict:
    return {
        "id": r.get("id"),
        "title": r.get("title"),
        "horizon_start": r.get("horizon_start"),
        "horizon_end": r.get("horizon_end"),
        "scenarios": _loads(r.get("scenarios_json")),
        "checks": _loads(r.get("checks_json")),
        "invalidators": _loads(r.get("invalidators_json")),
        "status": r.get("status"),
        "source": r.get("source"),
        "created_at": r.get("created_at"),
    }


def list_theses(active_only: bool = False, day: str = None) -> List[Dict]:
    ensure_tables()
    try:
        if active_only:
            d = str(day or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
            rows = db.fetch(f"""
                SELECT * FROM {_T_SCEN}
                WHERE status='active' AND horizon_start<=%s AND horizon_end>=%s
                ORDER BY created_at DESC LIMIT %s
            """, (d, d, MAX_ACTIVE)) or []
        else:
            rows = db.fetch(f"SELECT * FROM {_T_SCEN} "
                            f"ORDER BY created_at DESC LIMIT 50") or []
    except Exception as e:
        print(f"[thesis] list failed: {str(e)[:90]}")
        return []
    return [_row_to_thesis(r) for r in rows]


def get_thesis(tid: str) -> Optional[Dict]:
    ensure_tables()
    try:
        r = db.fetch_one(f"SELECT * FROM {_T_SCEN} WHERE id=%s", (tid,))
    except Exception as e:
        print(f"[thesis] get failed: {str(e)[:80]}")
        return None
    return _row_to_thesis(r) if r else None


def archive(tid: str) -> bool:
    ensure_tables()
    try:
        db.execute(f"UPDATE {_T_SCEN} SET status='archived' WHERE id=%s", (tid,))
        return True
    except Exception as e:
        print(f"[thesis] archive failed: {str(e)[:80]}")
        return False


def update_checks(tid: str, checks) -> Dict:
    """替换验证项 —— **不重建推演，历史对照全部保留**。

    ★ 2026-11-23（用户："验证框架必须匹配行情性质，性质变了要立刻换框架"）：
      换尺子是常态，但重建推演会换 id ⇒ 历史对照就断了（`thesis_daily` 按 id 关联）。
      所以单独给一个只改 checks 的口径。
    只允许改 `status='active'` 的推演；已归档的拒改（避免改完又被日批捡起来）。
    """
    ensure_tables()
    t = get_thesis(tid)
    if not t:
        return {"ok": False, "error": "推演不存在"}
    if t.get("status") != "active":
        return {"ok": False, "error": "已归档的推演不能改"}
    norm, err = _norm_checks(checks)
    if err:
        return {"ok": False, "error": err}
    try:
        db.execute(f"UPDATE {_T_SCEN} SET checks_json=%s WHERE id=%s",
                   (_dumps(norm), tid))
    except Exception as e:
        print(f"[thesis] update_checks failed: {str(e)[:90]}")
        return {"ok": False, "error": f"落库失败: {str(e)[:60]}"}
    return {"ok": True, "id": tid, "checks": norm}


# ══════════════════════════════════════════════════════════════════════════
#  每日对照（确定性）
# ══════════════════════════════════════════════════════════════════════════
def evaluate_day(thesis: Dict, day: str) -> Dict:
    """逐项对照 → {checked:[...], n_pass, n_fail, n_nodata}。不调 LLM。"""
    checked = []
    for c in thesis.get("checks") or []:
        key = c.get("key")
        val, note = metric_value(key, day)
        verdict = _cmp(c.get("op"), val, c.get("value"))
        checked.append({
            "key": key,
            "label": c.get("label") or METRIC_LABELS.get(key, key),
            "op": c.get("op"), "target": c.get("value"),
            "actual": val, "verdict": verdict,
            "note": note or "",
        })
    return {
        "checked": checked,
        "n_pass": sum(1 for x in checked if x["verdict"] == "pass"),
        "n_fail": sum(1 for x in checked if x["verdict"] == "fail"),
        "n_nodata": sum(1 for x in checked if x["verdict"] == "nodata"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  LLM 修正
# ══════════════════════════════════════════════════════════════════════════
_SYSTEM = """你是交易系统的"推演复盘官"。任务：拿用户**事先登记**的推演，
对照今天已经发生的实际数据，判断哪个情景正在兑现、概率该怎么修正。

铁律：
1. 只复盘，不做新预测，不给买卖建议。
2. 只依据给定的实际数据；**没有的数据就是没有，不许推测或编造**。
3. 必须指出推演里**已经被证伪的假设**（没有就返回空数组）。
4. 必须回答"什么情况说明我这次的修正也错了"（falsifier），这是硬要求。
5. 输出严格 JSON，不要 markdown 代码块。"""


def _prompt(thesis: Dict, day: str, ev: Dict) -> str:
    sc = "\n".join("- %s（%s%%）：%s" % (s.get("name"), s.get("prob"), s.get("path"))
                   for s in thesis.get("scenarios") or [])
    ck = "\n".join(
        "%s %s %s → 实际 %s（%s）%s"
        % (x["label"], x["op"], x["target"],
           "无数据" if x["actual"] is None else x["actual"],
           x["verdict"],
           ("；" + x["note"]) if x.get("note") else "")
        for x in ev.get("checked") or [])
    inv = "\n".join("- " + s for s in thesis.get("invalidators") or []) or "（未登记）"
    return f"""【推演】{thesis.get('title')}（{thesis.get('horizon_start')} ~ {thesis.get('horizon_end')}）

情景：
{sc}

用户登记的证伪条件：
{inv}

【今日实际（{day}）】
{ck}

统计：pass {ev.get('n_pass')} / fail {ev.get('n_fail')} / nodata {ev.get('n_nodata')}

请输出 JSON：
{{
  "scenario_now": "当前最像哪个情景名（必须来自上面情景之一）",
  "probs": {{"情景名": 修正后概率整数, ...}},
  "progress": "一两句话：兑现进度（引用上面的实际数字）",
  "invalidated": ["已被证伪的假设，没有就空数组"],
  "next_watch": ["接下来 1~3 个交易日最该盯的具体指标"],
  "falsifier": "什么情况说明这次修正也错了"
}}"""


def llm_review(thesis: Dict, day: str, ev: Dict) -> Optional[Dict]:
    """一次 LLM 调用；失败返回 None（**不抛** —— 确定性对照已落库，不能因 LLM 丢了）。"""
    if not LLM_ENABLED:
        return None
    try:
        from app.flash import llm
    except Exception as e:
        print(f"[thesis] llm import failed: {str(e)[:80]}")
        return None
    try:
        txt = llm.call_llm(_SYSTEM, _prompt(thesis, day, ev),
                           temperature=0.2, json_mode=True, tier="slow")
    except Exception as e:
        print(f"[thesis] llm call failed: {str(e)[:90]}")
        return None
    if not txt:
        print("[thesis] llm 返回空（provider 未配置/熔断）")
        return None
    s = txt.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    try:
        obj = json.loads(s)
    except ValueError:
        # 容错：截取第一个 { 到最后一个 }
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            print(f"[thesis] llm 非法 JSON: {txt[:80]}")
            return None
        try:
            obj = json.loads(s[i:j + 1])
        except ValueError:
            print(f"[thesis] llm 非法 JSON(2): {txt[:80]}")
            return None
    return obj if isinstance(obj, dict) else None


# ══════════════════════════════════════════════════════════════════════════
#  文本导入（粘贴 → LLM 结构化 → 回填表单，**不落库**）
# ══════════════════════════════════════════════════════════════════════════
# ★★ 2026-11-22（用户："加入一个导入功能，并且有格式举例，内容可以由 llm 生成，
#   自动写入，我到时审阅，有问题我会自行修改并登记"）
#   定位：**只解析、不写库** —— 解析结果回填表单，用户审阅/改数字后自己点登记。
#   这样 LLM 认错指标或编造阈值时，人工一定看得见（直接写库就绕过了审阅）。
MAX_PARSE_CHARS = 8000

_PARSE_SYSTEM = """你是"推演文本结构化解析器"。把用户粘贴的自然语言推演解析成 JSON。

【唯一允许的指标 key】（不在列表里的一律不要硬塞，放进 unparsed）
- sh_close        上证收盘（点数）
- sh_chg_pct      上证涨跌%（百分数，跌为负，如 -1.2）
- sh_open         上证开盘（点数）
- sh_high         上证最高（点数）
- sh_low          上证最低（点数）
- sh_open_gap_pct 上证开盘缺口%（正=高开，负=低开）
- sz_cyb_close    创业板指收盘（点数）
- sz_cyb_low      创业板指最低（点数）
- sz_cyb_chg_pct  创业板指涨跌%（百分数）
- bk_semi_chg_pct 半导体板块涨跌%（百分数）
- amount_yi       两市成交额（亿元）
- limit_down      跌停家数
- limit_up        涨停家数
- down_ratio      下跌占比（0~1 的小数，如 0.95 表示 95% 在跌）
- breadth_gap     广度背离（百分点 pp）
- icbc_chg_pct    工商银行涨跌%（红利端代理）
- brent           布伦特原油（美元/桶）
- margin_total_yi 两融余额（亿元，T+1）
- margin_add_yi   两融单日变化（亿元，正=增）
- margin_up_days  两融连续同向天数（+3=连升3天，-2=连降2天）

【规则】
1. 只输出 JSON，不要 markdown 代码块、不要解释。
2. scenarios 的 prob 合计必须等于 100；原文没写概率就按你判断给一个合计 100 的分配，
   并在 unparsed 里注明"概率为自动分配"。
3. checks 的 op 只能是 <= >= < > == 之一；value 必须是数字。
4. 原文提到但无法映射到上面这些指标的（纳指、SOX、英伟达、汇率、北向、个股价格、
   PCE、地缘事件等），**放进 unparsed 数组**，不要塞进 checks。
   ⚠️ "低开 0.5-1.5%" 映射成 sh_open_gap_pct <= -0.5 与 >= -1.5 两条；
      "盘中低点 ≥3520" 映射成 sh_low >= 3520；
      "创业板不再新低 2183.58" 映射成 sz_cyb_close >= 2183.58。
5. 日期缺失：horizon_start 用今天，horizon_end 用今天+6 天。
6. invalidators（证伪条件）是给复盘用的文字判据，**原样放进 invalidators 即可**，
   **不要**因为里面提到点位/成交额就把它塞进 unparsed —— 只有"验证项/判断依据"
   里出现不可映射内容时才用 unparsed。

【输出格式】
{
  "title": "简短标题",
  "horizon_start": "YYYY-MM-DD",
  "horizon_end": "YYYY-MM-DD",
  "scenarios": [{"name": "情景名", "prob": 40, "path": "路径描述"}],
  "checks": [{"key": "sh_close", "label": "上证收盘", "op": "<=", "value": 3500}],
  "invalidators": ["证伪条件原文"],
  "unparsed": ["无法映射的原文片段 + 原因"]
}"""


def _today() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


def parse_text(text: str) -> Dict:
    """把自由文本解析成可登记草案。返回 {ok, draft, warnings} 或 {ok:False, error}。

    ⚠️ 绝不落库：即使解析成功也只返回草案，由用户在页面上审阅后自己点「登记」。
    """
    t = (text or "").strip()
    if not t:
        return {"ok": False, "error": "文本为空"}
    if len(t) > MAX_PARSE_CHARS:
        return {"ok": False,
                "error": f"文本过长（{len(t)} 字），上限 {MAX_PARSE_CHARS}"}
    if not LLM_ENABLED:
        return {"ok": False, "error": "LLM 已关闭（THESIS_LLM=0），无法解析"}

    try:
        from app.flash import llm
    except Exception as e:
        return {"ok": False, "error": f"LLM 模块不可用: {str(e)[:60]}"}

    try:
        txt = llm.call_llm(_PARSE_SYSTEM, t, temperature=0.1,
                           json_mode=True, tier="slow")
    except Exception as e:
        return {"ok": False, "error": f"LLM 调用失败: {str(e)[:80]}"}
    if not txt:
        return {"ok": False, "error": "LLM 返回空（provider 未配置/熔断）"}

    s = txt.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    try:
        obj = json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return {"ok": False, "error": "LLM 返回非法 JSON"}
        try:
            obj = json.loads(s[i:j + 1])
        except ValueError:
            return {"ok": False, "error": "LLM 返回非法 JSON"}
    if not isinstance(obj, dict):
        return {"ok": False, "error": "LLM 返回不是对象"}

    warnings = []
    for u in (obj.get("unparsed") or []):
        u = str(u).strip()
        if u:
            warnings.append(u)

    # ── 逐项校验/规整（口径与 create 一致，避免"能导入但登记被拒"）──
    title = str(obj.get("title") or "").strip() or "未命名推演"
    hs = str(obj.get("horizon_start") or "").strip()[:10] or _today()
    he = str(obj.get("horizon_end") or "").strip()[:10]
    if not he:
        try:
            he = (datetime.strptime(hs, "%Y-%m-%d")
                  + timedelta(days=6)).strftime("%Y-%m-%d")
        except ValueError:
            he = hs
    for nm, v in (("horizon_start", hs), ("horizon_end", he)):
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": f"解析出的 {nm} 不是 YYYY-MM-DD：{v}"}
    if hs > he:
        hs, he = he, hs
        warnings.append("开始/结束日期被自动对调")

    sc_out = []
    _loose_n = 0        # 有几个数值是从"带单位文本"剥出来的（最后提示用户核对）
    for s_ in (obj.get("scenarios") or []):
        if not isinstance(s_, dict):
            continue
        name = str(s_.get("name") or "").strip()
        _raw_prob = s_.get("prob")
        prob = _f_loose(_raw_prob)
        if isinstance(_raw_prob, str) and prob is not None:
            _loose_n += 1
        if not name or prob is None:
            continue
        sc_out.append({"name": name, "prob": round(prob, 1),
                       "path": str(s_.get("path") or "").strip()})
    if not sc_out:
        return {"ok": False, "error": "解析不出现景，请检查文本格式"}
    total = sum(x["prob"] for x in sc_out)
    if abs(total - 100) > 1:
        warnings.append(f"情景概率合计 {round(total, 1)}%，登记前请改成 100%")

    ck_out = []
    dropped = []
    for c_ in (obj.get("checks") or []):
        if not isinstance(c_, dict):
            continue
        key = str(c_.get("key") or "").strip()
        op = str(c_.get("op") or "<=").strip()
        _raw_val = c_.get("value")
        val = _f_loose(_raw_val)
        if isinstance(_raw_val, str) and val is not None:
            _loose_n += 1
        if key not in METRIC_LABELS:
            dropped.append(f"{key or '?'}（不在可用指标）")
            continue
        if op not in _OPS or val is None:
            dropped.append(f"{METRIC_LABELS[key]}（op 或阈值非法）")
            continue
        ck_out.append({"key": key, "label": METRIC_LABELS[key],
                       "op": op, "value": val})
    if dropped:
        warnings.append("已丢弃无法登记的验证项：" + "、".join(dropped))
    if _loose_n:
        warnings.append(
            "有 %d 个数值来自带单位的文本（如 35%% / 3500点），"
            "已按字面数字解析，请核对" % _loose_n)
    if not ck_out:
        return {"ok": False, "error": "解析不出可用验证项（指标需在 7 个内置项内）"}

    # ★ 2026-11-25：与 create() 共用同一份规整口径 —— LLM 偶尔会把
    #   多个证伪条件写成一整段字符串，直接迭代会逐字符拆开。
    inv = _norm_invalidators(obj.get("invalidators"))

    return {"ok": True,
            "draft": {"title": title, "horizon_start": hs, "horizon_end": he,
                      "scenarios": sc_out, "checks": ck_out,
                      "invalidators": inv},
            "warnings": warnings}


# ══════════════════════════════════════════════════════════════════════════
#  落库 + 推送
# ══════════════════════════════════════════════════════════════════════════
def save_daily(tid: str, day: str, ev: Dict,
               review: Optional[Dict], thesis: Optional[Dict] = None) -> bool:
    """落库当日对照。thesis 传了就顺带生成完整推演文本（report_text）。"""
    ensure_tables()
    try:
        row = {
            "thesis_id": tid, "date": day,
            "checked_json": _dumps(ev.get("checked")),
            "scenario_now": (review or {}).get("scenario_now"),
            "probs_json": _dumps((review or {}).get("probs") or []),
            "llm_json": _dumps(review) if review else None,
            "pushed": 0,
            "created_at": _now(),
        }
        if thesis:
            row["report_text"] = _full_text(thesis, day, ev, review)
        db.upsert(_T_DAILY, row, conflict_columns=["thesis_id", "date"])
        return True
    except Exception as e:
        print(f"[thesis] save_daily failed: {str(e)[:90]}")
        return False


def _full_text(thesis: Dict, day: str, ev: Dict,
               review: Optional[Dict]) -> str:
    """完整推演文本（落库 + 页面复制）。

    与 `_markdown()` 的区别：markdown 是**企微推送用的精简版**（无表格、短），
    本函数是**存档全文** —— 情景路径、逐项对照含 note、六个修正字段全给。
    企微消息会被刷掉，全文必须留在库里。
    """
    L = ["【推演复盘】%s" % (thesis.get("title") or ""),
         "日期：%s　有效期：%s ~ %s"
         % (day, thesis.get("horizon_start"), thesis.get("horizon_end")),
         ""]

    L.append("一、情景（登记时）")
    scs = thesis.get("scenarios") or []
    if scs:
        for s in scs:
            path = ("　—　" + str(s.get("path"))) if s.get("path") else ""
            # %g：40.0 → 40，避免文本里出现"40.0%"这种多余小数
            L.append("  %s　%g%%%s"
                     % (s.get("name"), s.get("prob") or 0, path))
    else:
        L.append("  （未登记）")
    L.append("")

    L.append("二、当日对照（%s）" % day)
    checks = ev.get("checked") or []
    if checks:
        for x in checks:
            mark = {"pass": "✓", "fail": "✗", "nodata": "—"}.get(x["verdict"], "?")
            act = "无数据" if x["actual"] is None else x["actual"]
            note = ("（%s）" % x["note"]) if x.get("note") else ""
            L.append("  %s %s %s %s　实际 %s%s"
                     % (mark, x.get("label"), x.get("op"), x.get("target"),
                        act, note))
    else:
        L.append("  （无验证项）")
    L.append("  统计：命中 %d / 偏离 %d / 无数据 %d"
             % (ev.get("n_pass", 0), ev.get("n_fail", 0), ev.get("n_nodata", 0)))
    L.append("")

    if review:
        L.append("三、情景判定与概率修正")
        L.append("  当前最像：%s" % (review.get("scenario_now") or "—"))
        if review.get("progress"):
            L.append("  兑现进度：%s" % review["progress"])
        probs = review.get("probs")
        if isinstance(probs, dict) and probs:
            base = {s.get("name"): s.get("prob") for s in scs}
            seg = []
            for k, v in probs.items():
                old = base.get(k)
                seg.append("%s %s%% → %s%%"
                           % (k, old if old is not None else "?", v))
            L.append("  概率：" + "　".join(seg))
        L.append("")

        L.append("四、已被证伪的假设")
        inv = review.get("invalidated") or []
        for x in inv:
            L.append("  · %s" % x)
        if not inv:
            L.append("  （无）")
        L.append("")

        L.append("五、接下来盯")
        nw = review.get("next_watch") or []
        for x in nw:
            L.append("  · %s" % x)
        if not nw:
            L.append("  （无）")
        L.append("")

        L.append("六、这次修正错在哪")
        L.append("  %s" % (review.get("falsifier") or "（LLM 未给出）"))
    else:
        L.append("三、LLM 修正")
        L.append("  不可用（未配置 / 调用失败）：以上仅为确定性对照。")
    L.append("")
    L.append("生成时间：%s" % _now())
    L.append("口径：只对照已登记指标，无数据项标「—」不推测。纯复盘，不进决策链。")
    return "\n".join(L)


def _markdown(thesis: Dict, day: str, ev: Dict,
              review: Optional[Dict]) -> str:
    lines = ["**%s**（%s ~ %s）"
             % (thesis.get("title"), thesis.get("horizon_start"),
                thesis.get("horizon_end")), ""]
    for x in ev.get("checked") or []:
        mark = {"pass": "✓", "fail": "✗", "nodata": "—"}[x["verdict"]]
        act = "无数据" if x["actual"] is None else x["actual"]
        lines.append("%s %s %s %s → 实际 %s"
                     % (mark, x["label"], x["op"], x["target"], act))
    lines.append("")
    lines.append("统计：命中 %d / 偏离 %d / 无数据 %d"
                 % (ev.get("n_pass", 0), ev.get("n_fail", 0), ev.get("n_nodata", 0)))
    # ★ 2026-11-25（L4 失效检测）：**只在触发时出现**，平时不占位（避免天天报警）。
    _inv = ev.get("invalidation") or {}
    if _inv.get("suggest_switch"):
        lines.append("")
        lines.append("⚠️ **尺子可能已不准**：%s" % _inv.get("reason"))
        lines.append("→ %s" % _inv.get("action"))
    if review:
        lines.append("")
        lines.append("**情景：%s**" % review.get("scenario_now", "—"))
        probs = review.get("probs") or {}
        if isinstance(probs, dict) and probs:
            lines.append("概率修正：" + " / ".join(
                "%s %s%%" % (k, v) for k, v in probs.items()))
        if review.get("progress"):
            lines.append("兑现进度：" + str(review["progress"]))
        inv = review.get("invalidated") or []
        if inv:
            lines.append("已证伪：" + "；".join(str(x) for x in inv))
        nw = review.get("next_watch") or []
        if nw:
            lines.append("明日观察：" + "；".join(str(x) for x in nw))
        if review.get("falsifier"):
            lines.append("⚠️ 这次修正的失效条件：" + str(review["falsifier"]))
    else:
        lines.append("")
        lines.append("（LLM 修正不可用：仅确定性对照）")
    lines.append("")
    lines.append("口径：只对照已登记指标，无数据项标「—」，不推测。纯复盘，不进决策链。")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  失效检测器（★ 2026-11-25，用户贴的五层架构 L4 里唯一真缺的一块）
# ══════════════════════════════════════════════════════════════════════════
# 【为什么需要它】
#   已有的「换尺子」是**人工动作**（`PUT /scenarios/{tid}/checks`），
#   而人不会天天主动问"我这把尺子还准吗" —— 缺的是**什么时候该问**的触发。
#   这正是用户复盘出来的那条教训："验证框架必须匹配行情性质，性质变了就要立刻换框架"，
#   但"立刻"靠人是做不到的，得有个东西在看。
#
# 【设计原则（三条都不能破）】
#   ① **只报警，绝不自动改 checks / 自动归档** —— 换不换尺子取决于
#      "这次不一样在哪"（磨底→派发→修复的元判断），规则写不出来。
#      自动换等于用死规则替代判断，比不换更糟。
#   ② **nodata 不算 fail** —— 数据缺失是"不知道"，不是"被证伪"。
#      否则部署当天指标没落库就会误报"推演失效"。
#   ③ **判据要能说清理由** —— 输出必须带"哪几天、哪几项、多少比例"，
#      让人能一眼判断是"尺子错了"还是"行情真的这么走"。
#
# 【阈值依据】
#   连续 2 天失败率 ≥40% + 累计 fail ≥2 项。
#   为什么不是 1 天：单日可能被一个异常读数带偏（如 11-24 收盘 3548 差 32 点）；
#   为什么不是 3 天：等到第 3 天，坏尺子已经污染了两天的记录。
#   ⚠️ 这两个数是**经验初值**（和项目里其他阈值一样），要校准得靠历史推演积累。
_INVALID_DAYS = int(os.environ.get("THESIS_INVALID_DAYS") or 2)
_INVALID_FAIL_RATIO = float(os.environ.get("THESIS_INVALID_FAIL_RATIO") or 0.4)
_INVALID_MIN_FAIL = int(os.environ.get("THESIS_INVALID_MIN_FAIL") or 2)


def invalidation_check(tid: str, lookback: int = 5) -> Dict:
    """检查某份推演的**尺子是否已经不准**（连续多日高失败率）。

    返回 `{ok, thesis_id, days[], suggest_switch, reason, action}`。
    ⚠️ **只读 + 只建议**：不改 checks、不改概率、不归档。
    """
    ensure_tables()
    t = get_thesis(tid)
    if not t:
        return {"ok": False, "error": "推演不存在"}

    try:
        rows = db.fetch(
            f"SELECT date, checked_json FROM {_T_DAILY} "
            f"WHERE thesis_id=%s ORDER BY date DESC LIMIT %s",
            (tid, max(1, min(int(lookback or 5), 30)))) or []
    except Exception as e:
        print(f"[thesis] invalidation query failed: {str(e)[:80]}")   # ASCII
        return {"ok": False, "error": f"读取对照失败: {str(e)[:50]}"}

    if not rows:
        return {"ok": True, "thesis_id": tid, "days": [],
                "suggest_switch": False,
                "reason": "还没有任何每日对照记录（日批未跑或推演刚登记）",
                "action": None}

    days = []
    for r in reversed(rows):                    # 旧 → 新，便于看趋势
        chk = _loads(r.get("checked_json"))
        n_pass = sum(1 for x in chk if x.get("verdict") == "pass")
        n_fail = sum(1 for x in chk if x.get("verdict") == "fail")
        n_nd = sum(1 for x in chk if x.get("verdict") == "nodata")
        denom = n_pass + n_fail                 # ⚠️ nodata 不进分母（原则②）
        rate = round(n_fail / denom, 3) if denom else None
        days.append({
            "date": r.get("date"),
            "n_pass": n_pass, "n_fail": n_fail, "n_nodata": n_nd,
            "fail_ratio": rate,
            "bad": bool(rate is not None and rate >= _INVALID_FAIL_RATIO),
        })

    # 从最新往回数：连续"高失败率"的天数
    consec = 0
    for d in reversed(days):
        if d["bad"]:
            consec += 1
        else:
            break
    total_fail = sum(d["n_fail"] for d in days[-_INVALID_DAYS:] if d["bad"])

    suggest = bool(consec >= _INVALID_DAYS and total_fail >= _INVALID_MIN_FAIL)

    if suggest:
        seg = "、".join("%s(%d失败/%d项)" % (d["date"], d["n_fail"],
                                            d["n_pass"] + d["n_fail"])
                        for d in days[-consec:])
        reason = (f"连续 {consec} 天失败率 ≥ {_INVALID_FAIL_RATIO:.0%}：{seg}"
                  f"（阈值：连续 {_INVALID_DAYS} 天且累计失败 ≥{_INVALID_MIN_FAIL} 项）")
        action = ("**人工复核**：行情性质是否已变（如磨底→派发、修复→破位）。"
                  "若是，用 `PUT /api/thesis/scenarios/{id}/checks` **换尺子但保留 id**"
                  "（不要重建推演，重建会换 id、`thesis_daily` 历史对照就断了）。"
                  "若只是单日异常读数，可继续观察一天。")
    elif consec > 0:
        reason = (f"最近 {consec} 天失败率偏高，但未达触发线"
                  f"（需连续 {_INVALID_DAYS} 天）")
        action = None
    else:
        # ⚠️ 必须区分"正常"与"无数据" —— 全 nodata 时说"命中率正常"
        #    等于把"不知道"说成"没问题"（项目铁律：缺失 ≠ 满足）。
        _has_valid = any(d["fail_ratio"] is not None for d in days)
        reason = ("最近一日命中率正常，尺子仍有效" if _has_valid
                  else "最近几日全部无数据，**无法判断**（缺失 ≠ 证伪，等数据落库再看）")
        action = None

    return {
        "ok": True, "thesis_id": tid, "title": t.get("title"),
        "days": days,
        "consecutive_bad": consec,
        "suggest_switch": suggest,
        "reason": reason,
        "action": action,
        "thresholds": {
            "days": _INVALID_DAYS,
            "fail_ratio": _INVALID_FAIL_RATIO,
            "min_fail": _INVALID_MIN_FAIL,
        },
        "note": ("观察项：只提示'该复核尺子了'，**不自动改任何东西** —— "
                 "换不换尺子取决于'这次不一样在哪'，那是人的判断。"),
    }


def push(thesis: Dict, day: str, ev: Dict, review: Optional[Dict]) -> bool:
    if not PUSH_ENABLED:
        return False
    try:
        from app.flash import wechat
        return bool(wechat.notify("brief", f"推演复盘 {day}",
                                  _markdown(thesis, day, ev, review)))
    except Exception as e:
        print(f"[thesis] push failed: {str(e)[:90]}")
        return False


# ══════════════════════════════════════════════════════════════════════════
#  日批入口
# ══════════════════════════════════════════════════════════════════════════
def run_daily(day: str = None) -> Dict:
    """取有效推演 → 确定性对照 → LLM 修正 → 落库 → 推送。异常不外抛。"""
    try:
        ensure_tables()
        d = str(day or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
        theses = list_theses(active_only=True, day=d)
        if not theses:
            return {"ok": True, "date": d, "skipped": "无有效推演", "items": []}

        items = []
        for t in theses:
            ev = evaluate_day(t, d)
            # ★ 2026-11-25：失效检测（L4）。**先算、后落库** —— 让当天的对照
            #   也计入"连续失败"的判定（否则永远慢一天）。
            try:
                ev["invalidation"] = invalidation_check(t["id"])
            except Exception as e:
                print(f"[thesis] invalidation failed {t.get('id')}: {str(e)[:70]}")
                ev["invalidation"] = {"ok": False, "error": str(e)[:60]}
            rv = llm_review(t, d, ev)
            ok_db = save_daily(t["id"], d, ev, rv, t)
            pushed = push(t, d, ev, rv) if ok_db else False
            if pushed:
                try:
                    db.execute(f"UPDATE {_T_DAILY} SET pushed=1 "
                               f"WHERE thesis_id=%s AND date=%s", (t["id"], d))
                except Exception as e:
                    print(f"[thesis] mark pushed failed: {str(e)[:70]}")
            _inv = ev.get("invalidation") or {}
            items.append({
                "id": t["id"], "title": t.get("title"),
                "n_pass": ev["n_pass"], "n_fail": ev["n_fail"],
                "n_nodata": ev["n_nodata"],
                "scenario_now": (rv or {}).get("scenario_now"),
                "llm": bool(rv), "pushed": pushed,
                "suggest_switch_rule": bool(_inv.get("suggest_switch")),
                "invalidation_reason": _inv.get("reason"),
            })
        return {"ok": True, "date": d, "items": items}
    except Exception as e:
        print(f"[thesis] run_daily failed: {str(e)[:100]}")
        return {"ok": False, "error": str(e)[:120]}


def daily_rows(tid: str, limit: int = 30) -> List[Dict]:
    """某份推演的逐日对照历史（前端时间线用）。"""
    ensure_tables()
    try:
        rows = db.fetch(f"SELECT * FROM {_T_DAILY} WHERE thesis_id=%s "
                        f"ORDER BY date DESC LIMIT %s", (tid, max(1, min(limit, 200)))) or []
    except Exception as e:
        print(f"[thesis] daily_rows failed: {str(e)[:80]}")
        return []
    out = []
    for r in rows:
        out.append({
            "date": r.get("date"),
            "checked": _loads(r.get("checked_json")),
            "scenario_now": r.get("scenario_now"),
            "llm": json.loads(r["llm_json"]) if r.get("llm_json") else None,
            "pushed": bool(r.get("pushed")),
            # ★ 完整推演文本（企微只发精简版；这里给页面查看/复制）
            "report_text": r.get("report_text") or "",
        })
    return out
