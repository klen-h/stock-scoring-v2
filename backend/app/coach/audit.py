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
import time
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
_columns_ready = False


def ensure_tables() -> None:
    """幂等建表（新部署走 schema.sql，老库自动补）。"""
    global _table_ready, _columns_ready
    if _table_ready and _columns_ready:
        return
    if not _table_ready:
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
        except Exception as e:
            print(f"[coach] 表存在性探测失败（回落 DDL 确认）: {e}")
        if not _table_ready:
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
                    status TEXT DEFAULT 'open',
                    actual_exit_date TEXT,
                    actual_exit_reason TEXT,
                    abandon_reason TEXT,
                    followed INTEGER,
                    closed_at TEXT,
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
                    -- ★ 2026-11-13：市场级规则的「同期基准涨跌」（沪深300 T+5，%）。
                    --   为什么需要：`gate_no_add` / `gate_reduce` 这类建议**没有个股代码**，
                    --   无法用"个股 5 日涨跌"评价，而它们是占比最高的建议
                    --   （实测 171 条中 116 条无 code，占 68%）⇒ 缺这列等于
                    --   最重要的那批纪律建议**从来没被检验过**。
                    outcome_bench_pct REAL,
                    outcome_bench_date TEXT,
                    -- ★ 2026-11-13：`gate_add_weak_direction` 专属口径 —— T+N 的
                    --   「权重 − 创业板」收益差（pp，正=权重跑赢）。
                    --   该规则说"别加在成长上"，用单一基准涨跌评价是答非所问；
                    --   spread < 0 才说明"成长确实跑输权重"、建议被印证。
                    -- ⚠️ 权重端**不是固定指数**：2026-11-13 ~ 11-13 为沪深300，
                    --   **2026-11-14 起改为上证50**（与 `style_spread.WEIGHT_IDX` 同步）。
                    --   两者均值差约 1pp（上证50 更抗跌）⇒ **跨切换点的 spread 不可直接比**。
                    outcome_spread_pp REAL,
                    outcome_spread_date TEXT,
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
    if _table_ready and not _columns_ready:
        _ensure_columns()
        _columns_ready = True


