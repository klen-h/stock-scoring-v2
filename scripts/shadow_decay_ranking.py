#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】旁路衰减榜单：每日产出「生产 top N」与「公告后衰减 top N」两套榜单落库，
用于灰度对比两周收益（验证「财报公告后 20-30 天成长/质量因子反向 → 该衰减」）。
================================================================================

背景（2026-09-17）：
  · 事件研究（quality_defense_backtest.py --event）证明：财报公告后 0-20 天，
    成长/质量因子横截面 IC 为负（拉升出货、利好兑现）；
  · 衰减寻优（--decay）证明：neutral 市况下，公告后 20-30 天内「彻底剔除成长
    （α=0）」能把成长 IC 从 -0.013 抬到 +0.021，两条持有期同向；
  · 但 defensive 市况样本不足、未验证 —— 而当前线上恰是 defensive。
→ 本脚本在实盘**旁路**跑一套衰减版榜单，不改生产主排序，两周后用
  compare_shadow_rank.py 对比两套 top N 的收益，用真实未来收益补 defensive 缺口的证据。

口径（落 3 个 variant，衰减逻辑见 backend/app/scoring/decay.py 单一事实源）：
  · base = 生产榜单（ranking_live 精算结果原样，total_score 排序）。
  · zero = 公告后 ≤25 天成长/质量分 ×0（**初版，对照**）——语义是"判 0 分"，
           会让总分暴跌 ~40 分、把刚公告股全压到 30-40 分档（000612 实测 73.7→33.4）。
  · grad = **梯度降权（主版本）**——按公告龄调整成长/质量的**权重乘数**：
           0-5 天 → 0（剔除，权重分摊给其余维度）、6-20 天 → 0.5、>20 天 → 1.0。
           语义中性（"信号不可信 → 不看"，非"判 0 分"），分数尺度连续
           （000612 实测 73.7→70.4，只降几名而非掉榜）。
  只读 ranking_live + get_finance_batch + get_regime_weights，零评分重算、零网络请求。

用法：
  python scripts/shadow_decay_ranking.py                # dry-run
  python scripts/shadow_decay_ranking.py --apply        # 落库（3 variant）
  python scripts/shadow_decay_ranking.py --top 50
================================================================================
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

from app.database import db   # noqa: E402
from app.scoring.decay import (decay_total, VARIANT_BASE,  # noqa: E402
                               VARIANT_ZERO, VARIANT_GRAD)

_BJ = timezone(timedelta(hours=8))

# 中文维度名 → get_regime_weights 英文键


def _today() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


def init_table() -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS shadow_rank_daily (
            id SERIAL PRIMARY KEY,
            rank_date TEXT NOT NULL,
            variant TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            rank_pos INTEGER,
            total_score REAL,
            decay_age INTEGER,
            snapshot_price REAL,
            dimensions_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rank_date, variant, code)
        )
    """)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_shadow_rank_date_var_code
        ON shadow_rank_daily(rank_date, variant, code)
    """)


def load_live():
    """读 ranking_live 最新榜（全量），返回 (data, rank_date)。"""
    from app.scoring.live_ranking import load
    st = load(limit=1000, use_cache=False)
    return (st.get("data") or []), st.get("date")


def current_weights():
    """当前市况的五维权重（英文键）——复用 decay.resolve_weights（与接口同源）。"""
    from app.scoring.decay import resolve_weights
    return resolve_weights()


def get_prices(codes, on_date):
    """{code: on_date 收盘价}，读 backtest_prices（与 compare 收益口径一致）。

    ★ 必须用快照日（rank_date）而非今天：shadow 榜是盘后快照，快照价应是
      快照日收盘价；且 compare 脚本用 backtest_prices 算未来收益，买入价
      口径必须同源，否则「衰减榜跑赢」可能是价差口径错配。
    """
    prices = {}
    ph = ",".join(["%s"] * len(codes))
    rows = db.fetch(
        f"SELECT code, close FROM backtest_prices WHERE code IN ({ph}) "
        "AND date = %s", (*codes, on_date)) or []
    for r in rows:
        if r.get("close") is not None:
            prices[r["code"]] = float(r["close"])
    return prices


