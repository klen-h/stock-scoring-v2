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

口径：
  · base  = 生产榜单（ranking_live 精算结果原样，total_score 排序）。
  · decay = 同一批精算结果，但「公告后 ≤ N 天」的股票成长分×α、质量分×α，
            复刻 engine._combine 重新加权算总分，再排序。
  只读 ranking_live + get_finance_batch + get_regime_weights，零评分重算、零网络请求。

用法：
  python scripts/shadow_decay_ranking.py                # dry-run，N=25, α=0.0
  python scripts/shadow_decay_ranking.py --apply        # 落库
  python scripts/shadow_decay_ranking.py --days 20 --alpha 0.5 --top 30
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

_BJ = timezone(timedelta(hours=8))

# 中文维度名 → get_regime_weights 英文键
DIM_TO_KEY = {"技术面": "technical", "资金面": "capital", "基本面": "fundamental",
              "成长": "growth", "质量": "quality"}
DECAY_DIMS = ("成长", "质量")   # 只衰减这两维（季频财报因子）


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
    """当前市况的五维权重（英文键）；拿不到则用引擎默认权重兜底。

    ★ 优先级：regime 内存缓存 → market_regime_history 最新行 → 默认权重。
      本地/日批独立进程的 regime 缓存通常为空，必须回退历史表（与
      live_ranking.compute_full_ranking 先 _sync_regime_weights 同理），
      否则 base 与 decay 会各用一套权重、对比失真。
    """
    state = ""
    try:
        from app.backtest.market_regime import get_regime_cache, get_regime_weights
        state = (get_regime_cache() or {}).get("state") or ""
        if not state:
            row = db.fetch_one(
                "SELECT state FROM market_regime_history ORDER BY date DESC LIMIT 1")
            state = (row or {}).get("state") or ""
        w = get_regime_weights(state) if state else None
        if w:
            return state, w
    except Exception as e:
        print(f"[shadow] 权重获取失败: {e}")
    from app.scoring.engine import ScoreEngine
    return state, dict(ScoreEngine.DEFAULT_WEIGHTS)


def recompute_total(dims, weights, alpha, age, days):
    """复刻 engine._combine：加权求和、缺失维度按比例分摊权重。

    衰减：age ≤ days 时，成长/质量分 ×alpha（α=0 = 打 0 分，维度仍占权重）。
    返回衰减后总分；dims 无有效维度则 None。
    """
    d = dict(dims or {})
    if age is not None and age <= days:
        for k in DECAY_DIMS:
            if k in d and d[k] is not None:
                d[k] = d[k] * alpha
    valid = []
    for name, score in d.items():
        if score is None:
            continue
        w = weights.get(DIM_TO_KEY.get(name, ""))
        if w is None or w <= 0:
            continue
        valid.append((score, w))
    if not valid:
        return None
    w_sum = sum(w for _, w in valid)
    return round(sum(s * w / w_sum for s, w in valid), 1)


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


