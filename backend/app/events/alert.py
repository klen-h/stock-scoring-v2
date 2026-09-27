"""
================================================================================
【文件作用】E2 政策脉冲 · 企微推送（2026-09-27）
================================================================================
背景（为什么需要它）：
  E2 此前**只在工作台顶栏显示** —— 而它 21 年仅 296 个触发日 / **60 个独立事件簇**
  （约每年 3 次）⇒ 用户不开着页面的那天正好触发，就**永远看不到**。
  验证做完了却触达不到，等于没做。

定位：**把稀有信号推到用户面前**（不是新的决策逻辑）。
  · 只推「**进入事件期**」的那一天（簇内连续触发日不重复推）—— E2 中位间隔仅 6 个
    交易日，若每日都推会「过吵」，与本项目推送纪律相悖。
  · `force=True` 穿透 `WECHAT_BUSINESS_ALERTS` —— 稀有且关键，属关键通知
    （与 `coach/regime_alert.py` 同类设计）。

设计（对齐 `coach/regime_alert.py`）：
  · 判定源：`app.events.signal.get_event_signal()`（实时行情缓存；盘后由收盘快照兜底）
  · 去重：`event_alert_log`（date 主键）+ **簇窗口** `CLUSTER_GAP_DAYS=28`
    （自然日 ＝ 20 交易日，与 `event_edge_check.py:CLUSTER_GAP` 的簇定义对齐）
    —— 距上次推送不足该窗口 ⇒ 视为同一事件簇，跳过。
  · 接入：`scheduler.event_alert_loop`（**仅 Render 常驻**，盘后 16:00-23:59 窗口）。
    ⚠️ **不进日批**：Actions 独立进程无实时行情缓存，`get_event_signal()` 必然
       `available=False`，加了是空转（同 `record_event_snapshot` 的判断）。

⚠️ 文案纪律：必须带「历史统计参考，非投资建议」+「不进决策链」+ 稀有/假信号提示，
   防止用户把它当买入指令（本项目「无预测力的形态不当信号用」的既有教训）。

用法：
  from app.events.alert import run_alert
  print(run_alert())              # 调度器调用
  print(run_alert(dry_run=True))  # 只组装文案，不推送/不落库（测试用）
================================================================================
"""
from datetime import datetime

_TABLE_READY = False
# ★ 2026-09-27 修正（口径对齐）：原值 25 自然日 ≈ **17 交易日**，**短于**簇定义
#   （`event_edge_check.py:CLUSTER_GAP = 20` **交易日**）⇒ 相邻间隔 18~19 交易日的两次触发
#   会被「簇定义」判为**同一簇**、却在推送层**重复推送**（违背「簇内只推一次」的设计意图）。
#   改为 **28 自然日 = 20 交易日（4 周）**，与验证口径一致。
#   ⚠️ 局限：长假会把**同一簇**的自然日间隔拉长（如 18 交易日 + 春节 ≈ 32 自然日）
#      ⇒ 仍可能重复推。彻底解法需按**交易日**计数，但 `market_events` 可能落库不全（不可靠）
#      ⇒ 接受此近似：E2 年均仅 ~3 簇，且 28 天已把误判面从 8 次大幅收窄。
CLUSTER_GAP_DAYS = 28


def ensure_table() -> None:
    """创建 event_alert_log（幂等，兼容 PostgreSQL/SQLite）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    from app.database import db
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS event_alert_log (
                date DATE PRIMARY KEY,
                up_ratio REAL,
                pushed_at TEXT
            )""")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS event_alert_log (
                date TEXT PRIMARY KEY,
                up_ratio REAL,
                pushed_at TEXT
            )""")
    _TABLE_READY = True


def _build(ev: dict) -> tuple:
    """组装推送文案 → (title, content)。"""
    from app.events.signal import E2_STATS, EXEC_STATS
    s = E2_STATS
    d = s["by_regime"]["defensive"]
    ur = ev.get("up_ratio")
    ur_s = f"{ur:.1%}" if ur is not None else "—"
    lr = ev.get("limit_up_ratio")
    lr_s = f"{lr:.2%}" if lr is not None else "—"
    ex_lines = "\n".join(
        f"- {t['name']}({t['etf']}) **+{t['net_edge']}pp**（P={t['p']}，n={t['n']}）"
        for t in EXEC_STATS["targets"])
    content = (
        f"**触发**：涨家数占比 **{ur_s}**、涨停 {ev.get('limit_up')} 家"
        f"（占比 {lr_s}）\n\n"
        f"**历史同态**（{s['n']} 天 / {s['n_clusters']} 个独立事件簇，2005-2026）：\n"
        f"- 后 20 日全市场等权 **+{s['h20_mean']}%**"
        f"（基准 +{s['h20_base']}%，胜率 {s['h20_win']}%）\n"
        f"- 防御期内增量 **+{d['diff']}pp**（n={d['n']}）\n\n"
        f"**历史同态可执行标的**（T+1 开盘买、持 20 日、**已扣 0.3% 成本**）：\n"
        f"{ex_lines}\n\n"
        f"⚠️ **两点必须知道**：\n"
        f"1. **历史统计参考，非投资建议**；本信号**不进决策链**（不改市况/仓位/买入闸门）\n"
        f"2. E2 稀有（21 年仅 {s['n_clusters']} 个独立簇，约每年 3 次）且**有假信号**"
        f"（2008 年 28 次均 -1.1%）\n\n"
        f"（触发日 {ev.get('date')}；同一事件簇内只推一次）"
    )
    return "政策脉冲信号（E2）", content


def run_alert(dry_run: bool = False) -> str:
    """判定 E2 → 簇去重 → 推企微。返回状态文案（供日志）。"""
    from app.events.signal import get_event_signal
    ev = get_event_signal()
    if not ev.get("available"):
        return "事件推送: 行情缓存不可用，跳过（不 mark_done，等下次重试）"
    if not ev.get("e2_policy_surge"):
        return f"事件推送: {ev.get('date')} 无 E2（涨家数占比 {ev.get('up_ratio')}）"

    today = ev["date"]
    ensure_table()
    from app.database import db
    try:
        last = db.fetch_one("SELECT date FROM event_alert_log "
                            "ORDER BY date DESC LIMIT 1")
    except Exception:
        last = None
    if last and not dry_run:
        try:
            d0 = datetime.strptime(str(last["date"])[:10], "%Y-%m-%d")
            d1 = datetime.strptime(today, "%Y-%m-%d")
            gap = (d1 - d0).days
            if gap < CLUSTER_GAP_DAYS:
                return (f"事件推送: {today} 命中 E2，但距上次推送仅 {gap} 天"
                        f"（< {CLUSTER_GAP_DAYS}）⇒ 同一事件簇，跳过")
        except Exception:
            pass

    title, content = _build(ev)
    if dry_run:
        return f"【dry-run 不推送】{title}\n{content}"

    try:
        from app.flash import wechat
        if not wechat.WECHAT_WEBHOOK:
            return "事件推送: 检测到 E2 但未推送（未配置 WECHAT_WEBHOOK）"
        wechat.push_markdown_batched(title, content, force=True)
    except Exception as e:
        return f"事件推送: E2 触发但推送失败（{str(e)[:80]}）"
    try:
        db.execute("INSERT INTO event_alert_log (date, up_ratio, pushed_at) "
                   "VALUES (%s, %s, %s)",
                   (today, ev.get("up_ratio"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        print(f"[event_alert] 去重记录写入失败（已推送，可能重复推）: {e}")
    return f"事件推送: 已推送 {today} E2（force）"
