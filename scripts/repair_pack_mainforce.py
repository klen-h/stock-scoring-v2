# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】修复指标包（indicators-pack.json.gz）里**缺失的主力行为标签**
================================================================================

【为什么需要这个脚本】
  2026-10-31 用户报「评分排行榜的主力行为整列全是 `-`」。排查结论：

    · Supabase `mainforce_state` **完全正常**（10-30 有 2853 只，含 644 吸筹 /
      44 出货，`flow5_amt` 零空值）——数据源没坏；
    · 但产包链路 `scripts/update_backend_pack_incremental.py:323-325` 对
      `mainforce` **只有"继承"，没有任何"计算"**：
          p = prev_ind.get(code) or {}
          if p.get("mainforce"):
              ind["mainforce"] = p["mainforce"]      # 唯一来源：上一份包
    · 而**唯一会计算它**的 `generate_backend_pack.py::compute_mainforce_overlay`
      在 `backend-pack.yml` 里**从未被调用**（workflow 只跑
      `daily_batch --tasks mainflow` 与 `rebuild_pack_struct.py`）。

  ⇒ `mainforce` 成了**单向棘轮**：某一份包里丢了，之后每天继承"没有"，
    **永远不恢复**（没有任何路径会重新计算）。
    这是本项目的固定失败模式：**静默降级 → 被固化 → 再也不会自愈**。

【本脚本做什么】
  不改产包链路、不重算 overlay —— 直接**把已经算好的结果搬过去**：

    `mainforce_state` 表（`compute_mainforce_overlay` 的输出落库）
              ↓  按 code 匹配
    `indicators-pack.json.gz` 的每只 `indicators[code]["mainforce"]`

  字段一致性由 `app.mainforce.state.load_latest()` 保证——它是项目约定的
  **"唯一数据入口"**（`phase_cn` 的补齐、`chip_json` 的解析都在那里统一做），
  本脚本不自己写第二套映射，避免再次分叉。

【成本】
  读 `mainforce_state` 最新一日 ≈ 2853 行 ≈ **0.4 MB egress**（一次性）。
  本地包解析在内存完成，无网络。

【用法】
  # 1) 预览（默认，只统计不写文件）——先看数字合不合理
  python scripts/repair_pack_mainforce.py

  # 2) 真写（写到 .repaired.gz，**不覆盖原包**，确认后再手工替换）
  python scripts/repair_pack_mainforce.py --write

  # 3) 指定包路径（例如从 GitHub Pages 下来的线上包）
  python scripts/repair_pack_mainforce.py --pack /tmp/indicators-pack.json.gz

【⚠️ 发布前必须注意（脚本不代劳）】
  前端 `ensureIndicatorsPack()` 只在 `klineDate !== indDate` 时才重新下载包，
  而本脚本**故意不改包的 `date`**（那是 K 线口径，改了会让 `seriesIsFresh`
  误判、评分回退现算）。⇒ 替换包之后，用户浏览器仍可能用 IndexedDB 里的旧包。
  发布时的正确做法（二选一）：
    · 让 K 线包也随当日更新（`date` 自然变化，前端会自动重下）；
    · 或临时把包的 `date` 改成一个新值并确保 `_series` 也同步新（仅在确认
      包里 `_series` 已是新数据时才可这么做）。
================================================================================
"""

import argparse
import gzip
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# .env 必须在 import app.database 之前加载（DATABASE_URL 决定 SQLite/PG）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    load_dotenv(os.path.join(BACKEND, ".env"))
except Exception:
    pass

DEFAULT_PACK = os.path.join(ROOT, "data", "kline", "indicators-pack.json.gz")


def _log(msg):
    print(msg, flush=True)


def load_pack(path):
    """读指标包 → (dict, indicators_map)。兼容 {'indicators': {...}} 与裸映射两种结构。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"包不存在: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict) and isinstance(d.get("indicators"), dict):
        return d, d["indicators"]
    if isinstance(d, dict):
        return {"indicators": d}, d
    raise ValueError(f"无法识别的包结构: {type(d).__name__}")


