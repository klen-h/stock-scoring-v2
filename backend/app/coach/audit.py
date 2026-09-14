"""
================================================================================
【文件作用】Coach 落库与对照审计（W1）
================================================================================

两张表（对齐 PLAN §2.4 / 简报 §3.4）：
  coach_plans   入场剧本（预承诺退出计划：开仓瞬间就写死"什么时候走"）
  coach_alerts  教练建议 + 执行回写 + 放弃理由 + 5 日结果

★ 评审 ④「对照审计前先定义执行一致性度量」——本模块顶部给出第一版口径
  （见 `execution_consistency` 的 docstring），先定标尺再谈提升。

★ 幂等：同一天 + 同规则 + 同标的只落一条（30s 轮询会重复触发同一条件，
  靠 dedupe_key 去重；已有行不做覆盖，避免冲掉执行回写）。
================================================================================
"""

import json
from datetime import datetime
from typing import Dict, List, Optional

from app.database import db
from app.flash import rules as flash_rules


def _now() -> str:
    # ★ 审查 P2-⑫：统一北京时间（唯一时间源 beijing_now），此前本地时区在
    #   未设 TZ 的环境跨 8:00 会日期错位
    return flash_rules.beijing_now().isoformat()


def _today() -> str:
    return flash_rules.beijing_now().strftime("%Y-%m-%d")


_table_ready = False


def ensure_tables() -> None:
    """幂等建表（新部署走 schema.sql，老库自动补）。"""
    global _table_ready
    if _table_ready:
        return
    # ★ 2026-09-15：先探三对象是否已存在（廉价 catalog 查询，**零 DDL**）——全存在
    #   即直接返回。为什么必须这么做：`CREATE TABLE IF NOT EXISTS` 即使表已存在，
    #   也会触发 Supabase PostgREST **schema cache 全量重载**（见 `database.py:137`
    #   注释：每次 ~1s、重拉全部 43 个关系，是 egress 元凶）——本地实测 coach
    #   三连 DDL 冷启 3.5s，`GET /api/coach/alerts` 首请求因此超时；且 DDL 取表锁，
    #   遇并发写可能长时间等待。表/索引都在时，走探测分支即免掉全部 DDL。
    try:
        row = db.fetch_one(
            "SELECT to_regclass('public.coach_alerts') AS a, "
            "to_regclass('public.coach_plans') AS p, "
            "to_regclass('public.ux_coach_alerts_dedupe') AS i")
        if row and row.get("a") and row.get("p") and row.get("i"):
            _table_ready = True
            return
    except Exception as e:
        print(f"[coach] 表存在性探测失败（回落 DDL 确认）: {e}")
    _ok = True
    for sql in (
        """CREATE TABLE IF NOT EXISTS coach_plans (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL,
            name TEXT,
            position_id INTEGER,
            plan_date TEXT NOT NULL,
            entry_price REAL,
            stop_loss REAL,
            review_date TEXT,
            trail_trigger_pct REAL,
            exit_conditions TEXT,
            created_at TEXT,
            UNIQUE(code, plan_date)
        )""",
        """CREATE TABLE IF NOT EXISTS coach_alerts (
            id SERIAL PRIMARY KEY,
            dedupe_key TEXT,
            alert_date TEXT NOT NULL,
            alert_time TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            label TEXT,
            severity TEXT,
            code TEXT,
            name TEXT,
            message TEXT,
            numbers_json TEXT,
            pushed INTEGER DEFAULT 0,
            executed TEXT,
            abandon_reason TEXT,
            outcome_pct REAL,
            outcome_date TEXT,
            created_at TEXT
        )""",
        # ★ 审查 P2-⑩：dedupe_key 加唯一索引——此前「先 SELECT 后 INSERT」非原子，
        #   Render 部署重叠期两进程可同时 miss → 同警报双行、企微双推。
        #   历史重复行会让索引创建失败（非致命，打印后下轮重试）。
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_alerts_dedupe "
        "ON coach_alerts (dedupe_key)",
    ):
        try:
            db.execute(sql)
        except Exception as e:
            print(f"[coach] 建表: {e}")
            _ok = False
    if _ok:
        _table_ready = True   # ★ 审查 P2-⑨：仅全部成功才置位，失败下轮重试


# ==============================================================================
#  建议落库
# ==============================================================================

def record_advices(advices: List[dict], push: bool = False) -> List[dict]:
    """把求值结果落库（按 dedupe_key 去重），返回**本次新增**的记录（供推送）。

    同日同规则同标的重复触发（30s 轮询的常态）不会产生新行，也就不会重复推送。
    """
    ensure_tables()
    now = _now()
    today = _today()
    fresh = []
    for a in advices:
        key = f"{today}|{a['rule_id']}|{a.get('code') or '-'}"
        try:
            exist = db.fetch_one("SELECT id FROM coach_alerts WHERE dedupe_key=%s", (key,))
            if exist:
                continue
            db.execute(
                "INSERT INTO coach_alerts (dedupe_key, alert_date, alert_time, rule_id, label, "
                "severity, code, name, message, numbers_json, pushed, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (key, today, now, a["rule_id"], a["label"], a["severity"],
                 a.get("code") or "", a.get("name") or "", a["message"],
                 json.dumps(a.get("numbers") or {}, ensure_ascii=False),
                 1 if push else 0, now))
            fresh.append({**a, "dedupe_key": key})
        except Exception as e:
            # ★ 审查 P2-⑩：唯一索引冲突 = 另一进程（部署重叠期）已落库 → 静默跳过
            if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                continue
            print(f"[coach] 建议落库失败 {key}: {e}")
    if fresh:
        print(f"[coach] 新增建议 {len(fresh)} 条（推送={push}）")
    return fresh


