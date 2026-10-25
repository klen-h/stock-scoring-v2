#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】H2' 阴跌防御因子影子榜（variant=defensive，旁路不改生产排序）
================================================================================

判据（方案 §7 冻结 v1.0 / §9 用户拍板①②）：
  · 阴跌段（等权锚 eq_anchor state=bear_slow）影子榜启用防御因子加成；
    GO 线 = 阴跌段 defensive 榜 vs base 榜对全池等权超额差 ≥ +0.10pt/日 且
    段聚类 P≤0.05；95%CI 上界 ≤0 ⇒ KILL；样本闸 ≥10 个**前瞻**阴跌段
    （历史段仅 in-sample 定参，见 scripts/insample_defensive_params.py 与
    reports/insample_defensive_params_20261025.md）。
  · 判定脚本：scripts/defensive_edge_check.py（前瞻段才判，本脚本只积累数据）。

定参结果（in-sample 2010 起 96 段阴跌，代理动量框架，★ 只定参不判定）：
  · 有效形式 = 银行组乘法加成：δ=0.80 段均值差 +0.105pt/日（峰值；
    δ=0.50 → +0.046，δ=1.20 → +0.092 回落）。
  · 低波因子被否定（mult/quota 全形式 ≈0，未启用）；席位注入弱于乘法。
  · 峰值 CI [−0.046,+0.282] 跨 0、正差段占比 47% —— 过拟合风险如实登记，
    真伪由前瞻段判。

口径：
  · 分数源 = ranking_live 精算 total（与 shadow_decay_ranking 的 base 同源）；
  · 防御组 = 名称含「银行」（与 H4 defensive_attr 同口径，零新增查询）；
  · gate = eq_anchor 最新态 bear_slow（非阴跌段加成=0 ⇒ 榜=base，保持每日
    连续落库，判定脚本按日对齐无需特判）；
  · 与衰减榜（base/zero/grad）共表 shadow_rank_daily，本脚本只动
    variant='defensive' 的行（★ 不整日 DELETE——那会清掉衰减榜）。

用法：
  python scripts/shadow_defensive_ranking.py             # dry-run
  python scripts/shadow_defensive_ranking.py --apply     # 落库（日批 task 共用 run()）
================================================================================
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

_BJ = timezone(timedelta(hours=8))

# ── 定参常量（in-sample 2026-10-25；改动须在方案 §8 留痕并重走定参） ──
VARIANT_DEFENSIVE = "defensive"
DEFENSIVE_GROUP = "bank"       # 银行组（低波 in-sample 无效，未启用）
DEFENSIVE_DELTA = 0.80         # 阴跌段防御组总分 ×(1+δ)


def _init_table() -> None:
    from app.scoring.decay import VARIANT_BASE  # noqa: F401  （确认单一事实源可导入）
    from app.database import db
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


def run(top: int = 50, apply: bool = True) -> str:
    """防御影子榜主逻辑（CLI 与日批 task_shadow_defensive 共用）。返回摘要。"""
    _init_table()
    from app.scoring.live_ranking import load
    from app.backtest.eq_anchor import latest_state, STATE_BEAR_SLOW
    st = load(limit=1000, use_cache=False)
    data, rank_date = (st.get("data") or []), st.get("date")
    if not data or not rank_date:
        print("ranking_live 为空，无法产出防御影子榜（等日批 rank_live 先产出）")
        return "ranking_live 为空，跳过（等日批 rank_live 先产出）"

    eq = latest_state()
    eq_state = eq.get("state") or "?"
    eq_date = str(eq.get("anchor_date") or "")
    gate = eq_state == STATE_BEAR_SLOW
    if eq_date and rank_date and eq_date < str(rank_date):
        print(f"⚠️ 等权锚态滞后（{eq_date} < 榜日 {rank_date}），仍按 {eq_state} 门控")

    recs = []
    n_boost = 0
    for r in data:
        total = r.get("total_score") or 0
        is_def = "银行" in (r.get("name") or "")
        boosted = bool(gate and is_def)
        if boosted:
            total = total * (1.0 + DEFENSIVE_DELTA)
            n_boost += 1
        recs.append({"code": r["code"], "name": r.get("name") or "",
                     "score": round(total, 1), "boosted": boosted,
                     "dims": r.get("dimensions") or {}})

    recs.sort(key=lambda x: x["score"], reverse=True)
    tops = recs[:top]

    # 与 base 榜的重叠（当日 shadow_decay_ranking 的 base=生产 top50；此处近似
    # 用「未加成排序的前 top」对照，读取库中 base 行更准但仅展示用途）
    no_boost_order = sorted(recs, key=lambda x: (x["score"] if not x["boosted"]
                                                 else x["score"] / (1 + DEFENSIVE_DELTA)),
                            reverse=True)
    base_codes = {r["code"] for r in no_boost_order[:top]}
    overlap = len(base_codes & {r["code"] for r in tops})

    print("=" * 74)
    print(f"防御影子榜  date={rank_date}  等权锚={eq_state}（{eq_date}）  "
          f"gate={'ON' if gate else 'OFF'}")
    print(f"加成：{DEFENSIVE_GROUP} 组 ×{1 + DEFENSIVE_DELTA:.2f}，"
          f"池内命中 {n_boost} 只；top{top} 与 base 重叠 {overlap}/{top}")
    print("=" * 74)
    for i, r in enumerate(tops[:15]):
        mark = " ←防御" if r["boosted"] else ""
        print(f"{i + 1:>3} {r['code']:<7}{r['name'][:8]:<10}{r['score']:>6.1f}{mark}")

    if not apply:
        print("\n[dry-run] 未落库。加 --apply 写入 shadow_rank_daily(variant=defensive)。")
        return f"dry-run：gate={'ON' if gate else 'OFF'}，加成 {n_boost} 只，未落库"

    from app.database import db
    meta = {"_defensive": {"gate": gate, "eq_state": eq_state, "eq_date": eq_date,
                           "group": DEFENSIVE_GROUP, "delta": DEFENSIVE_DELTA}}
    payload = []
    for i, r in enumerate(tops):
        dims = dict(r["dims"])
        dims.update(meta)
        payload.append({
            "rank_date": str(rank_date), "variant": VARIANT_DEFENSIVE,
            "code": r["code"], "name": r["name"], "rank_pos": i + 1,
            "total_score": r["score"], "decay_age": None,
            "dimensions_json": json.dumps(dims, ensure_ascii=False),
        })
    # ★ 只清本 variant 当日行（衰减榜 base/zero/grad 共表，整日 DELETE 会误删）
    db.execute("DELETE FROM shadow_rank_daily WHERE rank_date = %s AND variant = %s",
               (str(rank_date), VARIANT_DEFENSIVE))
    n = db.upsert_many("shadow_rank_daily", payload, ["rank_date", "variant", "code"])
    summary = (f"防御影子榜: {n} 行（gate={'ON' if gate else 'OFF'}，"
               f"{eq_state}，加成 {n_boost} 只，重叠 {overlap}/{top}）")
    print(f"\n[apply] {summary}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    args = ap.parse_args()
    run(top=args.top, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
