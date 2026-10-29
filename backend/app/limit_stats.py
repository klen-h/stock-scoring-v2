"""
================================================================================
【文件作用】涨跌停家数的**唯一口径**（单票判定 + 家数统计）
================================================================================
【为什么必须收拢（2026-10-09 事故）】
  盘中误推「跌停 123 只 / 黑天鹅熔断」，而当日**收盘真值仅 11~13 只**、且是
  96 涨停的**上涨日**。根因是同一个"跌停家数"在系统里有多份实现、三套阈值：
    · `flash/intraday_alerts` 用 `change_pct <= -9.7` 固定阈值 ⇒ 创业板/科创板（±20%）
      跌 10%~13% 被当成跌停（早盘普跌 826涨/4044跌 时把计数放大到 123）——**事故主因**；
    · `coach/rules` 用 `-19.9 / -9.9` 两档 ⇒ 漏**北交所 30cm**、漏 **ST 5%**；
    · `backtest/market_regime`（→ E2 事件信号）用 `>=9.9 / <=-9.9` 一刀切 ⇒ 20cm 被高估。
  ⇒ 本模块把"单票是不是涨/跌停"与"家数怎么数"收成**一处**，所有消费方 import 它。
    （`routers/market` 已在 2026-09-29/30 先切「按板幅 + rt_k 精确优先」，本模块是
      那次工作的推广与去重 —— 它的 `_limit_counts` / `_limit_counts_best` 现已委托到本模块。）

【三层来源（**全部精确**，任何一层都不是固定阈值）】
  ① `realtime_uplimit.peek()`：zzshare rt_k + `high_limit/low_limit` 逐票判定，
     覆盖率 100%（含北交所/ST）—— **只读缓存、绝不抓取**（首页/告警路径的既有纪律）。
     ⚠️ 两个前提按用途收紧（见 `counts_best` 参数）：
        · 盘前/休市时它给的是**上一交易日收盘定稿**（`is_intraday=False`）；
        · `snapshot()` 是 fail-open（单批失败只保留已拿到的行）⇒ 会**静默漏计**。
  ② 内存行情 dict 的**真值限价** `limit_up/limit_down`（腾讯快照，板块差异自动覆盖）。
  ③ 兜底：按项目**唯一板幅实现** `backtest.engine._limit_pct`
     （主板10 / 双创20 / 北交30 / ST5），留 0.3pt 容差 —— 与 `routers/market._limit_counts`
     同口径（涨停价按分四舍五入 ⇒ 实际涨幅可能略低于板幅）。
================================================================================
"""

# 板幅容差（百分点）——**仅在无真值价时**使用；与 `routers/market._limit_counts` 同口径
# （涨停价按分四舍五入 ⇒ 实际涨幅可能略低于板幅）
_TOL_PCT = 0.3

_limit_pct_fn = None
# ⚠️ 缓存变量**不能与函数同名**：`def _break_tol(...)` 会把同名全局变量整个覆盖成函数对象
#   ⇒ 函数体里读到的 `_break_tol` 就是它自己 ⇒ `1 - _break_tol()` 直接 TypeError。
#   （我第一版踩了这个坑；py_compile / lint 都查不出，**只有真调用**才暴露 —— 见 MEMORY 验证纪律。）
_BREAK_TOL_CACHE = None


def _limit_pct(code, name) -> float:
    """该票的板幅（**百分点**，如 10.0 / 20.0 / 30.0 / 5.0）。

    ★ 复用项目唯一实现 `backtest.engine._limit_pct` —— **绝不在本模块自造板块号段表**
      （自造必然漏掉北交所 30cm 与 ST 5%，那正是本次事故里 coach 那份实现的毛病）。
    """
    global _limit_pct_fn
    if _limit_pct_fn is None:
        from app.backtest.engine import _limit_pct as _f
        _limit_pct_fn = _f
    return _limit_pct_fn(str(code or ""), str(name or "")) * 100


def _break_tol() -> float:
    """涨停"封住"缓冲 `_BREAK_TOL`（0.2%）—— 复用 `routers/market` 的**既有常量**。

    ★ 为什么不能自己写个"固定 0.005 元"的容差（我第一版就是，实测与权威口径差 24 只）：
      `realtime_uplimit.classify` 判涨停用 `close >= high_limit × (1 − _BREAK_TOL)`
      （"防一分钱误差误判回封"）；低价股上 0.2% 只值 1~2 个最小变动单位
      ⇒ 固定绝对容差会系统性**少算**。要同源就必须用**同一个常量**。
    """
    global _BREAK_TOL_CACHE
    if _BREAK_TOL_CACHE is None:
        from app.routers.market import _BREAK_TOL
        _BREAK_TOL_CACHE = float(_BREAK_TOL)
    return _BREAK_TOL_CACHE