def run(days: int = 25, alpha: float = 0.0, top: int = 30,
        apply: bool = True) -> str:
    """旁路衰减榜单主逻辑（CLI 与日批 task_shadow_rank 共用）。返回摘要字符串。"""
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
    print(f"旁路衰减榜单  date={rank_date}  市况={state or '未知'}")
    print(f"权重 = {weights}")
    print(f"衰减规则: 公告后 ≤{days} 天，{'/'.join(DECAY_DIMS)} 分 ×{alpha}")
    print("=" * 74)

    # 权重口径校验：不衰减重算应 ≈ ranking_live 总分（否则 decay 口径不可信）
    _check_n = min(50, len(data))
    _diff_sum, _cnt = 0.0, 0
    for r in data[:_check_n]:
        _rt = recompute_total(r.get("dimensions") or {}, weights, 1.0, None, days)
        _lt = r.get("total_score")
        if _rt is not None and _lt is not None:
            _diff_sum += abs(_rt - _lt)
            _cnt += 1
    if _cnt:
        _avg_diff = _diff_sum / _cnt
        print(f"权重口径校验：不衰减重算 vs ranking_live 总分，前{_cnt}只平均差"
              f" = {_avg_diff:.2f} 分" + ("  OK" if _avg_diff < 0.5
                                        else "  ⚠️ 口径不一致，decay 不可信"))
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
        decay_total = recompute_total(dims, weights, alpha, age, days)
        recs.append({
            "code": r["code"], "name": r.get("name") or "",
            "base": r.get("total_score") or 0,   # 生产权威 total（ranking_live）
            "decay": decay_total,
            "age": age,
            "dims": dims,
            "price": prices.get(r["code"]),
        })

    base_rank = sorted(recs, key=lambda x: x["base"], reverse=True)
    decay_rank = sorted(recs, key=lambda x: (x["decay"] is not None, x["decay"] or 0),
                        reverse=True)
    base_top = base_rank[:top]
    decay_top = decay_rank[:top]
    base_codes = {r["code"] for r in base_top}
    decay_codes = {r["code"] for r in decay_top}

    print(f"\n生产 top{top} vs 衰减 top{top}  重叠 {len(base_codes & decay_codes)}/{top}")
    print(f"衰减后新进榜（不在生产 top）: {sorted(decay_codes - base_codes)}")
    print(f"衰减后掉榜（生产 top 被挤掉）: {sorted(base_codes - decay_codes)}")

    print(f"\n{'生产榜':<8}{'总分':>7} | {'衰减榜':<8}{'衰减分':>7}{'age':>5}")
    print("-" * 40)
    for i in range(top):
        b = base_top[i]
        d = decay_top[i]
        age_b = f"{b['age']}d" if b["age"] is not None else "无"
        age_d = f"{d['age']}d" if d["age"] is not None else "无"
        print(f"{b['code']:<8}{b['base']:>7.1f} | {d['code']:<8}"
              f"{str(d['decay'] if d['decay'] is not None else '-'):>7}{age_d:>5}")
        if b["code"] != d["code"]:
            print(f"{'':>8}{'':>7} |   (↑ {d['name']}, age {age_d})")

    if not apply:
        print("\n[dry-run] 未落库。加 --apply 写入 shadow_rank_daily。")
        return f"dry-run：重叠 {len(base_codes & decay_codes)}/{top}，未落库"

    payload = []
    for i, r in enumerate(base_top):
        payload.append({
            "rank_date": rank_date, "variant": "base", "code": r["code"],
            "name": r["name"], "rank_pos": i + 1,
            "total_score": r["base"], "decay_age": r["age"],
            "snapshot_price": r["price"],
            "dimensions_json": json.dumps(r["dims"], ensure_ascii=False),
        })
    for i, r in enumerate(decay_top):
        payload.append({
            "rank_date": rank_date, "variant": "decay", "code": r["code"],
            "name": r["name"], "rank_pos": i + 1,
            "total_score": r["decay"], "decay_age": r["age"],
            "snapshot_price": r["price"],
            "dimensions_json": json.dumps(r["dims"], ensure_ascii=False),
        })
    db.execute("DELETE FROM shadow_rank_daily WHERE rank_date = %s", (rank_date,))
    n = db.upsert_many("shadow_rank_daily", payload, ["rank_date", "variant", "code"])
    summary = (f"旁路衰减榜: {n} 行（base {len(base_top)} + decay {len(decay_top)}，"
               f"重叠 {len(base_codes & decay_codes)}/{top}，市况 {state or '?'}）")
    print(f"\n[apply] {summary}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=25, help="公告后衰减窗口（天）")
    ap.add_argument("--alpha", type=float, default=0.0, help="衰减系数（0=彻底剔除）")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--apply", action="store_true", help="落库（默认 dry-run 仅打印）")
    args = ap.parse_args()
    run(days=args.days, alpha=args.alpha, top=args.top, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