def has_mainforce(path, sample_chars=1 << 20):
    """抽样快判：包内是否已含 `mainforce` 字段（只读 gz 前 ~1MB，毫秒级）。

    ★ 2026-10-31：本函数是**日常零成本**的关键——产包链路每天都会调用本脚本，
      99% 的日子包里字段是好的，不该为此解压 11MB 并重写整包。
      整包缺失时（2026-10-28 起真实发生过），前 1MB 里一个 `"mainforce"` 都不会有，
      所以抽样足够可靠。
    ★ fail-open：读失败一律当"已有"⇒ 不修补、不阻断产包（宁可漏修，不可误重写）。
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            head = f.read(sample_chars)
        return '"mainforce"' in head
    except Exception as e:
        _log(f"      ⚠️ 抽样检测失败（按『已有』处理，不修补）: {str(e)[:70]}")
        return True


def main():
    ap = argparse.ArgumentParser(description="修复指标包里缺失的主力行为标签")
    ap.add_argument("--pack", default=DEFAULT_PACK, help="指标包路径（默认本地 data/kline）")
    ap.add_argument("--out", default="", help="输出路径（--write 时；默认 <pack>.repaired.gz）")
    ap.add_argument("--write", action="store_true", help="真正写文件（默认只预览）")
    ap.add_argument("--force", action="store_true",
                    help="跳过『已有 mainforce 就退出』的快判，强制重新回填")
    ap.add_argument("--limit-codes", type=int, default=0,
                    help="仅处理前 N 只（调试用，0=全部）")
    args = ap.parse_args()

    _log("=" * 78)

    # ★ 快判放在 load_pack **之前**：命中时连 11MB 的解压都省掉。
    if not args.force and not args.limit_codes and has_mainforce(args.pack):
        _log(f"      ✅ 包内已有 mainforce 字段，跳过修补（--force 可强制）")
        _log(f"      {args.pack}")
        _log("=" * 78)
        return 0

    _log("[1/5] 读包")
    pack, ind_map = load_pack(args.pack)
    codes = list(ind_map.keys())
    if args.limit_codes:
        codes = codes[:args.limit_codes]
    pack_date = pack.get("date") or "(无)"
    _log(f"      {args.pack}")
    _log(f"      包日期={pack_date}  条目={len(ind_map)}  本次处理={len(codes)}")

    have_mf = sum(1 for c in codes
                  if isinstance(ind_map.get(c), dict) and ind_map[c].get("mainforce"))
    _log(f"      已有 mainforce 字段: {have_mf} 只"
         f"（{'会被本次结果覆盖' if have_mf else '当前全部缺失'}）")

    _log("[2/5] 读 mainforce_state（Supabase）")
    from app.database import db
    from app.mainforce.state import load_latest_for_pack
    row = db.fetch_one("SELECT MAX(date) AS d, COUNT(*) AS n FROM mainforce_state")
    _log(f"      表最新日={row.get('d')}  表总行数={row.get('n')}  "
         f"后端={'PostgreSQL' if db.is_postgres else 'SQLite'}")

    _log(f"[3/5] 按 code 匹配（{len(codes)} 只，走 load_latest_for_pack 单次 IN 查询）")
    mf_map = load_latest_for_pack(codes)
    hit = [c for c in codes if c in mf_map]
    miss = [c for c in codes if c not in mf_map]
    _log(f"      命中 {len(hit)} / {len(codes)}"
         f"（{len(hit) * 100.0 / max(len(codes), 1):.1f}%）  未命中 {len(miss)}")

    _log("[4/5] 补后 signal 分布（预测）")
    sig = Counter()
    phase = Counter()
    n_flow_null = 0
    for c in hit:
        raw = mf_map[c]
        sig[raw.get("signal") or "(NULL)"] += 1
        phase[raw.get("phase") or "(?)"] += 1
        if raw.get("flow5_amt") is None:
            n_flow_null += 1
    _log(f"      signal: {dict(sig.most_common(8))}")
    _log(f"      phase : {dict(phase.most_common(8))}")
    _log(f"      flow5_amt 为空: {n_flow_null} 只"
         f"（为空 ⇒ 吸筹/出货判据先天不成立，属正常退化）")

    # 覆盖率护栏（与产包脚本同思路）：低于阈值说明数据源本身有问题，拒绝写包
    n_signal = sum(v for k, v in sig.items() if k != "(NULL)")
    ratio = n_signal * 100.0 / max(len(hit), 1)
    _log(f"      带信号比例: {ratio:.1f}%（基准：mainforce_state 当日约 24%）")

    _log("[5/5] 结论")
    if not args.write:
        _log("      ✅ 预览模式：**未写任何文件**")
        _log("      确认数字合理后，加 --write 生成 .repaired.gz")
        _log("=" * 78)
        return 0

    if ratio < 5.0:
        _log(f"      ❌ 带信号比例仅 {ratio:.1f}%（<5%）——数据源异常，拒绝写包")
        _log("=" * 78)
        return 2

    # 真写：只动 mainforce 字段，其余原样保留
    n_applied = 0
    for c in hit:
        cur = ind_map.get(c)
        if not isinstance(cur, dict):
            continue
        cur["mainforce"] = mf_map[c]
        n_applied += 1

    out_path = args.out or (args.pack + ".repaired.gz")
    tmp = out_path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)
    _log(f"      ✅ 已写 {n_applied} 只 → {out_path}"
         f"（{os.path.getsize(out_path) / 1048576:.2f} MB）")
    _log("      ⚠️ 未覆盖原包；发布前请阅读脚本头部的『发布前必须注意』")
    _log("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