def is_limit_up(s: dict) -> bool:
    """单票是否涨停：**真值限价优先**（与 `classify` 逐字同判据）→ 按板幅 + 容差。"""
    price = s.get("price") or 0
    if price <= 0:
        return False
    lu = s.get("limit_up") or 0
    if lu > 0:
        # 与 `realtime_uplimit.classify` 同款：`close >= high_limit × (1 − _BREAK_TOL)`
        return price >= lu * (1 - _break_tol())
    chg = s.get("change_pct")
    if chg is None:
        return False
    lp = _limit_pct(s.get("code"), s.get("name"))
    if chg > lp + _TOL_PCT:
        return False          # 涨幅超过板幅上限 ⇒ 它不受该板幅约束（见下方说明）
    return chg >= lp - _TOL_PCT


def is_limit_down(s: dict) -> bool:
    """单票是否跌停：**真值限价优先**（与 `classify` 逐字同判据）→ 按板幅 + 容差。

    ⚠️ 跌停是**严格** `close <= low_limit`（`classify` 原文如此，没有缓冲）——
      涨停有 `_BREAK_TOL` 缓冲、跌停没有，这是项目既有口径，**照抄不擅自"对称化"**。

    ★★ 无真值价时的**"超过板幅即不算"守卫**（2026-10-10 实测加的）：
      有些票**根本不受板幅约束** —— 新股上市首 5 日（名称带 C）、退市整理期等，
      其 `limit_up/limit_down` 在数据源里就是 **0**。若只按"涨跌幅达板幅"判，
      会把 `C力勤(001246)`（跌 15.87%，主板 ±10%）算成**跌停**（rt_k 精确口径不算）。
      ⇒ 判据：涨跌幅若**超出**板幅上限，说明它不受该板幅约束 ⇒ **不计数**
        （"限制比板幅更宽"与"数据缺失"在这里同形，但按此规则只会**少报**，
         而本次事故的教训正是"宁少报、不误报"）。
      ⚠️ 同类问题**项目原有实现也有**：`routers/market._limit_counts` 的板幅分支
         未做此守卫（此前同页"收盘跌停 13"里含此类票，rt_k 口径为 11）。
    """
    price = s.get("price") or 0
    if price <= 0:
        return False
    ld = s.get("limit_down") or 0
    if ld > 0:
        return price <= ld
    chg = s.get("change_pct")
    if chg is None:
        return False
    lp = _limit_pct(s.get("code"), s.get("name"))
    if chg < -(lp + _TOL_PCT):
        return False          # 跌幅超过板幅下限 ⇒ 不受该板幅约束（新股/退市整理等）
    return chg <= -(lp - _TOL_PCT)


def counts_from_quotes(stocks: dict) -> tuple:
    """内存行情 → `(涨停家数, 跌停家数)`。一次循环判完（不两遍扫）。"""
    lu = ld = 0
    for s in (stocks or {}).values():
        if is_limit_up(s):
            lu += 1
        elif is_limit_down(s):
            ld += 1
    return lu, ld


