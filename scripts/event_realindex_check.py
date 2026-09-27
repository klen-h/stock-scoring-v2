#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · 可执行性终验】真实宽基指数（市值加权）能否吃到 E2 的 edge？
================================================================================
背景：
  `event_executability_check.py` §8.2 用沪深300 代表市值加权 ⇒ "不可执行"；
  `event_index_check.py` §8.3 用 zzshare 估算市值加权 ⇒ 深市/创业板仍显著（可执行）。
  但 §8.3 用的是**全板块**（含全部创业板股），而真实 ETF 跟踪的指数**只含 top N 成分股**
  ⇒ 本脚本用**真实指数日线**做终验。

数据：腾讯 fqkline（本机外网限制下的唯一可用源）
  · 东财 push2his 在本机不通（`RemoteDisconnected`，开 VPN 亦不通）
  · 腾讯单次上限 800 根 ⇒ **按 `end` 递减分段翻页**拉全历史
  · 缓存到 `data/idx_daily.json`（避免重复请求）

标的（可直接买 ETF）：创业板指 159915 ｜ 中证500 510500 ｜ 中证1000 512100
                      ｜ 科创50 588000 ｜ 沪深300 510300（对照）

━━━━━━━━━━━━━━━━━━ 预登记判定 ━━━━━━━━━━━━━━━━━━
  口径：事件日 T → **T+1 开盘买** → T+20 收盘卖，未扣成本。
  (1) 某指数 edge >= +1.5pp 且 bootstrap P < 0.05 ⇒ **该宽基可执行**
  (2) 全部 < +0.5pp ⇒ 市值加权吃不到 ⇒ 无可用标的
  (3) 与 §8.3 的"板块全量市值加权"对比 ⇒ 量化「top N 成分」的影响

用法：python scripts/event_realindex_check.py [--refresh]
================================================================================
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX_CACHE = os.path.join(ROOT, "data", "idx_daily.json")
EVENT_JSON = os.path.join(ROOT, "data", "event_history.json")

E2_UP_RATIO = 0.90
E2_LU_RATIO = 0.020
H = 20
B_BOOT = 10000
START = "2004-01-01"

INDEXES = [
    ("sz399006", "创业板指", "159915 创业板ETF"),
    ("sh000905", "中证500", "510500 500ETF"),
    ("sh000852", "中证1000", "512100 1000ETF"),
    ("sh000688", "科创50", "588000 科创50ETF"),
    ("sh000300", "沪深300", "510300 300ETF"),
]

_TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
_SESSION = requests.Session()
_SESSION.trust_env = False       # 本机系统代理不可用（见 §8.3 注释）


def fetch_page(code: str, end: str, count: int = 800, retries: int = 3):
    """腾讯单页：返回 <= count 根（`end` 之前的最近 N 根，升序）。"""
    params = {"param": f"{code},day,,{end},{count},qfq"}
    for i in range(retries):
        try:
            r = _SESSION.get(_TX_URL, params=params, headers=_HEADERS, timeout=25)
            if r.status_code == 501:
                print(f"   [{code}] 腾讯 WAF 拦截(501)，冷却 30s")
                time.sleep(30)
                continue
            r.raise_for_status()
            raw = ((r.json() or {}).get("data") or {}).get(code) or {}
            items = raw.get("day") or raw.get("qfqday") or []
            out = []
            for it in items:
                if len(it) < 6:
                    continue
                try:
                    out.append({"date": it[0], "open": float(it[1]), "close": float(it[2]),
                                "high": float(it[3]), "low": float(it[4]),
                                "volume": float(it[5])})
                except (ValueError, TypeError):
                    continue
            return out
        except Exception as e:
            print(f"   [{code}] 失败({i+1}/{retries}): {e}")
            time.sleep(3)
    return []


def fetch_all(code: str, start: str = START):
    """分段翻页拉全历史（`end` 递减）。"""
    bars = {}
    end = datetime.now().strftime("%Y-%m-%d")
    for k in range(14):
        items = fetch_page(code, end)
        if not items:
            break
        for it in items:
            bars[it["date"]] = it
        earliest = min(it["date"] for it in items)
        print(f"   第 {k+1} 页: {earliest} ~ {max(it['date'] for it in items)}"
              f"（累计 {len(bars)} 根）")
        if earliest <= start or len(items) < 200:
            break
        end = (datetime.strptime(earliest, "%Y-%m-%d")
               - timedelta(days=1)).strftime("%Y-%m-%d")
        time.sleep(1.2)
    return [bars[d] for d in sorted(bars)]


