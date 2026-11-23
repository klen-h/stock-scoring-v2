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

from app import t_trade as TT
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
def get_pool(include_holdings: bool = True,
             user: dict = Depends(get_current_user)):
    """做T 池（**独立于自选** —— 自选是"想盯的票"，这里是"能做T 的票"）。

    ★ 2026-11-16（用户："做T 交易台的池子不会自动加入吗？"）：
      **持仓本来就会被自动监控**（`watch_codes() = 持仓 + 池`），
      但本接口原先**只返回 `t_watchlist`** ⇒ 池空时用户看到"池子是空的"，
      会以为"没有任何票在被监控"，而实际上持仓一直在盯。
      ⇒ 默认 `include_holdings=1`，把持仓一并返回并标 `source`。
      **这不会让任何东西"自动进池"**（池仍是手动维护的观察列表），
      只是把**真实的监控范围**如实展示出来。
    """
    rows = TX.pool_codes()
    if include_holdings:
        try:
            # ★ 2026-11-16：`holdings()` 现在 = 真实持仓 + **模拟底仓**（sim=True）。
            #   `source` 必须能区分三层，否则用户分不清"这 300 股是真买的还是
            #   系统自动建的模拟仓"（两者处置完全不同：真实持仓要自己能卖，
            #   模拟仓随时可清）。
            held = TX.holdings()
            in_pool = {str(r.get("code") or "") for r in rows}
            for r in rows:
                # ★ 2026-11-19：保留"这只票是怎么进池的"（auto/manual），
                #   另用一个字段表示"来自池"，否则前端分不清
                #   哪些是系统自动挑的、哪些是自己加的。
                r["pool_source"] = str(r.get("source") or "manual")
                r["source"] = "pool"
            for code, h in held.items():
                _sim = bool(h.get("sim"))
                _src = "sim" if _sim else None
                if code in in_pool:
                    for r in rows:
                        if str(r.get("code")) == code:
                            r["source"] = _src or "both"   # 模拟仓优先标注
                            r["shares"] = h.get("shares")
                            break
                else:
                    rows.append({
                        "code": code, "name": h.get("name") or code,
                        "note": "", "added_at": "",
                        "source": _src or "holding",       # 仅持仓 / 仅模拟仓
                        "shares": h.get("shares"),
                    })
        except Exception as e:
            # 吞异常必须可见（GUARDRAILS #11）
            print(f"[t-trade] pool include_holdings failed: {str(e)[:80]}")
            for r in rows:
                r.setdefault("source", "pool")
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


@router.post("/pool/auto")
def auto_pool(top: int = None, day: str = None,
              user: dict = Depends(get_current_user)):
    """**立即自动选池**（按当日 feasible 预案的 `space_atr_mult` 取前 N 只）。

    平时由盘后扫描自动触发（`scan_market` 末尾）；这个端点用于手动重跑。

    ⚠️ **只替换 `source='auto'` 的行** —— 你手动加的票永远不会被它清掉。
    ⚠️ 排除已有持仓/模拟仓的票（它们本来就在监控范围内，进池只是重复）。
    """
    return TX.auto_pick_pool(day, top)


@router.delete("/pool/{code}")
def del_pool(code: str, user: dict = Depends(get_current_user)):
    """从做T 池移除。"""
    ok = TX.remove_from_pool(code)
    if not ok:
        raise HTTPException(500, "移除失败")
    return {"success": True, "code": code}


@router.get("/sim")
def get_sim(user: dict = Depends(get_current_user)):
    """做T **模拟底仓**清单（自动建仓的落点）。

    ⚠️ 与 `user_portfolio`（真实持仓）是**两张表**：系统没有券商接口，
      自动建仓只能是模拟。真实买入后建议在这里清掉，避免两层底仓并存。
    """
    held = TX.sim_holdings()
    return {"data": TX.sim_positions(),
            # ★ `total` 只算**持有中**的：`data` 含 closed 历史行，
            #   拿 `len(data)` 当前端"模拟仓 N 只"会越用越大（把平掉的也算进去）。
            "total": len(held),
            "holding_codes": sorted(held.keys()),
            "auto_build": bool(getattr(TT, "_AUTO_BUILD", False)),
            "note": "模拟仓：不涉及真实资金；做T 盈亏仅作参考"}


@router.delete("/sim/{code}")
def del_sim(code: str, user: dict = Depends(get_current_user)):
    """平掉/清除模拟底仓（只改 status，不删历史）。"""
    ok = TX.close_sim(code, note="用户手动清除")
    if not ok:
        raise HTTPException(500, "清除失败")
    return {"success": True, "code": code}


