# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】做T API（2026-11-15）
================================================================================
URL 前缀 /api/t-trade（JWT 保护）：

  GET  /api/t-trade/plans?date=&only_feasible=1   → 当日（或最近一批）预案清单
  GET  /api/t-trade/plan?code=000048              → 单只预案（库里有读库，没有则实时算）
  GET  /api/t-trade/explain?code=000048           → 实时解释"为什么能做/不能做"
  GET  /api/t-trade/summary?date=                 → 当日汇总（预案 + 未平 + 已平 + 盈亏）
  POST /api/t-trade/scan                          → 手动补跑全市场扫描（后台任务）

【数据流（理解这个才能看懂接口）】
  盘后 15:40 `t_trade_scan_loop` → `scan_market()` 扫全市场 → 只把 `feasible=True`
  的预案写进 `t_plans`；盘中 `t_trade_loop` 60s 读这些预案 + 腾讯内存价判断触发。
  ⇒ 所以 `plans` 里的"日期"通常是**上一交易日收盘**算的，接口会把 `plan_date`
    原样返回，**前端必须显示它是哪天的**（不能假装是今天盘中算的）。
================================================================================
"""
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException

from app import t_trade_exec as TX
from app.auth import get_current_user
from app.database import db

router = APIRouter()


def _enrich_facts(rows: list):
    """给预案补**日频量能字段**（`stock_facts_wide` 最新快照）。

    【为什么分两套行情】
      · **实时涨跌幅**：前端调 `/score/batch-prices`（与评分榜**同口径**，
        零新增机制）—— 那个接口只给 `{code, name, price, change_pct}`；
      · **量比 / 换手 / 流通市值**：本项目已经按日算好存在 `stock_facts_wide`，
        一次批量查询即可，没必要让前端再算一遍。
    ⚠️ 日频字段是**上一交易日**的，接口返回 `facts_date` 让前端显示清楚，
      不能让人误以为是盘中实时。
    """
    if not rows:
        return rows, None
    codes = [str(r.get("code") or "") for r in rows if r.get("code")]
    if not codes:
        return rows, None
    facts_date = None
    try:
        facts_date = (db.fetch_one(
            "SELECT MAX(date) AS d FROM stock_facts_wide") or {}).get("d")
        if not facts_date:
            return rows, None
        ph = ",".join(["%s"] * len(codes))
        facts = db.fetch(
            f"SELECT code, vol_ratio, turnover_pct, float_cap_yi, phase "
            f"FROM stock_facts_wide WHERE date=%s AND code IN ({ph})",
            tuple([facts_date] + codes)) or []
        fm = {str(f["code"]): dict(f) for f in facts}
        for r in rows:
            f = fm.get(str(r.get("code") or "")) or {}
            r["vol_ratio"] = f.get("vol_ratio")
            r["turnover_pct"] = f.get("turnover_pct")
            r["float_cap_yi"] = f.get("float_cap_yi")
            r["phase"] = f.get("phase")
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）——否则"量能列一直是空的"无从排查
        print(f"[t-trade] enrich facts failed: {str(e)[:80]}")
    return rows, str(facts_date)[:10] if facts_date else None


def _attach_buy_check(rows: list):
    """给每只算**现在能不能下单**（正T 口径）。

    用户（2026-11-15）："要看下资金现状给不给买入。"

    ★ 目的：让前端能显示"**底仓不足**"而不是让用户以为"今天没机会"——
      前者要加仓或换标的，后者只是等。

    ⚠️ **刻意不读** `regime` / `risk_frozen` / `cooldown_until`：
      那些是"加新仓"的闸门，做T 净仓位不变，不该被拦住
      （完整理由见 `t_trade.buy_check` 的 docstring）。
    """
    from app import t_trade as TT

    if not rows:
        return rows
    try:
        holds = TX.holdings()
        cash = TT.T_CASH_ASSUMED
        for r in rows:
            code = str(r.get("code") or "")
            base = float((holds.get(code) or {}).get("shares") or 0)
            ratio = float(r.get("ratio") or 0.333)
            chk = TT.buy_check(base, cash, r.get("price_ref"), ratio)
            r["base_shares"] = base
            r["buy_ok"] = chk["ok"]
            r["buy_shares"] = chk["shares"]
            r["buy_reason"] = chk["reason"]
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）——否则"能不能买"这一列永远空着
        print(f"[t-trade] attach buy_check failed: {str(e)[:80]}")
    return rows


@router.get("/plans")
def get_plans(date: str = None, only_feasible: bool = True,
              watch_only: bool = False, limit: int = 60,
              user: dict = Depends(get_current_user)):
    """当日（或最近一批）做T 预案清单，按 `space_atr_mult` 降序（空间越足越靠前）。

    `watch_only=1` ⇒ 只返回**自选+持仓**里可做的（工作台卡用这个，
      否则会给用户糊 300+ 只他根本不关心的票）。
    """
    rows = TX.list_plans(date, feasible_only=only_feasible)
    if watch_only:
        w = TX.watch_codes()
        rows = [r for r in rows if str(r.get("code") or "") in w]
    rows.sort(key=lambda x: -(x.get("space_atr_mult") or 0))
    lim = min(max(limit, 1), 500)
    # 先截断再补字段：只查真正要返回的那几条，省一次大 IN 查询
    data, facts_date = _enrich_facts(rows[:lim])
    data = _attach_buy_check(data)
    return {"data": data, "total": len(rows), "watch_only": watch_only,
            "facts_date": facts_date}


@router.get("/plan")
def get_plan(code: str = "", date: str = None,
             user: dict = Depends(get_current_user)):
    """单只预案：优先读库（盘后生成的），库里没有就**实时算一次**（含不可行原因）。"""
    c = str(code or "").strip()
    if not c:
        raise HTTPException(400, "缺少 code")
    p = TX.load_plan(c, date)
    if p:
        rows, facts_date = _enrich_facts([p])
        return {"data": rows[0], "source": "db", "facts_date": facts_date}
    e = TX.explain(c, date)
    rows, facts_date = _enrich_facts([e])
    return {"data": rows[0], "source": "live", "facts_date": facts_date}


@router.get("/explain")
def get_explain(code: str = "",
                user: dict = Depends(get_current_user)):
    """实时解释某只票**此刻**能不能做T —— 用于回答"为什么它不在清单里"。"""
    c = str(code or "").strip()
    if not c:
        raise HTTPException(400, "缺少 code")
    return {"data": TX.explain(c)}


@router.get("/pool")
def get_pool(user: dict = Depends(get_current_user)):
    """做T 池（**独立于自选** —— 自选是"想盯的票"，这里是"能做T 的票"）。"""
    rows = TX.pool_codes()
    # 池里"今日不可做"的票没有预案行，也把量能补上 —— 否则那几行只有代码和名字
    rows, facts_date = _enrich_facts(rows)
    # ⚠️ 池清单的行**没有** `price_ref` / `ratio`（不是预案行）⇒ 只对有的算，
    #   不给"今日不可做"的票安上误导性的 `buy_ok=False`（那会被前端显示成
    #   "仓位不足"，而实际是"今天没预案"——两回事）。
    #   `_attach_buy_check` 是**原地修改**，所以 `rows` 本身已带上字段；
    #   **绝不能写成 `rows = _attach_buy_check([...])`** —— 那会把没有预案的行过滤掉，
    #   池子页就看不到"今日不可做"的票了。
    _attach_buy_check([r for r in rows if r.get("price_ref")])
    return {"data": rows, "total": len(rows), "facts_date": facts_date}


@router.post("/pool")
def add_pool(payload: dict = Body(...),
             user: dict = Depends(get_current_user)):
    """批量加进做T 池（幂等）。

    请求体：`{"codes": ["000415", "000878"], "note": "磨底价值股 2026-11-15"}`
    """
    codes = payload.get("codes") or []
    if isinstance(codes, str):
        codes = [codes]
    if not isinstance(codes, list) or not codes:
        raise HTTPException(400, "codes 不能为空")
    return TX.add_to_pool([str(c) for c in codes],
                          note=str(payload.get("note") or ""))


@router.delete("/pool/{code}")
def del_pool(code: str, user: dict = Depends(get_current_user)):
    """从做T 池移除。"""
    ok = TX.remove_from_pool(code)
    if not ok:
        raise HTTPException(500, "移除失败")
    return {"success": True, "code": code}


@router.post("/test-push")
def test_push(user: dict = Depends(get_current_user)):
    """发一条做T 测试消息，验证企微通道是否真的通。

    【为什么需要这个接口】
      做T 的触发/平仓都走 `notify("coach", ...)`，但 `push_log` 里
      **从没出现过 `coach` 分类** ⇒ 没法从历史判断这个分类群配没配。
      与其"等真信号触发时才发现没收到"，不如让用户主动验证一次。

    【通道解析顺序】（见 `flash/wechat.py` 文件头）
      ① `WECHAT_HOOK_COACH`（分类群机器人）
      ② `WECHAT_NOTIFY_COACH`（应用消息）
      ③ `WECHAT_WEBHOOK`（主群兜底）
      ⇒ 只要 ① 或 ③ 有一个配了就能收到；**全没配时推送静默跳过**（只走 Web）。
    """
    from app.flash.wechat import notify

    ok = notify("coach", "做T 通道自检",
                "收到这条说明**做T 的触发/平仓通知能送达**。\n"
                "本消息由 `/api/t-trade/test-push` 发起，不是真实交易信号。",
                force=True)
    return {"success": bool(ok),
            "note": ("已发送，请查企微" if ok else
                     "未送达 —— 检查 WECHAT_HOOK_COACH 或 WECHAT_WEBHOOK 是否配置")}


@router.get("/summary")
def get_summary(date: str = None, user: dict = Depends(get_current_user)):
    """当日做T 汇总（预案 + 未平 + 已平 + 模拟盈亏）。"""
    return TX.daily_summary(date)


@router.post("/scan")
def trigger_scan(background: BackgroundTasks, date: str = None,
                 user: dict = Depends(get_current_user)):
    """手动补跑全市场扫描。

    ⚠️ 同步跑要 **72 秒**（1025 只 × 算 ATR/位）⇒ 走 BackgroundTasks 立即返回，
      结果看日志 `[t-trade] scan done`。**不要**在这里同步阻塞（会让 HTTP 超时）。
    """
    background.add_task(TX.scan_market, date, 120, 200, True)
    return {"success": True, "note": "后台扫描已启动（约 1~2 分钟），完成后预案自动落库"}
