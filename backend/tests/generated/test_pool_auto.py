# -*- coding: utf-8 -*-
"""自动选池（2026-11-19 用户："池子系统通过挑选自动加入池"）。

红线：
  ① **手动加的票永远不能被自动选池删掉**（`source='manual'`）；
  ② 排除已持仓/已有模拟仓的票（它们本来就在 watch_codes 里）；
  ③ 按 `space_atr_mult` 排序 —— 不是 `space_pct`（后者只是价格的函数）。
"""
import unittest.mock as mock

from app import t_trade_exec as TX

PLANS = [
    {"code": "600001", "name": "A", "space_atr_mult": 1.8, "space_pct": 5.0},
    {"code": "600002", "name": "B", "space_atr_mult": 1.6, "space_pct": 3.0},
    {"code": "600003", "name": "C", "space_atr_mult": 1.4, "space_pct": 4.0},
    {"code": "600004", "name": "D", "space_atr_mult": 1.2, "space_pct": 2.0},
]
MANUAL = [{"code": "600009"}]


def _run(fetch_side, execute_side=0, upsert=None, held=None):
    """喂 db.fetch —— ★ 按 **SQL 内容** 分发，不再按调用顺序。

    ★★ 2026-11-24（CI 挂了 `StopIteration`）：原实现 `side_effect=fetch_side`
      是**位置式**的（[PLANS, MANUAL, []] 对应 t_plans → manual → pool_codes）。
      但 `ensure_tables()` 在 **SQLite**（conftest 强制）下会为新列做
      `PRAGMA table_info(t_trades)` 探测 —— 11-24 成本模型新增 `fee`/`pnl_net`
      两列 ⇒ 多 2 次 db.fetch ⇒ 位置式侧效被挤爆，**主查询拿到 []**。
      （生产 PG 走 `ALTER ... IF NOT EXISTS`，没有这 2 次 fetch，所以是测试脆弱性问题，
        不是线上功能 bug。）

      改成按 SQL 关键字分发：PRAGMA 探测返回 []、主查询返回第 1 项、
      manual 返回第 2 项、其余（pool_codes）返回第 3 项 —— 与调用次数/顺序解耦。
    """
    ups = upsert or (lambda *a, **k: None)
    plans = fetch_side[0] if fetch_side else []
    manual = fetch_side[1] if len(fetch_side) > 1 else []
    rest = fetch_side[2] if len(fetch_side) > 2 else []

    def _by_sql(sql, params=None):
        s = str(sql or "")
        if "PRAGMA table_info" in s:
            return []                       # 建表的列探测，与选池逻辑无关
        if "FROM t_plans" in s:
            return plans
        if "t_watchlist" in s and "manual" in s:
            return manual
        return rest                         # pool_codes() 的 t_watchlist 全量

    with mock.patch("app.t_trade_exec.db.fetch_one",
                    return_value={"d": "2026-11-19"}), \
            mock.patch("app.t_trade_exec.db.fetch", side_effect=_by_sql), \
            mock.patch("app.t_trade_exec.db.execute", return_value=execute_side), \
            mock.patch("app.t_trade_exec.db.upsert", side_effect=ups), \
            mock.patch("app.t_trade_exec.holdings", return_value=held or {}), \
            mock.patch("app.t_trade_exec.resolve_name", side_effect=lambda c, n="": n or c):
        return TX.auto_pick_pool("2026-11-19", top=2)


def test_picks_top_by_atr_mult_and_never_touches_manual():
    """挑前 2 只（按 ATR 倍数），并跳过手动加的票。"""
    upserted = []
    r = _run([PLANS, MANUAL, []], execute_side=3,
             upsert=lambda t, d, **k: upserted.append(d["code"]))
    assert r["picked"] == ["600001", "600002"]
    assert r["removed"] == 3           # 只清了 auto 行
    assert r["excluded_manual"] == 1
    assert all(u != "600009" for u in upserted)