def counts_best(stocks: dict, require_intraday: bool = False,
                min_trading: int = 0) -> tuple:
    """`(涨停, 跌停, src, meta)` —— **fuyao → rt_k → 内存板幅** 三级。
    返回的 `src` ∈ {"fuyao", "rt_k", "board_approx"}；`meta` 含 `data_date/age_sec/is_intraday`
    及 fuyao 专属的 `seal_rate/open_count/touched_count/boards`，供调用方**披露口径**。

    ★ 按用途收紧采用条件（默认都不要求，与 `routers/market._limit_counts_best` 同口径）：
        · `require_intraday=True` —— 告警用：盘前/休市拿到的是**上一交易日收盘定稿**，
          拿来当"此刻"就是又一次误报（2026-10-09 事故的同类坑）；
        · `min_trading>0` —— 防静默漏计：rt_k `snapshot()` fail-open，缺一两批时
          `trading` 会明显偏小（全量 ≈5400+），此时宁可用内存精确回退。
    """
    mem_up, mem_dn = counts_from_quotes(stocks)
    # ★★ 2026-10-28（阶段 2 · fuyao）：优先读同花顺涨停统计。
    #   【为什么放在 rt_k 之前】① 1 次请求 ~0.2s（rt_k 要 3 批 5569 行 / 14s 预热）；
    #     ② 额外给**封板率 / 炸板家数 / 触板家数 / 连板梯队**（rt_k 只有家数）；
    #     ③ 家数口径来自同花顺加工数据（`limit_up_stock_count`），比我们自己按板幅数更权威。
    #   【为什么跌停要单独校验】涨跌停池端点**有 50 条上限**（实测传 limit=200 仍回 50）
    #     ⇒ `len(dnp) >= 50` 视为可能截断（千股跌停日），整组不用、回退 rt_k。
    fu, fd, fmeta = _fuyao_counts(require_intraday)
    if fu is not None:
        return (fu, fd, "fuyao", fmeta)
    pk = None
    try:
        from app import realtime_uplimit
        pk = realtime_uplimit.peek()
    except Exception as e:
        print(f"[limit_stats] rt_k peek failed: {str(e)[:80]}")     # ASCII（铁律⑥）
    if pk and pk.get("limit_up") is not None and pk.get("limit_down") is not None:
        usable = True
        if require_intraday and not pk.get("is_intraday"):
            usable = False
        if min_trading and (pk.get("trading") or 0) < int(min_trading):
            usable = False
        # ★★ 2026-10-29（线上 09:22 日志实测）：**全零保护** —— 今天新发现的一类静默错。
        #   集合竞价时段（09:15~09:25）rt_k 的 `close` 尚未成交（=0），而 `high_limit` 有值
        #   ⇒ 逐票 `close >= high_limit × (1-tol)` **全不成立** ⇒ up=0 down=0；
        #   但 `data_date` 已经是今天 ⇒ `is_intraday=True` ⇒ 上面两道闸门**都放行**
        #   ⇒ 页面显示「涨停 0 / 跌停 0」。
        #   同一条日志里能直接看到矛盾：
        #     approx(mem) up=21 down=13 | rt_k(exact) up=0 down=0 | delta up=-21
        #   "0 只涨停"比"21 只"更误导（用户会以为今天真的一家都没涨停）。
        #   ⇒ 判据：**连"曾触板"都是 0，而内存侧有数** ⇒ rt_k 快照不可用，回退近似。
        #   ⚠️ 为什么不直接判 `up==0`：极端冰点日真的可能 0 涨停，那时内存侧也是 0，
        #      `and (mem_up or mem_dn)` 这个条件让该情形仍走 rt_k（两者一致，无损）。
        if not (pk.get("touched") or 0) and (mem_up or mem_dn):
            usable = False
        if usable:
            return (int(pk["limit_up"]), int(pk["limit_down"]), "rt_k",
                    {"age_sec": pk.get("age"), "data_date": pk.get("data_date"),
                     "is_intraday": pk.get("is_intraday")})
    return (mem_up, mem_dn, "board_approx",
            {"age_sec": None, "data_date": None, "is_intraday": None})


# 连板档位中文（fuyao `page_boards[].board_level` 实测取值：
# one/two/three/four/**five_plus**；多的档位先给保守映射，未知值原样透传）
_BOARD_CN = {"one": "首板", "two": "2板", "three": "3板", "four": "4板",
             "five": "5板", "five_plus": "5板+", "six": "6板", "seven": "7板",
             "eight": "8板", "nine": "9板", "ten": "10板+"}


def _norm_boards(raw) -> list:
    """`page_boards` → `[{level,label,count,promotion_rate}]`（保留原 level 便于前端判等）。"""
    out = []
    for b in (raw or []):
        if not isinstance(b, dict):
            continue
        lvl = str(b.get("board_level") or "")
        if not lvl:
            continue
        out.append({"level": lvl, "label": _BOARD_CN.get(lvl, lvl),
                    "count": b.get("stock_count"),
                    "promotion_rate": b.get("promotion_rate")})
    return out


def _fuyao_counts(require_intraday: bool = False) -> tuple:
    """fuyao 涨停/跌停家数 + 增强指标；不可用 / 被截断 / 非当日 ⇒ `(None, None, {})`。"""
    try:
        from app import fuyao
        if not fuyao.enabled():
            return None, None, {}
        st = fuyao.limit_up_stats()
        if not st or st.get("limit_up_stock_count") is None:
            return None, None, {}
        td = str(st.get("trade_date") or "")[:10]
        if require_intraday:
            # 盘前/休市时 trade_date 是上一交易日 ⇒ 不能当"此刻"（与 rt_k 的 is_intraday 同义）
            from app.flash.rules import beijing_now
            if td != beijing_now().strftime("%Y-%m-%d"):
                return None, None, {}
        dnp = fuyao.limit_down_pool()
        if not dnp or len(dnp) >= 50:      # 50 是端点上限 ⇒ 可能截断，整组不用
            return None, None, {}
        return int(st["limit_up_stock_count"]), len(dnp), {
            "data_date": td or None,
            "age_sec": None,
            "is_intraday": None,
            "seal_rate": st.get("seal_rate"),
            "open_count": st.get("open_stock_count"),
            "touched_count": st.get("touched_limit_up_stock_count"),
            "boards": _norm_boards(st.get("page_boards")),
        }
    except Exception as e:
        print(f"[limit_stats] fuyao counts failed: {str(e)[:80]}")   # ASCII（铁律⑥）
        return None, None, {}