def load_indexes(refresh: bool = False):
    cache = {}
    if not refresh and os.path.exists(IDX_CACHE):
        try:
            with open(IDX_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    changed = False
    for code, name, _etf in INDEXES:
        if code in cache and len(cache[code]) > 500:
            continue
        print(f"拉取 {name}（{code}）…")
        bars = fetch_all(code)
        if bars:
            cache[code] = bars
            changed = True
            print(f"   OK {len(bars)} 根  {bars[0]['date']} ~ {bars[-1]['date']}")
        else:
            print(f"   [warn] {name} 无数据")
    if changed:
        with open(IDX_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        print(f"已缓存 -> {IDX_CACHE}")
    return cache


def load_e2():
    with open(EVENT_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for d in sorted(raw):
        v = raw[d]
        denom = (v.get("up") or 0) + (v.get("down") or 0)
        if v["up_ratio"] >= E2_UP_RATIO and denom > 0 \
                and v["limit_up"] / denom >= E2_LU_RATIO:
            out.append(d)
    return out


def series_of(bars):
    ds = [b["date"] for b in bars]
    rets, gaps = [], []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["close"] if i > 0 else b["close"]
        rets.append((b["close"] / pc - 1) * 100 if pc else 0.0)
        gaps.append((b["open"] / pc - 1) * 100 if pc else 0.0)
    return ds, rets, gaps


def fwd(rets, gaps, i, n=H):
    if i + 1 + n > len(rets):
        return None
    e = 1 + (gaps[i + 1] or 0) / 100.0
    if e <= 0.05:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        cum *= (1 + rets[k] / 100.0)
    return (cum / e - 1) * 100


def block_p(vals, base, B=B_BOOT, seed=42, chunk=2000):
    a = np.asarray([v for v in vals if v is not None], dtype=float)
    n = len(a)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)
    cnt = tot = 0
    done = 0
    while done < B:
        m = min(chunk, B - done)
        means = a[rng.integers(0, n, size=(m, n))].mean(axis=1)
        cnt += int(np.sum(means <= base))
        tot += m
        done += m
    return cnt / tot


def _stat(v):
    v = [x for x in v if x is not None]
    if not v:
        return None
    return {"n": len(v), "mean": sum(v) / len(v),
            "win": sum(1 for x in v if x > 0) / len(v) * 100}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    cache = load_indexes(refresh=args.refresh)
    e2 = load_e2()
    print(f"\nE2 事件 {len(e2)} 天")

    print(f"\n{'='*112}")
    print(f"【真实宽基指数 E2 后 T+{H}】T+1 开盘买 -> T+{H} 收盘卖，未扣成本")
    print(f"{'='*112}")
    print(f"{'指数':<10}{'ETF':<18}{'数据起':<12}{'事件n':>7}{'事件T+20':>11}"
          f"{'基准T+20':>11}{'差':>10}{'胜率':>8}{'P':>9}  判定")
    results = []
    for code, name, etf in INDEXES:
        bars = cache.get(code) or []
        if len(bars) < 300:
            print(f"{name:<10}{etf:<18}{'（无数据）':>12}")
            continue
        ds, rets, gaps = series_of(bars)
        di = {d: i for i, d in enumerate(ds)}
        ev = _stat([fwd(rets, gaps, di[d]) for d in e2 if d in di])
        ba = _stat([fwd(rets, gaps, i) for i in range(len(ds))])
        if not ev or not ba:
            continue
        p = block_p([fwd(rets, gaps, di[d]) for d in e2 if d in di], ba["mean"])
        diff = ev["mean"] - ba["mean"]
        v = ("可执行" if (diff >= 1.5 and p is not None and p < 0.05)
             else ("不可执行" if diff < 0.5 else "弱"))
        results.append((name, ev["n"], diff, p, v))
        print(f"{name:<10}{etf:<18}{ds[0]:<12}{ev['n']:>7}{ev['mean']:>+10.2f}%"
              f"{ba['mean']:>+10.2f}%{diff:>+9.2f}pp{ev['win']:>7.1f}%"
              f"{(p if p is not None else 1):>9.4f}  {v}")

    print(f"\n  对照（§8.3 zzshare 估算·板块全量市值加权）：")
    print(f"    创业板 +2.43pp ｜ 深市 +1.90pp ｜ 沪市 +0.44pp ｜ 全市场 +0.90pp")

    print(f"\n{'='*112}")
    print("【预登记判定】")
    print(f"{'='*112}")
    ok = [r for r in results if r[4] == "可执行"]
    if ok:
        for r in ok:
            print(f"  ★ 可执行：{r[0]}  {r[2]:+.2f}pp（P={r[3]:.4f}，事件 {r[1]} 天）")
    else:
        print("  ★ 没有任何真实宽基指数达到 +1.5pp 显著门槛 ⇒ 市值加权吃不到 E2 的 edge。")
    print(f"\n  注：腾讯 fqkline 分段拉取（单页上限 800 根）；未扣成本 0.3%。")


if __name__ == "__main__":
    main()