@router.post("/sim")
def add_sim(payload: dict = Body(...),
            user: dict = Depends(get_current_user)):
    """手动建一笔模拟底仓（不想等建仓点、想立刻验证做T 时用）。

    请求体：`{"code": "000415", "shares": 300, "price": 4.66}`
    `shares` 缺省按 `min_base_shares(ratio)` 反算。
    """
    code = str(payload.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "缺少 code")
    price = float(payload.get("price") or 0)
    if price <= 0:
        p = TX.load_plan(code) or TX.explain(code) or {}
        price = float(p.get("price_ref") or p.get("price") or 0)
    if price <= 0:
        raise HTTPException(400, "无法确定价格，请显式传 price")
    shares = int(payload.get("shares") or 0)
    if shares <= 0:
        ratio = TT.T_RATIO_DEFAULT
        try:
            ratio = float((TX.load_plan(code) or {}).get("ratio") or ratio)
        except Exception as e:
            print(f"[t-trade] add_sim ratio {code} failed: {str(e)[:60]}")
        shares = max(300, TT.min_base_shares(ratio))
    r = TX.auto_build(code, payload.get("name") or code, price, shares,
                      note="手动建立模拟底仓")
    if not r:
        raise HTTPException(500, "建立模拟底仓失败")
    return {"success": True, **r}


@router.post("/build-plan")
def build_plan(code: str = "", user: dict = Depends(get_current_user)):
    """用做T 预案的**支撑位**，建一条「底仓建仓点」交易计划。

    用户（2026-11-15）："我的池子符合做T，可以加入某个支撑位买入底仓的功能吗？"

    【为什么复用 `user_trade_plans` 而不是新写一套】
      `trade_plan_trigger_loop`（`scheduler.py`）**已经在盯 `buy_price`**：
      现价距买价 ±2% 就推「到达买点附近，按预案试仓」。
      再写一个"到支撑位提醒"的循环 = **重复造 + 两套阈值打架**
      （本项目已多次踩"两处各写一份必然漂移"，如 `_bench_code`、`SPREAD_THRESHOLD`）。

    【⚠️ 这是建仓，不是做T —— 风险特征完全不同】
      · 做T：净仓位**不变**（卖自己的持仓、买回自己的持仓）；
      · 建仓：**新增敞口**，方向错了就是实亏。
      ⇒ 所以 `stop_loss` 必须给（支撑 − 0.5×ATR，与做T 止损同一口径），
        并且 `target` 用预案阻力位，保证"盈亏比"从一开始就是清楚的。

    【资金说明】`buy_price = 支撑`，一次买多少**不由本接口决定**
      （系统不知道实盘现金）—— 建议至少 300 股，否则 1/3 仓不足 100 股，做T 仍做不了。
    """
    c = str(code or "").strip()
    if not c:
        raise HTTPException(400, "缺少 code")

    p = TX.load_plan(c) or TX.explain(c)
    if not p or not p.get("feasible"):
        raise HTTPException(
            400, "该票今日无可行预案（不设建仓点）：%s"
                 % ((p or {}).get("reason") or "无数据"))

    if not (p.get("support") and p.get("resistance")):
        raise HTTPException(400, "预案缺支撑/阻力，无法设建仓点")

    # ★ 2026-11-23：落库逻辑抽到 `TX.build_plan_row()` ——
    #   自动选池（`auto_build_plans`）与本接口**必须**走同一份代码，
    #   否则"手动建的"和"自动建的"迟早漂移（项目已多次踩过这个坑）。
    pp = dict(p)
    pp.setdefault("code", c)

    # 建仓股数按 `ratio` 反算（`100/ratio`），避免"建了仓却做不了T"——
    #   `ratio=0.2` 买 300 股只能卖 60 股。细则见 `TX.build_plan_row`。
    r = TX.build_plan_row(pp)
    if not r.get("success"):
        raise HTTPException(500, "建计划失败：%s" % r.get("error"))

    return {"success": True, "id": r["id"], "code": c,
            "buy_price": r["buy_price"], "stop_loss": r["stop_loss"],
            "target": r["target"],
            # ★ 建仓数量建议（用户要求"直接买到 300 股以上"）
            "suggest_shares": r["suggest_shares"],
            "suggest_cash": r["suggest_cash"],
            "note": "已建立仓计划；现价到 %.2f 附近（±2%%）会推企微提醒。"
                    "建议一次买 %d 股（约 %.0f 元）"
                    % (r["buy_price"], r["suggest_shares"], r["suggest_cash"]),
            "warning": "这是**建仓**（新增敞口），不是做T；请先想好仓位与止损"}


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


@router.get("/history")
def get_history(limit: int = 200, day_from: str = None,
                user: dict = Depends(get_current_user)):
    """**历史成交 + 累计收益**（跨日）。

    ★ 2026-11-22（用户："做t明明有交易，但是我看不到历史及收益情况"）：
      `/summary` 只查当日，隔天/周末打开就看不到历史的 T。本端点补这个缺口。
    """
    return TX.history(limit=limit, day_from=day_from)


@router.post("/scan")
def trigger_scan(background: BackgroundTasks, date: str = None,
                 user: dict = Depends(get_current_user)):
    """手动补跑全市场扫描。

    ⚠️ 同步跑要 **72 秒**（1025 只 × 算 ATR/位）⇒ 走 BackgroundTasks 立即返回，
      结果看日志 `[t-trade] scan done`。**不要**在这里同步阻塞（会让 HTTP 超时）。
    """
    background.add_task(TX.scan_market, date, 120, 200, True)
    return {"success": True, "note": "后台扫描已启动（约 1~2 分钟），完成后预案自动落库"}
