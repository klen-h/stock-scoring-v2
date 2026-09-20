"""
================================================================================
【文件作用】zzshare 每日数据积累（涨停生态 / 情绪套件快照，2026-09-21）
================================================================================
背景（`zzshare功能盘点与接入建议_20260920.md` §7 复测后的排期决策）：
  zzshare 的补充功能分两类 —— **分析开发型**（情绪×战法交互回测 / 基本面 PIT 体检，
  等 10 月体检收口后再做）与**数据积累型**（本模块）。
  数据积累有时钟属性：**晚一天接入就永远少一天历史**，且每日落库本身就是
  选型原则要求的「30 天影子验证」载体 ⇒ 先积累、后分析。

内容（每日一行 JSON，date 主键幂等）：
  sentiment_trend(model=1)      情绪分时序列 —— ② 情绪×战法交互回测的核心原料
                                ⚠️ model 是整数（README L432），字符串会 0 行
  market_style(date)            风格评分（regime 第二维候选）
  uplimit_hot(date)             连板分布 ban_info + 题材热度（战法归因/空间板识别）
  uplimit_stocks(date)          涨停清单（涨停回马枪/龙回头战法信号源）
  review_uplimit_reason(date)   全市场涨停题材归因分布

工程约束：
  · 数据源纪律：全部走 `zzshare_client.py` 单点封装（接口路径变更只改一处）；
  · **单接口 fail-open**：任一接口失败只记 `_error`，不阻断日批、不阻断其余接口；
  · 幂等：date 主键，重跑跳过（日批可能跨午夜补跑）；
  · 体量：每日 ~6 行 JSON、几十 KB，egress 可忽略；
  · token：ZZSHARE_TOKEN 必须在 **GitHub Actions secrets** 也配置（Render 已配），
    否则日批匿名调用会 0 行/慢 —— 探测脚本 `scripts/zzshare_probe.py` 月度复核。

消费方（未来）：
  · ② 情绪分位 × 战法胜率交互回测（`sentiment_trend_range` 可回填历史，
    每日增量从此表读）
  · 战法信号归因（涨停原因/题材持续性过滤）
  · regime 风格维候选（market_style）
================================================================================
"""

import json
from datetime import datetime

_TABLE_READY = False


def ensure_table() -> None:
    """创建 zz_daily_snapshots（幂等，兼容 PostgreSQL/SQLite）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    from app.database import db
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS zz_daily_snapshots (
                date DATE PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS zz_daily_snapshots (
                date TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
    _TABLE_READY = True


def _slim_uplimit_hot(h):
    """uplimit_hot 原始返回 ~890KB/日（实测），其中 `plate_stocks/_bx/_zb` 是
    187 个题材的成分股详情 ×3 份冗余（题材→个股映射 `review_uplimit_reason`
    已覆盖）、`stock_info`/`stocks_hot` 与 `uplimit_stocks` 接口重复
    ⇒ 只保留分析密度高的核心字段，890KB → ~5KB。"""
    if not isinstance(h, dict):
        return h
    return {k: h[k] for k in ("ban_info", "max_count", "plate", "stocks", "today")
            if k in h}


def _slim_uplimit_reason(rows):
    """review_uplimit_reason 原始 ~629KB/日：174 题材 × 每题材 20 只股票的全字段。
    归因价值在「哪只票属于哪个题材」⇒ stocks 每项只留 code/name，629KB → ~35KB。"""
    if not isinstance(rows, list):
        return rows
    out = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        slim = {k: r.get(k) for k in ("plate_code", "plate_name", "plate_score")}
        slim["stocks"] = [{"code": s.get("stock_code"), "name": s.get("stock_name")}
                          for s in (r.get("stocks") or []) if isinstance(s, dict)]
        out.append(slim)
    return out


def collect_daily(date_str: str) -> dict:
    """拉当日各接口快照（单接口 fail-open）。

    返回 {接口名: 原始返回 | {"_error": msg}}。参数格式均按
    `scripts/zzshare_probe.py` 复测通过的形式（date 带连字符）。
    ★ 体积纪律：全存原始返回实测 **1541KB/日**（Supabase 免费额度撑不了一个季度）
      ⇒ uplimit_hot / review_uplimit_reason 按上述 slim 规则裁剪（保分析价值、
      丢冗余），目标 <100KB/日。
    """
    from app.zzshare_client import get_api
    api = get_api()
    probes = (
        ("sentiment_trend", lambda: api.sentiment_trend(model=1, date1=date_str)),
        ("market_style", lambda: api.market_style(date1=date_str)),
        ("uplimit_hot", lambda: _slim_uplimit_hot(api.uplimit_hot(date1=date_str))),
        ("uplimit_stocks", lambda: api.uplimit_stocks(date1=date_str)),
        ("review_uplimit_reason",
         lambda: _slim_uplimit_reason(api.review_uplimit_reason(date1=date_str))),
    )
    out = {}
    for name, fn in probes:
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"_error": str(e)[:200]}
    return out


def run_daily(date_str: str) -> str:
    """每日快照入口（供 `scripts/daily_batch.py` 的 `task_zz_daily`）。

    幂等：该日期已有行则跳过（补跑安全）。返回状态文案（含接口成功数与
    payload 体积 —— 后者用于观察 review_uplimit_reason 是否膨胀）。
    """
    from app.database import db
    ensure_table()
    try:
        row = db.fetch_one(
            "SELECT date FROM zz_daily_snapshots WHERE date = %s", (date_str,))
    except Exception as e:
        return f"zz每日快照: 查询失败跳过（{str(e)[:80]}）—— 不写半截数据"
    if row:
        return f"zz每日快照: {date_str} 已存在，跳过"

    data = collect_daily(date_str)
    ok_n = sum(1 for v in data.values()
               if not (isinstance(v, dict) and "_error" in v))
    try:
        payload = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        return f"zz每日快照: {date_str} 序列化失败（{str(e)[:80]}）—— 不写库"
    try:
        db.execute(
            "INSERT INTO zz_daily_snapshots (date, payload, created_at) "
            "VALUES (%s, %s, %s)",
            (date_str, payload, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        return f"zz每日快照: {date_str} 落库失败（{str(e)[:80]}）"
    errs = [k for k, v in data.items()
            if isinstance(v, dict) and "_error" in v]
    note = f"，失败接口：{'、'.join(errs)}" if errs else ""
    return (f"zz每日快照: {date_str} 落库 {ok_n}/{len(data)} 接口，"
            f"payload {len(payload) // 1024}KB{note}")


def load_range(date_from: str, date_to: str) -> list:
    """按日期区间读取快照（未来回测/体检的消费入口，返 [{date, payload_dict}]）。"""
    from app.database import db
    ensure_table()
    rows = db.fetch(
        "SELECT date, payload FROM zz_daily_snapshots "
        "WHERE date >= %s AND date <= %s ORDER BY date", (date_from, date_to))
    out = []
    for r in rows or []:
        try:
            out.append({"date": str(r["date"])[:10],
                        **json.loads(r.get("payload") or "{}")})
        except (ValueError, TypeError):
            continue
    return out
