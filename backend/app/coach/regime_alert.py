"""
================================================================================
【文件作用】市况（regime）转档提醒（2026-09-22）
================================================================================
背景（用户痛点驱动）：
  「每天盯评分榜，但不知道什么时候能买」—— 而买入闸门（`trade_gate`）的 **C 条件
  就是"市况允许"**：`defensive` 档**禁买**（REGIME_POSITION=0）。此前
  `market_regime_history` 只落库、**没有任何变化推送**（验收 grep 确认）
  ⇒ 用户只能自己每天盯指数。

定位：**把"等待"自动化** —— 状态切换时主动推企微：
  · 转出 defensive（如 defensive→neutral）＝"可以开始看候选了"（买入闸门 C 解锁）
  · 转入 defensive ＝ 风控信号（仓位归零、禁买，持仓按剧本）

设计：
  · 数据：`market_regime_history` 最近两条（`task_market_regime` 日批第 2 位先落库）
  · 去重：`regime_alert_log`（date 主键）—— 同日只推一次（日批补跑安全）
  · 推送：`wechat.push_markdown_batched(force=True)` —— 转档属关键通知，
    穿透 `WECHAT_BUSINESS_ALERTS`（默认关，见 9-20 因子体检同类设计）
  · 联动：文案带观察池候选数（ready≥2 / 三绿），把"市况 + 候池"一次给全
  · 防抖说明：regime 切换已由 `HYSTERESIS_DAYS=3` 平滑（需连续 3 日），
    本提醒只在**切换确认当日**推一次，不重复打扰

用法：
  from app.coach.regime_alert import run_alert
  print(run_alert())            # 日批任务调用
  print(run_alert(dry_run=True))  # 只组装文案不推送/不落库（测试用）
================================================================================
"""

from datetime import datetime

_TABLE_READY = False


def ensure_table() -> None:
    """创建 regime_alert_log（幂等，兼容 PostgreSQL/SQLite）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    from app.database import db
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS regime_alert_log (
                date DATE PRIMARY KEY,
                state VARCHAR(24),
                from_state VARCHAR(24),
                pushed_at TEXT
            )""")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS regime_alert_log (
                date TEXT PRIMARY KEY,
                state TEXT,
                from_state TEXT,
                pushed_at TEXT
            )""")
    _TABLE_READY = True


def _pool_note() -> str:
    """观察池联动文案（失败静默——推送不能因它失败）。"""
    try:
        from app.routers.scoring import gate_watch
        gw = gate_watch(limit=1)            # 只需要计数，不取明细
        return (f"- **观察池**：ready≥2 共 **{gw.get('total')}** 只"
                f"（三绿 {gw.get('ready3')} 只）\n")
    except Exception:
        return ""


def run_alert(dry_run: bool = False) -> str:
    """比对最近两条 regime 判定；有切换则推企微（同日去重）。返回状态文案。"""
    from app.database import db
    ensure_table()
    rows = db.fetch("SELECT date, state FROM market_regime_history "
                    "ORDER BY date DESC LIMIT 2")
    if len(rows or []) < 2:
        return "市况提醒: 历史不足 2 条，跳过"
    cur, prev = rows[0], rows[1]
    cur_date = str(cur["date"])[:10]
    cur_state, prev_state = cur["state"], prev["state"]
    if cur_state == prev_state:
        return f"市况提醒: {cur_date} {cur_state}（无切换）"
    if not dry_run and db.fetch_one(
            "SELECT date FROM regime_alert_log WHERE date = %s", (cur_date,)):
        return f"市况提醒: {cur_date} {prev_state}→{cur_state} 已推送过，跳过"

    from app.mainforce.trade_gate import REGIME_POSITION
    to_base = REGIME_POSITION.get(cur_state, "?")
    from_base = REGIME_POSITION.get(prev_state, "?")
    recovering = cur_state != "defensive" and prev_state == "defensive"

    if recovering:
        title = f"🔔 市况转档：{prev_state} → {cur_state}（买入闸门 C 解锁）"
        head = (f"**市况修复**：`{prev_state}` → `{cur_state}`\n"
                f"- **仓位基准**：{from_base}% → **{to_base}%**\n"
                f"- **买入闸门 C 条件**：✗ → ✓（`defensive` 禁买解除）\n")
        tail = ("⚠️ **转档 ≠ 立即买入**：仍需闸门 3/3（A 主力根据 + B 不追高 + C 市况）"
                "+ 买入时机「适合介入」。先看观察池里非极端流入的 ready 票。\n")
    elif cur_state == "defensive":
        title = f"⚠️ 市况转防御：{prev_state} → defensive（仓位归零）"
        head = (f"**市况转弱**：`{prev_state}` → `defensive`\n"
                f"- **仓位基准**：{from_base}% → **{to_base}%**（禁买）\n"
                f"- **买入闸门 C 条件**：✓ → ✗\n")
        tail = "持仓按剧本执行（止损/到期），不加仓、不抢反弹。\n"
    else:
        title = f"🔔 市况转档：{prev_state} → {cur_state}"
        head = (f"**市况切换**：`{prev_state}` → `{cur_state}`\n"
                f"- **仓位基准**：{from_base}% → **{to_base}%**\n")
        tail = "按新档位调整仓位上限；候选仍需过闸门 3/3。\n"

    content = (f"{head}"
               f"{_pool_note()}"
               f"\n{tail}"
               f"\n（判定日 {cur_date}；regime 切换需连续 3 个交易日确认（HYSTERESIS），"
               f"本提醒仅在切换确认当日推送一次）")

    if dry_run:
        return f"【dry-run 不推送】{title}\n{content}"

    try:
        from app.flash import wechat
        if not wechat.WECHAT_WEBHOOK:
            return "市况提醒: 检测到转档但未推送（未配置 WECHAT_WEBHOOK）"
        wechat.push_markdown_batched(title, content, force=True)
    except Exception as e:
        return f"市况提醒: 转档 {prev_state}→{cur_state} 但推送失败（{str(e)[:80]}）"
    try:
        db.execute("INSERT INTO regime_alert_log (date, state, from_state, pushed_at) "
                   "VALUES (%s, %s, %s, %s)",
                   (cur_date, cur_state, prev_state,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        print(f"[regime_alert] 去重记录写入失败（已推送，可能重复推）: {e}")
    return f"市况提醒: 已推送 {cur_date} {prev_state}→{cur_state}（force）"