def test_upserted_rows_are_marked_auto():
    upserted = []
    _run([PLANS, [], []], execute_side=0,
         upsert=lambda t, d, **k: upserted.append(d))
    assert len(upserted) == 2
    assert all(d["source"] == "auto" for d in upserted)
    assert all("自动挑选" in d["note"] for d in upserted)


def test_excludes_held_positions():
    """已有仓位（真实或模拟）的票不进池 —— 它们本来就在 watch_codes 里。"""
    r = _run([PLANS, [], []], held={"600001": {"shares": 300}})
    assert "600001" not in r["picked"]
    assert r["picked"] == ["600002", "600003"]
    assert r["excluded_held"] == 1


def test_no_plan_batch_is_safe():
    """没有任何预案批次 ⇒ 不动池子，返回说明。"""
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value={"d": None}), \
            mock.patch("app.t_trade_exec.db.fetch", return_value=[]):
        r = TX.auto_pick_pool("2026-11-19", top=2)
    assert r["picked"] == []
    assert r["plan_date"] is None
    assert "先跑一次盘后扫描" in r["note"]


def test_sql_includes_liquidity_filter():
    """★ 2026-11-19：选池必须按估算成交额过滤（盘口太薄的票触发信号不可信）。"""
    seen = []

    def fake_fetch(sql, params=None):
        seen.append(sql)
        return []

    with mock.patch("app.t_trade_exec.db.fetch_one",
                    return_value={"d": "2026-11-19"}), \
            mock.patch("app.t_trade_exec.db.fetch", side_effect=fake_fetch), \
            mock.patch("app.t_trade_exec.db.execute", return_value=0):
        TX.auto_pick_pool("2026-11-19", top=2)

    main = next(s for s in seen if "stock_facts_wide" in s)
    assert "LEFT JOIN stock_facts_wide" in main
    assert "float_cap_yi * f.turnover_pct / 100.0" in main
    # 缺失放行（缺失 ≠ 不满足）
    assert "f.code IS NULL" in main


def test_note_reports_estimated_amount():
    """note 要写清估算成交额，让人知道为什么这只票被选中。"""
    upserted = []
    plans = [{"code": "600001", "name": "A", "space_pct": 4.0, "ratio": 0.333,
              "float_cap_yi": 50.0, "turnover_pct": 3.0}]
    _run([plans, [], []], upsert=lambda t, d, **k: upserted.append(d))
    assert "成交约 1.50 亿" in upserted[0]["note"]   # 50 × 3% = 1.5 亿


def test_note_marks_unknown_liquidity():
    """查不到量能数据 ⇒ 放行但在 note 里如实标"未知"，不能假装有数。"""
    upserted = []
    plans = [{"code": "600001", "name": "A", "space_pct": 4.0, "ratio": 0.333}]
    _run([plans, [], []], upsert=lambda t, d, **k: upserted.append(d))
    assert "流动性未知" in upserted[0]["note"]


def test_add_to_pool_upgrades_auto_to_manual():
    """★ 手动加一只"自动挑的票"要升级为 manual，否则明天会被自动清掉。"""
    ex = {"source": "auto"}
    seen = {}

    def fake_exec(sql, params=None):
        seen["sql"], seen["params"] = sql, params
        return 1

    with mock.patch("app.t_trade_exec.db.fetch_one", return_value=ex), \
            mock.patch("app.t_trade_exec.db.execute", side_effect=fake_exec), \
            mock.patch("app.t_trade_exec.pool_codes", return_value=[]):
        r = TX.add_to_pool(["600001"], note="我要盯")
    assert r["upgraded"] == ["600001"]
    assert "source='manual'" in seen["sql"]


def test_add_to_pool_skips_existing_manual():
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value={"source": "manual"}), \
            mock.patch("app.t_trade_exec.pool_codes", return_value=[]):
        r = TX.add_to_pool(["600001"])
    assert r["skipped"] == ["600001"]
    assert r["upgraded"] == []
