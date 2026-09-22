"""
================================================================================
【文件作用】买入闸门就绪度每日落库（2026-09-22）
================================================================================
背景（缺口驱动）：
  `trade_gate` 自 2026-09-17 上线以来**只有实时视图、没有任何历史** ⇒ 无法回答
  「就绪度到底有没有预测力」（ready 3/3 vs 2/3 vs 1/3 的后续收益差）。而它已经开始
  承担决策重量（观察池 + 用户操作动线）⇒ 必须攒样本验证，不能只靠"状态展示，
  非买入信号"的定位说明。
  ★ 时钟属性：**晚一天少一天样本**；且实测 3/3 常为 0（防御市 C 条件全灭）⇒
    样本积累天然慢，更要早开始。

设计（每日一行 JSON，`date` 主键幂等 —— 同 `zzshare_daily` 模式）：
  payload = {regime, counts{0..3}, evaluated,
             candidates:[{code, ready, flow5_amt, price_pos, winner_ratio, phase}]}
  · 候选只落 **ready≥2**（约 156 只/日、十几 KB）+ 全市场分布计数
    ⇒ 约 5MB/年，Supabase 压力可忽略；回测时解析 JSON 即可
  · ⚠️ `counts` 的键经 JSON 序列化会变成字符串（'0'~'3'），`load_range()` 读回时
    已归一为 int

数据来源：**复用 `app.routers.scoring.gate_watch`** —— 就绪度口径的唯一事实源，
  避免"两套实现导致口径漂移"（项目前车之鉴：本地 68.8 vs 后端 72.6）。

时序约束：必须在 `mainforce_state` **之后**执行（gate 依赖其 chip/flow5 快照）。
消费方（未来）：`scripts/gate_ready_backtest.py`（分档后续收益检验）。
================================================================================
"""

import json
from datetime import datetime

_TABLE_READY = False


def ensure_table() -> None:
    """创建 gate_snapshot_history（幂等，兼容 PostgreSQL/SQLite）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    from app.database import db
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS gate_snapshot_history (
                date DATE PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS gate_snapshot_history (
                date TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
    _TABLE_READY = True


def snapshot(date_str: str = None, overwrite: bool = False) -> str:
    """落库当日闸门快照（幂等；overwrite 时先删后插）。

    返回状态文案（含分布与候选数，便于日批日志观察）。
    """
    from app.database import db
    ensure_table()
    if not date_str:
        import app.flash.rules as _rules
        date_str = _rules.latest_completed_trading_day()
    try:
        row = db.fetch_one(
            "SELECT date FROM gate_snapshot_history WHERE date = %s", (date_str,))
    except Exception as e:
        return f"闸门快照: 查询失败跳过（{str(e)[:80]}）"
    if row and not overwrite:
        return f"闸门快照: {date_str} 已存在，跳过（force=true 可覆盖重跑）"

    try:
        # ★ 必须调 **_live**（2026-09-22）：端点主路径改成"读快照"后，若这里调端点，
        #   就会把**读到的旧快照**原样写进当天 ⇒ 快照永久停在旧数据、观察池永不更新。
        from app.routers.scoring import _gate_watch_live
        gw = _gate_watch_live()
    except Exception as e:
        return f"闸门快照: {date_str} 计算失败（{str(e)[:80]}）—— 不写库"

    # ★ 存**完整 item**（2026-09-22 配套改造）：端点改读快照后，快照必须自带展示所需
    #   字段（name/label/hint/missing/仓位/phase_cn/flow5_level）**以及 strategies
    #   （双信号命中）** —— 实测读快照时若再查 `strategy_results`（6 行大 JSON、无索引）
    #   要 **15.7s**，占满整个响应时间 ⚠️ ⇒ 随快照一起存，读取端零额外查询。
    #   战法扫描日随 payload 一起记（`strategy_date`），避免"闸门快照日 ≠ 战法日"错位。
    cands = list(gw.get("items") or [])
    payload = json.dumps({
        "regime": gw.get("regime"),
        "counts": gw.get("counts") or {},
        "evaluated": gw.get("evaluated"),
        # ★ 战法扫描日（随快照记）—— 读取端据此展示"战法数据日"，无需再查 strategy_results
        "strategy_date": gw.get("strategy_date"),
        "candidates": cands,
    }, ensure_ascii=False, default=str)
    try:
        if overwrite and row:
            db.execute("DELETE FROM gate_snapshot_history WHERE date = %s", (date_str,))
        db.execute(
            "INSERT INTO gate_snapshot_history (date, payload, created_at) "
            "VALUES (%s, %s, %s)",
            (date_str, payload, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        return f"闸门快照: {date_str} 落库失败（{str(e)[:80]}）"
    # ★ 写库即失效读取端缓存（跨模块清 `scoring._SNAP_CACHE`）—— 否则日批刚写完，
    #   页面还在用手里的旧快照（最长 60s），与"写入即失效"的既有惯例一致。
    try:
        from app.routers import scoring as _sc
        _sc._SNAP_CACHE.update(ts=0.0, val=None)
    except Exception:
        pass
    c = gw.get("counts") or {}
    return (f"闸门快照: {date_str} 落库 regime={gw.get('regime')} "
            f"ready 分布 {c.get(0)}/{c.get(1)}/{c.get(2)}/{c.get(3)}（0~3）"
            f"｜候选 {len(cands)} 只，payload {len(payload) // 1024}KB")


def load_range(date_from: str, date_to: str) -> list:
    """按日期区间读回快照（回测消费入口）。返回 [{date, regime, counts, candidates}]。"""
    from app.database import db
    ensure_table()
    rows = db.fetch(
        "SELECT date, payload FROM gate_snapshot_history "
        "WHERE date >= %s AND date <= %s ORDER BY date", (date_from, date_to))
    out = []
    for r in rows or []:
        try:
            d = json.loads(r.get("payload") or "{}")
        except (ValueError, TypeError):
            continue
        # counts 键归一为 int（JSON 序列化后是 '0'~'3'）
        d["counts"] = {int(k): v for k, v in (d.get("counts") or {}).items()
                       if str(k).isdigit()}
        d["date"] = str(r["date"])[:10]
        out.append(d)
    return out