def _ensure_columns() -> None:
    """老库自动补列（幂等）：模拟盘完整接入 B-1 新增字段。"""
    needed = {
        "status": "TEXT DEFAULT 'open'",
        "actual_exit_date": "TEXT",
        "actual_exit_reason": "TEXT",
        "abandon_reason": "TEXT",
        "followed": "INTEGER",
        "closed_at": "TEXT",
    }
    try:
        existing = {r["column_name"] for r in db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='coach_plans'")}
    except Exception as e:
        print(f"[coach] 列存在性探测失败: {e}")
        return
    for col, typ in needed.items():
        if col not in existing:
            try:
                db.execute(f"ALTER TABLE coach_plans ADD COLUMN {col} {typ}")
                print(f"[coach] 已补列 coach_plans.{col}")
            except Exception as e:
                print(f"[coach] 补列 coach_plans.{col} 失败: {e}")
    # ★ 2026-11-13：`coach_alerts` 此前**没有补列机制**（`_ensure_columns` 只管 coach_plans），
    #   而本次要给它加 `outcome_bench_pct` ⇒ 老库必须能自动补上。
    _ensure_alert_columns()


def _ensure_alert_columns() -> None:
    """老库自动给 `coach_alerts` 补列（幂等）。只补"新增且非空默认"的列。"""
    needed = {
        "outcome_bench_pct": "REAL",
        "outcome_bench_date": "TEXT",
        # ★ 2026-11-13：`gate_add_weak_direction` 的**专属**评价口径。
        #   该规则说的是"别加在成长上"，用基准涨跌评价会答非所问
        #   （基准跌不代表"成长跑输权重"，基准涨也不代表建议错了）。
        #   本列存 T+N 的「权重 − 创业板」收益差（pp，正=权重跑赢）：
        #     spread < 0 ⇒ 成长确实跑输权重 ⇒ 建议被印证。
        #   ⚠️ 权重端随 `_STYLE_WEIGHT_IDX` 走：2026-11-14 起由沪深300 **改为上证50**
        #     ⇒ 切换点前后的行**不是同一口径**，统计时要么分段、要么只用切换后的。
        "outcome_spread_pp": "REAL",
        "outcome_spread_date": "TEXT",
    }
    try:
        existing = {r["column_name"] for r in db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='coach_alerts'")}
    except Exception as e:
        print(f"[coach] coach_alerts 列探测失败: {e}")
        return
    for col, typ in needed.items():
        if col not in existing:
            try:
                db.execute(f"ALTER TABLE coach_alerts ADD COLUMN {col} {typ}")
                print(f"[coach] 已补列 coach_alerts.{col}")
            except Exception as e:
                print(f"[coach] 补列 coach_alerts.{col} 失败: {e}")


def close_plan(position_id: int, code: str, fill_date: str,
               actual_exit_date: str, actual_exit_reason: str) -> bool:
    """平仓时关闭对应 coach_plans 剧本，并判定是否按预承诺离场。

    按剧本离场的口径（与 write_plan 的 exit_conditions 对齐）：
      - stop_loss  → 命中"止损"条件
      - expire     → 命中"强制评估"条件
      其余（manual / take_profit 等）视为未严格按剧本，followed=0。
    """
    ensure_tables()
    try:
        plan = db.fetch_one(
            "SELECT id FROM coach_plans WHERE position_id=%s AND status='open' "
            "ORDER BY id DESC LIMIT 1", (position_id,))
        if not plan and code and fill_date:
            plan = db.fetch_one(
                "SELECT id FROM coach_plans WHERE code=%s AND plan_date=%s AND status='open' "
                "ORDER BY id DESC LIMIT 1", (code, fill_date))
        if not plan:
            return False
        followed = 1 if actual_exit_reason in ("stop_loss", "expire") else 0
        db.execute(
            "UPDATE coach_plans SET status='closed', actual_exit_date=%s, "
            "actual_exit_reason=%s, followed=%s, closed_at=%s WHERE id=%s",
            (actual_exit_date, actual_exit_reason, followed, _now(), plan["id"]))
        return True
    except Exception as e:
        print(f"[coach] 关闭剧本失败: {e}")
        return False


def plan_execution_rate(days: int = 30) -> dict:
    """预承诺执行率（模拟盘闭环核心 KPI）：按剧本离场的平仓数 / 已平仓剧本数。"""
    ensure_tables()
    try:
        rows = db.fetch(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed, "
            "SUM(CASE WHEN status='abandoned' THEN 1 ELSE 0 END) AS abandoned, "
            "SUM(CASE WHEN followed=1 THEN 1 ELSE 0 END) AS followed "
            "FROM coach_plans WHERE plan_date >= %s",
            (_days_ago(days),))
    except Exception as e:
        print(f"[coach] 预承诺执行率查询失败: {e}")
        return {"window_days": days, "error": str(e)}
    r = rows[0] if rows else {}
    total = int(r.get("total") or 0)
    closed = int(r.get("closed") or 0)
    abandoned = int(r.get("abandoned") or 0)
    settled = closed + abandoned
    followed = int(r.get("followed") or 0)
    return {
        "window_days": days,
        "plans_total": total,
        "plans_closed": closed,
        "plans_abandoned": abandoned,
        "plans_settled": settled,
        "plans_open": total - settled,
        "followed_count": followed,
        "follow_rate_pct": round(followed / settled * 100, 1) if settled else None,
        "note": "预承诺执行率 = 按剧本离场的平仓数 / 已结算剧本数（含已平仓与主动放弃）",
    }


def get_plans(position_ids: list = None, code: str = None,
              plan_date: str = None, status: str = None) -> list:
    """按 position_id 列表 / code / plan_date / status 查询剧本（A 前端展示用）。"""
    ensure_tables()
    where, params = [], []
    if position_ids:
        ph = ",".join(["%s"] * len(position_ids))
        where.append(f"position_id IN ({ph})")
        params.extend(position_ids)
    if code:
        where.append("code=%s")
        params.append(code)
    if plan_date:
        where.append("plan_date=%s")
        params.append(plan_date)
    if status:
        where.append("status=%s")
        params.append(status)
    if not where:
        return []
    sql = "SELECT * FROM coach_plans WHERE " + " AND ".join(where) + " ORDER BY id DESC"
    try:
        return db.fetch(sql, tuple(params)) or []
    except Exception as e:
        print(f"[coach] 查询剧本失败: {e}")
        return []


def abandon_plan(plan_id: int, reason: str) -> bool:
    """持仓中主动放弃剧本（A 执行回写）：reason 必填，写 abandon_reason。"""
    ensure_tables()
    reason = (reason or "").strip()
    if not reason:
        return False
    try:
        db.execute(
            "UPDATE coach_plans SET status='abandoned', actual_exit_reason='abandon', "
            "abandon_reason=%s, followed=0, closed_at=%s WHERE id=%s AND status='open'",
            (reason, _now(), plan_id))
        return True
    except Exception as e:
        print(f"[coach] 放弃剧本失败: {e}")
        return False


# ==============================================================================
#  建议落库
# ==============================================================================

def record_advices(advices: List[dict], push: bool = False,
                   day: Optional[str] = None) -> List[dict]:
    """把求值结果落库（按 dedupe_key 去重），返回**本次新增**的记录（供推送）。

    同日同规则同标的重复触发（30s 轮询的常态）不会产生新行，也就不会重复推送。
    ★ 2026-09-29：加可选 `day`（YYYY-MM-DD）—— **日批侧写入**用（`log_entry_candidates`）。
      不传 = 原行为（`_today()`，供盘中 30s 轮询）。传了则**同时用于 dedupe_key**，
      保证"同一交易日重跑/跨午夜补跑"仍幂等（与 `daily_batch._batch_trading_day`
      的跨午夜纪律同源）。
    """
    ensure_tables()
    now = _now()
    today = (str(day).strip()[:10] if day else "") or _today()
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
        _alert_list_invalidate()      # 列表缓存立即失效（新建议秒现）
    return fresh


_ENTRY_RULE_ID = "gate_entry"
_ENTRY_LABEL = "买入入口（闸门三条件就绪）"


def log_entry_candidates(day: Optional[str] = None) -> str:
    """把当日**买入入口**（闸门 `ready==3` 清单）落库为 `coach_alerts` 记录（**不推送**）。

    【为什么（2026-09-29 实测驱动，P1/P2 的共同根因）】
      ① P2 归因首跑实测：`coach_alerts` 里**只有风控/持仓类规则带 `code`**
         （loss_over_7pct/stop_loss_hit/hold_3d_review = 50 行），而 `gate_add` 等
         **买入/市场类建议无 `code`** ⇒ **「入场理由」的个股收益永远无法评估**。
      ② 唯一买入入口 `trader_brief._merged_buy_list`（`ready==3`）此前**只实时展示、
         不留痕** ⇒ 一旦 regime 转好、`ready==3` 开始出现，**"买没买"将永久丢失**
         ⇒ **时钟属性**：留痕越早越好（同 P1「先落库、后验证」的纪律）。

    【为什么复用 `coach_alerts` 而不新建表】
      ① 现成的 `executed`/`abandon_reason` **回写通道**可直接用 ⇒ 教练页「建议历史」
         **零新增前端**即可回写（"执行错"维度立即可测）；
      ② 自动进入 P2 归因（`by_rule` / `by_executed`）与 `backfill_outcome` 的 T+5 回填；
      ③ `dedupe_key = 交易日|规则|标的` ⇒ 日批重跑天然幂等（`day` 显式传入，
         跨午夜补跑也不会写成次日）。

    【纪律】`push=False` —— **只落库、不推送**（《专业度缺口》P1：达标后才谈是否推送）。
      空清单**不写任何行**（诚实为空，不造占位记录）。
    """
    try:
        from app.strategies.recommendation import get_push_whitelist
        from app.trader_brief import _merged_buy_list
        try:
            wl = get_push_whitelist() or []
        except Exception:
            wl = []                      # 白名单读失败不阻断（买入入口与战法无关，战法仅附注）
        items = _merged_buy_list(wl) or []
        if not items:
            return "买入入口留痕: 0 只（闸门 ready==3 为空 ⇒ 不写占位行）"
        advices = []
        for x in items:
            pct = x.get("position_pct")
            flow = x.get("flow5_amt")
            hits = x.get("strategies") or []
            msg = ("买入入口就绪：闸门三条件全满足（A 主力吸筹 + B 不追高 + C 市况允许）"
                   f" · 档位 {x.get('position_label') or '—'}"
                   f"(建议 {pct if pct is not None else '—'}%)"
                   f" · 主力阶段 {x.get('phase_cn') or '—'}"
                   + (f" · 5日主力净额 {float(flow):+.1f}%" if flow is not None else "")
                   + (" · 战法附注 " + "、".join(str(h) for h in hits) if hits else "")
                   + f" · 闸门快照 {x.get('snapshot_date') or '—'}")
            advices.append({
                "rule_id": _ENTRY_RULE_ID, "label": _ENTRY_LABEL, "severity": "info",
                "code": x.get("code"), "name": x.get("name"), "message": msg,
                "numbers": {"gate_ready": x.get("gate_ready"),
                            "position_pct": pct, "flow5_amt": flow,
                            "snapshot_date": x.get("snapshot_date"),
                            "strategy_hits": hits},
            })
        fresh = record_advices(advices, push=False, day=day)
        return (f"买入入口留痕: 闸门 ready==3 共 {len(items)} 只，"
                f"本次新增 {len(fresh)}（同日重跑其余自动去重）")
    except Exception as e:
        print(f"[coach] 买入入口留痕失败: {str(e)[:80]}")          # ASCII（铁律⑥）
        return f"买入入口留痕: 失败（{str(e)[:60]}）"


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
        _alert_list_invalidate()      # 执行状态变了 → 列表缓存立即失效
        return True
    except Exception as e:
        print(f"[coach] 执行回写失败 {alert_id}: {e}")
        return False


# ==============================================================================
#  对照审计
# ==============================================================================

def execution_consistency(days: int = 30, day: Optional[str] = None) -> dict:
    """
    **执行一致性度量（第一版口径，评审 ④：先定标尺）**

    定义（务必先看清分母再谈"提升"）：
      - 统计范围：`day` 指定时 = **该日**（`alert_date = day`）；未指定 = 近 `days` 天
        （`alert_date >= 今日-days`）；两种都要求**已推送**（pushed=1）。
      - **已决策** = executed ∈ {yes,no}（用户做了明确选择）。
      - **未响应** = executed IS NULL（用户没理会）→ **单列，不进分母**：
        "没看见"与"看见了但放弃"是两回事，混算会虚高执行率。
      - 执行率 = yes / 已决策数（**分母是已决策，不是全部推送**）。
      - 放弃率 = no / 已决策数；放弃理由一并回传，供周报复盘。

    为什么不用"建议胜率"当 KPI：教练的价值是**劝住冲动**，不是选股。
    简报 §3.2 的原话——"教练 KPI 是本周劝住几次操作"。

    ★ 2026-09-25 新增 `day`（供盘后「今日执行回看」）：**"今天"必须按日精确取** ——
      不能拿 `days=1` 顶替（那是"近 1 天"，条件是 `alert_date >= 昨天`，会把昨天算进"今天"）。
    """
    ensure_tables()
    if day:
        sql = ("SELECT executed, COUNT(*) AS c FROM coach_alerts "
               "WHERE pushed=1 AND alert_date = %s GROUP BY executed")
        params = (str(day)[:10],)
    else:
        sql = ("SELECT executed, COUNT(*) AS c FROM coach_alerts "
               "WHERE pushed=1 AND alert_date >= %s GROUP BY executed")
        params = (_days_ago(days),)
    try:
        rows = db.fetch(sql, params)
    except Exception as e:
        print(f"[coach] 执行一致性查询失败: {e}")
        return {"window_days": days, "day": day, "error": str(e)}
    counts = {str(r["executed"]): int(r["c"] or 0) for r in (rows or [])}
    yes, no = counts.get("yes", 0), counts.get("no", 0)
    ignored = counts.get("None", 0)
    decided = yes + no
    return {
        "day": day,                                     # 指定日模式（None = 窗口模式）
        "window_days": None if day else days,
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


# ★ 2026-09-25（egress 治理，探针实测驱动）：列表页要的列 + 截断 `message`。
#   原 `SELECT *` 会把 `numbers_json`（规则触发的数值明细）与完整 `message` 一起过网；
#   实测该语句 46 分钟 **40 次 / 2,840 行 ≈1.28MB**（页面开一天 ≈13MB）。
#   ⚠️ `numbers_json` **前端零引用**（已核）⇒ 列表不取它（要看明细走详情/回放接口）。
_ALERT_LIST_COLS = ("id", "alert_date", "alert_time", "rule_id", "label", "severity",
                    "code", "name", "executed", "abandon_reason", "outcome_pct",
                    "outcome_date",
                    # ★ 2026-09-25：补 `pushed` —— 盘后「今日执行」卡要按"今日**已推送**的"
                    #   过滤清单（与执行率口径 pushed=1 对齐）；缺它时前端只能把
                    #   "落库但未推送"的建议也列进来（会与上方数字对不上）。
                    #   一个整数字段，体积可忽略；其它调用方忽略即可。
                    "pushed")


# ★ 2026-10-19（egress 实测）：教练列表是**前端轮询**的（工作台 `loadCoach` 取 100 条、
#   教练页取 50 条），32h 探针实测 1,460 次调用 / 4.5 万行 ≈ 20MB（按整行宽算的上限）。
#   教练卡是日频数据（规则引擎按天产出，盘中偶尔新增）⇒ 加 5 分钟进程缓存；
#   写入侧 `record_advices` / 执行回写 / 结果回填都会**立即失效**，新建议照常秒现。
_ALERT_LIST_CACHE = {"ts": 0.0, "key": None, "val": None}
_ALERT_LIST_TTL = 300.0


def _alert_list_invalidate() -> None:
    _ALERT_LIST_CACHE.update(ts=0.0, key=None, val=None)


def recent_alerts(limit: int = 50, trunc: Optional[int] = 300) -> List[dict]:
    """最近 N 条教练卡（**列表口径**：不含 `numbers_json`，`message` 截断到 `trunc` 字符）。

    `trunc=None` ⇒ 退回原 `SELECT *` 全字段（需要完整正文的调用方用）。
    ★ 5 分钟进程缓存（写入侧主动失效）—— 见 `_ALERT_LIST_CACHE` 上方说明。
    """
    ensure_tables()
    key = (int(limit), None if trunc is None else int(trunc))
    now = time.time()
    cached = _ALERT_LIST_CACHE["val"]
    if (cached is not None and _ALERT_LIST_CACHE["key"] == key
            and now - _ALERT_LIST_CACHE["ts"] < _ALERT_LIST_TTL):
        return [dict(r) for r in cached]      # 深拷贝：调用方可能原地改写
    try:
        if trunc is None:
            rows = db.fetch("SELECT * FROM coach_alerts ORDER BY id DESC LIMIT %s",
                            (limit,))
        else:
            cols = ", ".join(_ALERT_LIST_COLS)
            rows = db.fetch(
                f"SELECT {cols}, SUBSTR(message, 1, %s) AS message "
                f"FROM coach_alerts ORDER BY id DESC LIMIT %s",
                (int(trunc), int(limit)))
        out = [dict(r) for r in (rows or [])]
        _ALERT_LIST_CACHE.update(ts=now, key=key, val=out)
        return [dict(r) for r in out]
    except Exception as e:
        print(f"[coach] 建议查询失败: {e}")
        return []


# ★★ 2026-11-13：市场级建议的评价映射。
#   【为什么必须有】实测 `coach_alerts` 171 条里 **116 条没有 `code`**（68%），
#   它们是 `gate_no_add(39) / gate_reduce(21) / no_chase_rally(15) /
#   external_lead_no_rebound(17) / gate_add(17)` 这类**市场/组合级纪律**，
#   天然没有个股 ⇒ 原来的 `backfill_outcome` 直接跳过 ⇒ **最重要的那批建议从未被检验**。
#   【口径】用 `sh000300` 的 T+5 涨跌作为"若当时听了建议、避开/参与这段"的**方向性**代理。
#   ⚠️ 这是**方向性检验，不是收益归因**：它只回答"事后方向是否印证了建议"，
#      不代表"听了就一定赚钱"（仓位、时点、个股选择都不在其中）。
#   ⚠️ 新增规则若不在此表 ⇒ `None` ⇒ 只落库不评价（不臆造口径）。
def _bench_code() -> str:
    """基准代码 —— 唯一的**事实源**是 `app.benchmarks.pick_bench()`。

    ⚠️ 2026-11-13（初版写错，已改）：一开始硬编码 `sh000300`，
       而 `coach/attribution.py` 走的是 `pick_bench()`（实测选出 **中证1000
       `sh000852`**）⇒ 同一份建议在两处用不同基准，命中率与超额会互相矛盾。
       **凡"基准/交易日/口径"这类全局约定，只能有一个事实源。**
    """
    try:
        from app import benchmarks
        return str(benchmarks.pick_bench()[0])
    except Exception as e:
        print(f"[coach] 基准选取失败，回落 sh000300: {str(e)[:60]}")
        return "sh000300"

_RULE_EXPECT = {
    # 「别买/别加/该减/该跑」⇒ 事后 **跌** 才算建议被印证
    "gate_no_add": "down",
    "gate_reduce": "down",
    "no_chase_rally": "down",
    "external_lead_no_rebound": "down",
    "distribution_cut": "down",
    "reversal_no_add": "down",
    "stop_loss_hit": "down",
    "loss_over_7pct": "down",
    "hold_3d_review": "down",
    # 「别割在底部 / 允许加仓」⇒ 事后 **涨** 才算被印证
    "reversal_no_panic": "up",
    "gate_add": "up",
    # ★ 2026-11-13：方向否决类。期望 `spread < 0`（成长跑输权重）才对，
    #   所以 `"down"` 语义成立——但**必须配合 `_SPREAD_RULES` 用分化列评价**，
    #   不能落到基准涨跌上（详见 `_spread_change` 与 `rule_stats` 的分支）。
    "gate_add_weak_direction": "down",
    # 行为约束类（闭嘴模式）：不适用方向评价
    "emotion_fuse": None,
}


def _bench_change(start_date: str, days_after: int):
    """基准指数自 `start_date` 起第 `days_after` 个交易日的涨跌幅(%)。

    返回 `(chg_pct, date_str)`；数据不足/未到期返回 `(None, None)`。
    """
    try:
        bars = db.fetch(
            "SELECT date, close FROM backtest_prices WHERE code=%s "
            "AND date >= %s ORDER BY date ASC LIMIT %s",
            (_bench_code(), start_date, days_after + 1))
    except Exception:
        return None, None
    if not bars or len(bars) < days_after + 1:
        return None, None
    base = float(bars[0]["close"] or 0)
    last = float(bars[days_after]["close"] or 0)
    if base <= 0:
        return None, None
    return round((last / base - 1) * 100, 2), str(bars[days_after]["date"])[:10]


# ★ 2026-11-13：用「权重 − 成长」分化评价的规则集合。
#   这些规则的语义是**方向**（"别往成长加"），不是"市场整体涨跌"，
#   用 `outcome_bench_pct`（单一基准）评价会答非所问。
_SPREAD_RULES = ("gate_add_weak_direction",)


def _spread_change(start_date: str, days_after: int):
    """自 `start_date` 起第 N 个交易日的「权重 − 成长」收益差（pp，正=权重跑赢）。

    返回 `(spread_pp, date_str)`；数据不足/未到期返回 `(None, None)`。

    ★ 为什么需要独立口径（2026-11-13）：
      `gate_add_weak_direction` 说的是"别把仓位加在成长上"。事后检验应当问
      "成长是不是真的跑输了权重"，而不是"基准指数是不是跌了"——
      基准跌 ≠ 成长跑输（可能一起跌且权重跌更多，那这条建议就是错的）。
    ⚠️ 指数代码复用 `coach.rules` 里的常量，**不在这里重新硬编码**：
      两处若各写一份，日后换代理指数必然漂移（与 `_bench_code` 同一教训）。
    """
    try:
        from app.coach.rules import _STYLE_GROWTH_IDX, _STYLE_WEIGHT_IDX
    except Exception:
        return None, None

    def _ret(code):
        bars = db.fetch(
            "SELECT date, close FROM backtest_prices WHERE code=%s "
            "AND date >= %s ORDER BY date ASC LIMIT %s",
            (code, start_date, days_after + 1))
        if not bars or len(bars) < days_after + 1:
            return None, None
        base = float(bars[0]["close"] or 0)
        last = float(bars[days_after]["close"] or 0)
        if base <= 0 or last <= 0:
            return None, None
        return (last / base - 1) * 100, str(bars[days_after]["date"])[:10]

    try:
        w, wd = _ret(_STYLE_WEIGHT_IDX)
        g, gd = _ret(_STYLE_GROWTH_IDX)
        if w is None or g is None:
            return None, None
        # ⚠️ 两侧到期日必须一致，否则比的是不同长度的区间
        if wd != gd:
            return None, None
        return round(w - g, 2), wd
    except Exception as e:
        print(f"[coach] spread_change failed: {str(e)[:70]}")      # ASCII（铁律⑥）
        return None, None


def backfill_outcome(days_after: int = 5) -> int:
    """T+N 结果回填（两类）。

    ① **个股级**（`code` 非空）：用该股 T+N 涨跌填 `outcome_pct`（原有逻辑）。
    ② **市场级**（`code` 为空且规则在 `_RULE_EXPECT` 里）：用 `sh000300` 的
       T+N 涨跌填 `outcome_bench_pct`（2026-11-13 新增，见 `_RULE_EXPECT` 注释）。

    未到期（数据不足 N+1 根）跳过，下轮再补。
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
    # ② 市场级建议：无 code，但规则有明确期望方向 ⇒ 用基准指数给方向性评价
    filled_bench = 0
    try:
        rows2 = db.fetch(
            "SELECT id, rule_id, alert_date FROM coach_alerts "
            "WHERE outcome_bench_pct IS NULL AND COALESCE(code,'') = '' "
            "ORDER BY id DESC LIMIT 300")
    except Exception:
        rows2 = []
    for r in rows2 or []:
        rid = str(r["rule_id"])
        # ★ 2026-11-13：方向类规则走 ③ 的专属口径，不能用基准涨跌评价。
        #   ⚠️ 这里必须**排除**：否则 ③ 永远补不上（`outcome_bench_pct` 一旦填了，
        #      该行就不再出现在 ③ 的 `outcome_spread_pp IS NULL` 查询里）。
        if rid in _SPREAD_RULES:
            continue
        # 未在 `_RULE_EXPECT` 里定义的规则 ⇒ 不评价（不臆造口径）
        if _RULE_EXPECT.get(rid) is None:
            continue
        chg, d = _bench_change(r["alert_date"], days_after)
        if chg is None:
            continue          # 未到期，下轮再补
        try:
            db.execute(
                "UPDATE coach_alerts SET outcome_bench_pct=%s, outcome_bench_date=%s "
                "WHERE id=%s", (chg, d, r["id"]))
            filled_bench += 1
        except Exception:
            continue
    # ③ 方向类规则：用「权重 − 成长」分化评价（见 `_spread_change`）
    filled_spread = 0
    # 规则名是模块常量，拼进 SQL 无注入风险；`ANY(%s)` 在 SQLite 测试库不兼容
    _sp_in = ",".join("'%s'" % x for x in _SPREAD_RULES)
    try:
        rows3 = db.fetch(
            "SELECT id, rule_id, alert_date FROM coach_alerts "
            f"WHERE outcome_spread_pp IS NULL AND rule_id IN ({_sp_in}) "
            "ORDER BY id DESC LIMIT 300") or []
    except Exception:
        rows3 = []
    for r in rows3:
        sp, d = _spread_change(r["alert_date"], days_after)
        if sp is None:
            continue          # 未到期，下轮再补
        try:
            db.execute(
                "UPDATE coach_alerts SET outcome_spread_pp=%s, outcome_spread_date=%s "
                "WHERE id=%s", (sp, d, r["id"]))
            filled_spread += 1
        except Exception:
            continue
    if filled or filled_bench or filled_spread:
        print(f"[coach] 结果回填 {filled} 条个股 / {filled_bench} 条基准 / "
              f"{filled_spread} 条分化（T+{days_after}）")
    return filled + filled_bench + filled_spread


def rule_stats(days: int = 90) -> list:
    """按规则聚合「触发次数 / 可评价数 / 命中数 / 命中率」。

    ★ 2026-11-13：这是"教练到底准不准"的唯一出口。此前只有原始建议和
      零星回填值，**没有任何聚合** ⇒ 所有规则阈值都是拍的，无法校准。

    命中判定（**方向性检验，不是收益归因**）：
      · 个股级（有 `outcome_pct`）：`outcome_pct` 符号是否符合 `_RULE_EXPECT`
      · 市场级（有 `outcome_bench_pct`）：基准涨跌符号是否符合 `_RULE_EXPECT`
      · `_RULE_EXPECT` 为 `None` 的规则（如 `emotion_fuse`）⇒ `evaluable=0`，不参与命中率

    ⚠️ 命中率高 ≠ 一定赚钱：它只说明"事后方向印证了建议"，
      仓位、时点、个股选择都不在其中。
    """
    start = _days_ago(int(days))
    try:
        rows = db.fetch(
            "SELECT rule_id, code, outcome_pct, outcome_bench_pct, outcome_spread_pp "
            "FROM coach_alerts WHERE alert_date >= %s", (start,)) or []
    except Exception as e:
        print(f"[coach] 规则统计查询失败: {e}")
        return []

    agg = {}
    for r in rows:
        rid = str(r.get("rule_id") or "")
        if not rid:
            continue
        a = agg.setdefault(rid, {"total": 0, "evaluable": 0, "hit": 0,
                                 "sum": 0.0, "basis": ""})
        a["total"] += 1
        expect = _RULE_EXPECT.get(rid)
        if expect is None:
            continue
        # 个股优先 → 方向类用分化 → 其余用基准
        # ★ 2026-11-13：方向类规则必须走 `outcome_spread_pp`。
        #   用基准涨跌会答非所问（"基准跌" ≠ "成长跑输权重"）。
        if r.get("outcome_pct") is not None:
            val, basis = float(r["outcome_pct"]), "stock"
        elif rid in _SPREAD_RULES and r.get("outcome_spread_pp") is not None:
            val, basis = float(r["outcome_spread_pp"]), "spread"
        elif r.get("outcome_bench_pct") is not None:
            val, basis = float(r["outcome_bench_pct"]), "bench"
        else:
            continue                      # 未到期，不计入分母
        a["evaluable"] += 1
        a["basis"] = a["basis"] or basis
        a["sum"] += val
        # 方向判定：expect=down 期待事后跌（val<0 为命中）；up 反之
        if (expect == "down" and val < 0) or (expect == "up" and val > 0):
            a["hit"] += 1

    out = []
    for rid, a in agg.items():
        ev = a["evaluable"]
        out.append({
            "rule_id": rid,
            "total": a["total"],
            "evaluable": ev,
            "hit": a["hit"],
            "hit_rate": round(a["hit"] / ev * 100, 1) if ev else None,
            "avg_result": round(a["sum"] / ev, 2) if ev else None,
            "basis": a["basis"] or "-",
            "expect": _RULE_EXPECT.get(rid),
        })
    # 可评价的排前面，再按命中率、触发次数
    out.sort(key=lambda x: (x["evaluable"] == 0,
                            -(x["hit_rate"] if x["hit_rate"] is not None else -1),
                            -x["total"]))
    return out


def _days_ago(days: int) -> str:
    from datetime import timedelta
    # ★ 审查 P2-⑫：与 _now/_today 统一北京时间
    return (flash_rules.beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