def write_back_execution(alert_id: int, executed: str, reason: str = "") -> bool:
    """执行回写：executed ∈ {yes, no}；放弃时 reason 必填（治"再等等看"）。

    ★ 这是教练 KPI 的数据源：重点不是"建议对不对"，而是"用户有没有照做"。
    """
    if executed not in ("yes", "no"):
        return False
    if executed == "no" and not (reason or "").strip():
        return False          # 放弃必须填理由，否则回写无效
    try:
        db.execute("UPDATE coach_alerts SET executed=%s, abandon_reason=%s WHERE id=%s",
                   (executed, (reason or "").strip(), alert_id))
        return True
    except Exception as e:
        print(f"[coach] 执行回写失败 {alert_id}: {e}")
        return False


# ==============================================================================
#  对照审计
# ==============================================================================

def execution_consistency(days: int = 30) -> dict:
    """
    **执行一致性度量（第一版口径，评审 ④：先定标尺）**

    定义（务必先看清分母再谈"提升"）：
      - 统计范围：近 `days` 天内**已推送**（pushed=1）的建议。
      - **已决策** = executed ∈ {yes,no}（用户做了明确选择）。
      - **未响应** = executed IS NULL（用户没理会）→ **单列，不进分母**：
        "没看见"与"看见了但放弃"是两回事，混算会虚高执行率。
      - 执行率 = yes / 已决策数（**分母是已决策，不是全部推送**）。
      - 放弃率 = no / 已决策数；放弃理由一并回传，供周报复盘。

    为什么不用"建议胜率"当 KPI：教练的价值是**劝住冲动**，不是选股。
    简报 §3.2 的原话——"教练 KPI 是本周劝住几次操作"。
    """
    ensure_tables()
    try:
        rows = db.fetch(
            "SELECT executed, COUNT(*) AS c FROM coach_alerts "
            "WHERE pushed=1 AND alert_date >= %s GROUP BY executed",
            (_days_ago(days),))
    except Exception as e:
        print(f"[coach] 执行一致性查询失败: {e}")
        return {"window_days": days, "error": str(e)}
    counts = {str(r["executed"]): int(r["c"] or 0) for r in (rows or [])}
    yes, no = counts.get("yes", 0), counts.get("no", 0)
    ignored = counts.get("None", 0)
    decided = yes + no
    return {
        "window_days": days,
        "pushed_total": decided + ignored,
        "decided": decided,
        "ignored": ignored,
        "executed": yes,
        "abandoned": no,
        "exec_rate_pct": round(yes / decided * 100, 1) if decided else None,
        "abandon_rate_pct": round(no / decided * 100, 1) if decided else None,
        "note": "执行率分母=已决策（不含未响应）；未响应单列",
    }


def abandon_reasons(limit: int = 20) -> List[dict]:
    """放弃理由清单（周报复盘用：高频道理由 = 用户最易失守的纪律点）。"""
    ensure_tables()
    try:
        rows = db.fetch(
            "SELECT alert_date, label, code, name, abandon_reason FROM coach_alerts "
            "WHERE executed='no' AND COALESCE(abandon_reason,'') <> '' "
            "ORDER BY alert_date DESC, id DESC LIMIT %s", (limit,))
        return [dict(r) for r in (rows or [])]
    except Exception as e:
        print(f"[coach] 放弃理由查询失败: {e}")
        return []


def recent_alerts(limit: int = 50) -> List[dict]:
    ensure_tables()
    try:
        rows = db.fetch("SELECT * FROM coach_alerts ORDER BY id DESC LIMIT %s", (limit,))
        return [dict(r) for r in (rows or [])]
    except Exception as e:
        print(f"[coach] 建议查询失败: {e}")
        return []


def backfill_outcome(days_after: int = 5) -> int:
    """5 日结果回填（对照审计"规则 vs 规则+LLM"的基准；W1 只回填不评估）。

    用 `backtest_prices` 的收盘价：alert 日之后第 `days_after` 个交易日相对
    alert 当日收盘的涨跌幅。数据不足（未到期）则跳过，下轮再补。
    """
    ensure_tables()
    try:
        rows = db.fetch(
            "SELECT id, code, alert_date FROM coach_alerts "
            "WHERE outcome_pct IS NULL AND COALESCE(code,'') <> '' "
            "ORDER BY id DESC LIMIT 200")
    except Exception:
        return 0
    filled = 0
    for r in rows or []:
        try:
            bars = db.fetch("SELECT date, close FROM backtest_prices WHERE code=%s "
                            "AND date >= %s ORDER BY date ASC LIMIT %s",
                            (r["code"], r["alert_date"], days_after + 1))
            if not bars or len(bars) < days_after + 1:
                continue          # 未到期，下轮再补
            base = float(bars[0]["close"] or 0)
            last = float(bars[days_after]["close"] or 0)
            if base <= 0:
                continue
            db.execute("UPDATE coach_alerts SET outcome_pct=%s, outcome_date=%s WHERE id=%s",
                       (round((last / base - 1) * 100, 2), bars[days_after]["date"], r["id"]))
            filled += 1
        except Exception:
            continue
    if filled:
        print(f"[coach] 结果回填 {filled} 条（T+{days_after}）")
    return filled


def _days_ago(days: int) -> str:
    from datetime import timedelta
    # ★ 审查 P2-⑫：与 _now/_today 统一北京时间
    return (flash_rules.beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
