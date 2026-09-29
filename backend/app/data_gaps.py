"""
================================================================================
【文件作用】数据底座断档自检 + 板块快照自愈回填（2026-09-30，P0）
================================================================================
背景（为什么需要它）：
  2026-09-30 用户问「前几天的地产、今天的金属拉升，项目似乎察觉不到？」
  ⇒ 排查发现"察觉不到"的**数据层**两个真问题（另有口径层，见《诊断》记忆）：
    ① `plate_daily_zz`（zzshare 板块快照 —— **前端「板块分化」的数据源**）**停在 09-24**，
       而它原先只挂在 Render 的 `sector_snapshot_loop`（15:10-16:40）上 ⇒ 循环停摆
       即**静默断档 5 个交易日**；日批的 `task_sector_snapshot` 只写了东财版 ⇒ 无人发现。
       （同 `mainforce_state` 09-09 / `weekly_report` 09-11 / `zz_finance` 09-19 的老毛病：
         "Render 循环 ⇒ 数据静默停摆 ⇒ 迁入日批"，本次是 `sector_snapshot_zz` 漏搬。）
    ② **断档没有任何检查** —— 数据底座缺一天就永久少一天历史（时钟属性，回不来）。

提供三件能力（口径唯一：日批自愈 / CLI 回填 / 断档告警**共用同一组函数**）：
  1. `trading_days()` / `plate_missing()` —— 缺口探测（按**交易日**，不是自然日）
  2. `backfill_plate()` —— 回填（幂等；zzshare `plates_rank` **支持历史日期**，故能补）
  3. `run_gap_check()` —— 关键表断档自检 + 企微告警（同日去重），日批任务入口

★ 判定口径
  · **交易日**而非自然日：中秋(9-25~9-27)/国庆(10-01~10-07) 休市期用自然日会天天误报
    （自然日只在日批正常时点碰巧等于交易日 —— 见 `rules.latest_completed_trading_day` 注释）。
  · 容差 `TOLERATED_LAG_DAYS = 2` 个交易日：给"源当日尚未发布 / 日批重跑"留缓冲；
    超过才判断档（本次事故是落后 3 个交易日，会命中）。
  · `critical=False` 的表**只报不告警**：东财板块快照长期残缺（push2 风控）属已知常态，
    每次告警纯噪音（与 `app/health._NO_WECHAT_SOURCES` 豁免东财同一判断）。

★ 顺序纪律：`run_gap_check` **先自愈后告警** —— 补上了就不吵（否则每晚都推一条可自愈的噪音）。
★ 幂等/去重：告警按**本轮交易日**落 `data_gap_alert_log`（date 主键），日批补跑不重复推。
================================================================================
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ★ 容差 = 1 个交易日 ⇒ **落后 ≥2 个交易日才告警**。
#   【为什么是 1 而不是 2 —— 实测教训】首版设 2（落后 >2 才报），首次干跑就发现
#   **它恰好漏掉本次事故本身**：`plate_daily_zz` 09-24 → 09-29 之间夹着 **09-25 中秋休市**
#   ⇒ 只落后 **2** 个交易日（09-28、09-29），`2 > 2` 不成立 ⇒ 静默。
#   【为什么不更严（设 0，落后 ≥1 就报）】板块源当日可能尚未发布（本模块的自愈会在
#   **次日**补上）⇒ 设 0 会天天误报。设 1 的效果：源正常 = 落后 0~1（不报）；
#   源真断档 = 第 **2** 个交易日就报（本次事故会命中 ✓）；且不是"隔夜就吵"。
TOLERATED_LAG_DAYS = 1
PLATE_TABLE = "plate_daily_zz"
PLATE_KINDS = ("industry", "concept")
PLATE_BACKFILL_DAYS = 5         # 自愈窗口：每晚顺手补最近 N 个交易日的板块缺口

# 关键表清单：(表, 日期列, 中文名, 是否关键)
#   ⚠️ 列名已实测核实（2026-09-30）：`ranking_history` 用 `rank_date`，其余都是 `date`。
#   ⚠️ 只收「缺一天就永久少一天」的底座表；派生表（如 market_regime_history）不入。
CHECK_TABLES = (
    ("plate_daily_zz", "date", "板块快照 · zzshare（前端「板块分化」数据源）", True),
    ("industry_mainline", "date", "行业主线（mainline）", True),
    ("ranking_history", "rank_date", "评分快照 Top50（主线/绩效轨道的输入）", True),
    ("mainforce_state", "date", "主力行为状态（排行榜标签依赖）", True),
    ("zz_daily_snapshots", "date", "zzshare 日快照（情绪/连板/涨停归因）", True),
    ("gate_snapshot_history", "date", "买入闸门就绪度（攒样本，断了要重攒）", True),
    # 东财 push2 长期风控 ⇒ 缺行是常态（与 `app/health._NO_WECHAT_SOURCES` 豁免东财同理）
    #   ⇒ 只报不告警；label 不要写"不告警"，否则与 `format_markdown` 的标注重复
    ("sector_daily", "date", "板块快照 · 东财（常缺）", False),
)

_TABLE_READY = False


# ── 交易日工具 ────────────────────────────────────────────────────────────

def trading_days(n: int, end: Optional[str] = None) -> List[str]:
    """最近 n 个交易日（含 end，默认=最新已完成交易日），**升序**。

    ⚠️ `HOLIDAYS` 目前只有 2026 年（`flash/rules.py`）⇒ 跨年补历史时会退化为"只跳周末"，
       与项目既有口径一致（不为此单独造日历）。
    """
    from app.flash.rules import is_trading_day, latest_completed_trading_day
    e = str(end or latest_completed_trading_day())[:10]
    d = datetime.strptime(e, "%Y-%m-%d").date()
    out: List[str] = []
    guard = 0
    while len(out) < max(1, n) and guard < 400:      # guard：防 is_trading_day 异常死循环
        guard += 1
        if is_trading_day(datetime(d.year, d.month, d.day)):
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(out))


def lag_trading_days(latest: str, expected: str) -> int:
    """`latest` 落后 `expected` **几个交易日**（latest ≥ expected ⇒ 0）。"""
    if not latest or not expected or latest >= expected:
        return 0
    from app.flash.rules import is_trading_day
    d = datetime.strptime(str(latest)[:10], "%Y-%m-%d").date()
    e = datetime.strptime(str(expected)[:10], "%Y-%m-%d").date()
    n = 0
    while d < e:
        d += timedelta(days=1)
        if is_trading_day(datetime(d.year, d.month, d.day)):
            n += 1
    return n


# ── ① 缺口探测 ─────────────────────────────────────────────────────────────

def plate_missing(days: int = PLATE_BACKFILL_DAYS,
                  kinds: Tuple[str, ...] = PLATE_KINDS,
                  dates: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """返回板块快照的缺口 `[(date, kind)]`（升序）。

    `dates` 给定时按**指定日期列表**判（CLI `--date` 补单日 / 跨出 `days` 窗口的历史日），
    否则取最近 `days` 个交易日。

    ⚠️ **按 kind 分别判**（不是"当天有任意行就算有"）：industry/concept 各自一次请求，
       只写成功一个时另一个会静默缺 —— 那种"半截数据"同样要补。
    """
    from app.database import db
    ds = [str(d)[:10] for d in dates] if dates else trading_days(days)
    if not ds:
        return []
    try:
        rows = db.fetch("SELECT DISTINCT date, kind FROM plate_daily_zz WHERE date >= %s",
                        (ds[0],))
    except Exception:
        return [(d, k) for d in ds for k in kinds]      # 表不存在 ⇒ 全缺
    have = {(str(r["date"])[:10], str(r["kind"])) for r in (rows or [])}
    return [(d, k) for d in ds for k in kinds if (d, k) not in have]


# ── ② 自愈回填 ─────────────────────────────────────────────────────────────

def backfill_plate(days: int = PLATE_BACKFILL_DAYS, dry_run: bool = False,
                   dates: Optional[List[str]] = None) -> Dict:
    """补齐板块快照缺口（幂等）。`dates` 给定时按指定日期列表补，否则最近 `days` 个交易日。

    ★ 为什么能补：zzshare `plates_rank` **支持历史日期**（实测 2026-09-30 取
      `2026-09-28`/`2026-09-29` 各返回 104 个板块）—— 这也是当初把「板块分化」
      切到 zzshare 的原因（东财 clist 只给当前快照，缺日**永久无法回补**）。
    ⚠️ 非交易日/源空数据 ⇒ 记入 `failed`（**不重试、不抛**）：日批 fail-open，
       真断档由 `run_gap_check` 告警兜底 —— 否则一个源异常会拖垮整个日批。
    """
    out: Dict = {"missing": [f"{d}/{k}" for d, k in plate_missing(days, dates=dates)],
                 "filled": 0, "rows": 0, "failed": []}
    if dry_run or not out["missing"]:
        return out
    from app.sector_zz import take_snapshot
    for d, k in plate_missing(days, dates=dates):
        try:
            r = take_snapshot(d, kinds=(k,))
            if r.get("written"):
                out["filled"] += 1
                out["rows"] += int(r["written"])
            else:
                out["failed"].append(f"{d}/{k}")
        except Exception as e:
            out["failed"].append(f"{d}/{k}:{str(e)[:40]}")
    return out


# ── ③ 断档自检 + 告警 ──────────────────────────────────────────────────────

def check_gaps(max_lag: int = TOLERATED_LAG_DAYS) -> Dict:
    """关键表断档自检。返回 {expected, checked, gaps, notes, ok}（不改库、不推送）。"""
    from app.database import db
    from app.flash.rules import latest_completed_trading_day
    expected = str(latest_completed_trading_day())[:10]
    gaps, notes, checked = [], [], 0
    for table, col, label, critical in CHECK_TABLES:
        try:
            # f-string 拼接：表名/列名来自本文件常量白名单（无外部输入）；
            # ⚠️ 不能写成 `db.fetch_one(sql, ...)` + `%s` 占位——本项目 DB 层会把 `%s`
            #    透传给 psycopg2，而这里的 `%s` 是**标识符**不是参数（踩过同类坑）。
            row = db.fetch_one(f"SELECT MAX({col}) AS d FROM {table}")
        except Exception as e:
            notes.append(f"{table}: 不可用（{str(e)[:60]}）")     # 表/列不存在 ⇒ 留痕跳过
            continue
        latest = str((row or {}).get("d") or "")[:10]
        if not latest:
            notes.append(f"{table}: 空表（尚无数据）")
            continue
        checked += 1
        lag = lag_trading_days(latest, expected)
        if lag > max_lag:
            gaps.append({"table": table, "label": label, "latest": latest,
                         "lag": lag, "critical": bool(critical)})
    crit = [g for g in gaps if g["critical"]]
    return {"expected": expected, "checked": checked, "gaps": gaps,
            "notes": notes, "ok": not crit}


def format_markdown(res: Dict, heal: Optional[Dict] = None) -> str:
    """告警正文（markdown）。只列 gap；`heal` 有失败时附上自愈结果与排查线索。"""
    lines = [f"> **数据底座断档自检**（应处理交易日 **{res['expected']}**，已检 {res['checked']} 张表）"]
    # ⚠️ 文案用 `≥`（落后**至少** N+1 个交易日）—— 别写成 `>`，那是另一个数（会误导排查）
    lines.append(f"> **{len(res['gaps'])} 张表落后 ≥ {TOLERATED_LAG_DAYS + 1} 个交易日：**")
    for g in res["gaps"]:
        tag = "" if g["critical"] else "（参考·不告警）"
        lines.append(f"> - `{g['table']}` {g['label']}{tag}：最新 **{g['latest']}**，"
                     f"落后 **{g['lag']}** 个交易日")
    if heal and heal.get("failed"):
        lines.append(f"> 自愈已试（最近 {PLATE_BACKFILL_DAYS} 交易日）：补上 "
                     f"{heal['filled']} 项，**仍缺 {len(heal['failed'])} 项** "
                     f"（{', '.join(heal['failed'][:5])}）")
        lines.append("> 排查：zzshare 是否可用 / Actions secrets 是否配 `ZZSHARE_TOKEN`"
                     "（缺 token 会匿名调用 → 0 行）；手动补跑 "
                     "`python scripts/backfill_plate_daily.py`")
    else:
        lines.append("> 排查：对应调度是否停摆（本项目老毛病：Render 循环停摆 ⇒ 迁入日批）；"
                     "板块快照可手动补 `python scripts/backfill_plate_daily.py`")
    return "\n".join(lines)


def _ensure_log() -> None:
    """告警去重表（幂等，兼容 PostgreSQL/SQLite）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    from app.database import db
    col = "DATE" if db._use_postgres else "TEXT"
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS data_gap_alert_log (
            date {col} PRIMARY KEY,
            detail TEXT,
            pushed_at TEXT
        )""")
    _TABLE_READY = True


def run_gap_check(push: bool = True, dry_run: bool = False) -> str:
    """日批入口：**先自愈板块缺口 → 再自检 → 有断档则企微告警**（同日去重）。

    返回一行**ASCII** 摘要（日批日志用，铁律⑥）。`dry_run`/`push=False` 只算不推
    （本地验证用，**不写去重表**）。
    """
    from app.flash import rules
    # ⚠️ `dry_run` 必须一路透传：否则"干跑"仍会写库（本地验证时意外改数据）
    heal = backfill_plate(PLATE_BACKFILL_DAYS, dry_run=dry_run)
    res = check_gaps()
    head = (f"[data_gap] expected={res['expected']} checked={res['checked']} "
            f"gaps={len(res['gaps'])} heal={heal['filled']}/{len(heal['missing'])}")
    if not res["gaps"]:
        return head + (" healed" if heal["filled"] else " all fresh")
    # ⚠️ 方向别写反：`res["ok"] == True` 的语义是"**没有关键表**断档" ⇒
    #    此刻若仍有 gap，那就是"只有参考级表"（非关键），标注一下便于读日志。
    if res["ok"]:
        head += " (non-critical only)"
    if dry_run or not push:
        return head + " push=off"
    _ensure_log()
    from app.database import db
    if db.fetch_one("SELECT date FROM data_gap_alert_log WHERE date = %s", (res["expected"],)):
        return head + " already-pushed"
    try:
        from app.flash.wechat import push_markdown_batched
        # force=True（关键通知）：数据底座静默断档正是本检查存在的理由，不能被
        #   WECHAT_BUSINESS_ALERTS 开关吃掉（同 regime_alert / E2 的稀有信号口径）；
        #   频次天然受限：仅在真断档时推 + 每交易日最多一条。
        push_markdown_batched("⚠️ 数据底座断档", format_markdown(res, heal),
                              force=True)
    except Exception as e:
        print(f"[data_gap] push failed: {str(e)[:80]}")          # ASCII（铁律⑥）
        return head + " push-error"
    try:
        db.upsert("data_gap_alert_log",
                  {"date": res["expected"], "detail": "; ".join(g["table"] for g in res["gaps"]),
                   "pushed_at": rules.beijing_now_iso()}, conflict_columns=["date"])
    except Exception as e:
        print(f"[data_gap] log failed: {str(e)[:80]}")
    return head + " pushed"