def run(top: int = 50, apply: bool = True) -> str:
    """旁路衰减榜单主逻辑（CLI 与日批 task_shadow_rank 共用）。返回摘要字符串。

    落 3 个 variant（供 compare_shadow_rank.py 对比）：
      · base 生产口径（不衰减）        · zero 公告后≤25天成长/质量分×0（对照）
      · grad 公告龄梯度降权（0-5天剔除、6-20天×0.5，主版本）
    """
    init_table()
    data, rank_date = load_live()
    if not data:
        print("ranking_live 为空，无法旁路（等日批先产出全量精算榜）")
        return "ranking_live 为空，跳过（等日批 rank_live 先产出）"
    codes = [r["code"] for r in data]

    # 财报公告日（拿 notice_date）
    from app.finance import get_finance_batch
    fin = get_finance_batch(codes) or {}

    state, weights = current_weights()
    today = _today()
    prices = get_prices(codes, rank_date)

    print("=" * 74)
    print(f"影子榜（生产 vs 衰减）  date={rank_date}  市况={state or '未知'}")
    print(f"权重 = {weights}")
    print("衰减口径: zero = 公告后≤25天 成长/质量分×0（对照）| "
          "grad = 梯度(0-5天剔除, 6-20天权重×0.5)")
    print("=" * 74)

    # 权重口径校验：不衰减重算应 ≈ ranking_live 总分（否则衰减口径不可信）
    _check_n = min(50, len(data))
    _diff_sum, _cnt = 0.0, 0
    for r in data[:_check_n]:
        _rt = decay_total(r.get("dimensions") or {}, weights, None)   # 不衰减
        _lt = r.get("total_score")
        if _rt is not None and _lt is not None:
            _diff_sum += abs(_rt - _lt)
            _cnt += 1
    if _cnt:
        _avg_diff = _diff_sum / _cnt
        print(f"权重口径校验：不衰减重算 vs ranking_live 总分，前{_cnt}只平均差"
              f" = {_avg_diff:.2f} 分" + ("  OK" if _avg_diff < 0.5
                                        else "  ⚠️ 口径不一致"))
    else:
        print("权重口径校验：无可校验样本")

    recs = []
    for r in data:
        dims = r.get("dimensions") or {}
        nd = (fin.get(r["code"]) or {}).get("notice_date")
        age = None
        if nd:
            try:
                age = (date.fromisoformat(today) - date.fromisoformat(str(nd)[:10])).days
            except ValueError:
                age = None
        recs.append({
            "code": r["code"], "name": r.get("name") or "",
            "base": r.get("total_score") or 0,      # 生产权威 total（ranking_live）
            "zero": decay_total(dims, weights, age, mode=VARIANT_ZERO),
            "grad": decay_total(dims, weights, age, mode=VARIANT_GRAD),
            "age": age,
            "dims": dims,
            "price": prices.get(r["code"]),
        })

    def _rank(key):
        return sorted(recs, key=lambda x: (x[key] is not None, x[key] or 0),
                      reverse=True)[:top]

    tops = {"base": _rank("base"), "zero": _rank("zero"), "grad": _rank("grad")}
    base_codes = {r["code"] for r in tops["base"]}
    ov_zero = len(base_codes & {r["code"] for r in tops["zero"]})
    ov_grad = len(base_codes & {r["code"] for r in tops["grad"]})
    print(f"\ntop{top} 与生产榜的重叠：zero(×0) {ov_zero}/{top} | grad(梯度) {ov_grad}/{top}")

    print(f"\n{'#':>3} {'生产':<7}{'分':>6} | {'zero':<7}{'分':>6} | "
          f"{'grad':<7}{'分':>6}{'age':>5}")
    print("-" * 62)
    for i in range(top):
        b, z, g = tops["base"][i], tops["zero"][i], tops["grad"][i]
        age_s = f"{g['age']}d" if g["age"] is not None else "无"
        zs = str(z["zero"]) if z["zero"] is not None else "-"
        gs = str(g["grad"]) if g["grad"] is not None else "-"
        print(f"{i + 1:>3} {b['code']:<7}{b['base']:>6.1f} | {z['code']:<7}{zs:>6} | "
              f"{g['code']:<7}{gs:>6}{age_s:>5}")

    if not apply:
        print("\n[dry-run] 未落库。加 --apply 写入 shadow_rank_daily。")
        return f"dry-run：base∩grad 重叠 {ov_grad}/{top}，未落库"

    payload = []
    for variant, key in ((VARIANT_BASE, "base"), (VARIANT_ZERO, "zero"),
                         (VARIANT_GRAD, "grad")):
        for i, r in enumerate(tops[key]):
            payload.append({
                "rank_date": rank_date, "variant": variant, "code": r["code"],
                "name": r["name"], "rank_pos": i + 1,
                "total_score": r[key], "decay_age": r["age"],
                "snapshot_price": r["price"],
                "dimensions_json": json.dumps(r["dims"], ensure_ascii=False),
            })
    db.execute("DELETE FROM shadow_rank_daily WHERE rank_date = %s", (rank_date,))
    n = db.upsert_many("shadow_rank_daily", payload, ["rank_date", "variant", "code"])
    summary = (f"影子榜: {n} 行（base/zero/grad 各 {top}，"
               f"base∩grad {ov_grad}/{top}，市况 {state or '?'}）")
    print(f"\n[apply] {summary}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--apply", action="store_true", help="落库（默认 dry-run 仅打印）")
    args = ap.parse_args()
    run(top=args.top, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
