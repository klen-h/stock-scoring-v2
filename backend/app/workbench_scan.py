# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】工作台盘中「变化检测器」——先证明信息量，再决定要不要上 LLM
================================================================================

背景（用户 2026-10-21）：
  想在盘中每 30 分钟让 LLM 分析一次工作台。但工作台大半数据是**日频**的
  （mainforce / 评分快照 / 教练卡 / 行业映射），半小时一报会有大量复读；
  且 LLM 有额度与熔断成本。⇒ 先上**纯规则、零 LLM** 的变化检测器跑几天，
  用数据回答："8 个采样点里，真正值得说一句的有几个？"

设计（与用户讨论结论一致）：
  ① **零外部增量**：宽度/板块/个股全部读 `tencent._cache` 内存行情；
     只有 3 个核心指数走 1 次腾讯请求（8 次/交易日，可忽略）。
  ② **零 LLM**：纯规则打分；`score >= WORTH_SCORE` 记为 worth（=有实质变化）。
     ★ 2026-10-27（用户确认）：在 worth 之上再算**方向** `bias = Σ(方向×分值)`，
       输出 verdict = positive / negative / neutral / none ⇒ 前端显示
       「正影响 / 反影响 / 中性变化 / 无明显变化」；方向不明的"放量、换主线、快讯条数"只计强度。
  ③ **只采盘中变量**：指数、宽度（涨跌家数/涨停跌停）、板块强弱、量能、快讯；
     日频项一律不进本检测器（它们半小时内不会变）。
  ④ 固定 8 个采样点（见 `SLOTS`），错过的点在宽限内补采，并在 diff 里记
     `trading_min`（**交易分钟**，不是墙钟分钟 —— 跨午休时二者差 90 分钟）。
  ⑤ 幂等：主键 `key = date|slot`，已有行则跳过（不覆盖）。
  ⑥ 时点诚实：`from_snapshot=True`（行情缓存其实是收盘快照）时不判分，
     理由标 `stale` —— 本项目踩过"把快照当实时"的坑太多次。

⚠️ 所有阈值都是**经验初值**，不是结论。用 `summary()` 的 `reason_counts`
   校准：某个 tag 天天触发 ⇒ 阈值太松；从不触发 ⇒ 太紧或该维度本就无信息。

用法：
  - 调度器：`flash/scheduler.workbench_scan_loop()` 交易时段每 60s 检查
  - 只读预览：GET /api/system/workbench-scan/preview（不写库）
  - 当日明细：GET /api/system/workbench-scan?date=YYYY-MM-DD
  - 多日统计：GET /api/system/workbench-scan/summary?days=7
  - 命令行：`python scripts/workbench_scan_probe.py --preview|--summary`
================================================================================
"""
import json
from datetime import timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.database import db
from app.flash.rules import beijing_now

_BJ = timezone(timedelta(hours=8))

# 采样点：上午 4 个、下午 4 个（11:15 是上午最后一档；13:15 起下午）
SLOTS = ("09:45", "10:15", "10:45", "11:15", "13:15", "13:45", "14:15", "14:45")

GRACE_MIN = 35          # 错过采样点的补采宽限（分钟，墙钟）
WORTH_SCORE = 40        # 达到该分 = "这半小时有实质变化"（强度，不含方向）
# ★★ 2026-10-27（用户确认）：把"有料"升级为**正/反影响**（强度 + 方向两维）
#   · bias = Σ(方向 × 分值)，只累加能判方向的规则（指数/宽度/涨停/跌停）；
#   · |bias| >= BIAS_MIN ⇒ 正影响 / 反影响；否则"中性变化"（确实在动、方向不明）；
#   · score < WORTH_SCORE ⇒ 无明显变化。
BIAS_MIN = 20           # 方向判定门槛（≈ 两条中等信号同向）
NOTIFY_BIAS = 30        # 浏览器推送门槛（比判定更严，避免半小时一条噪音）
NOTIFY_DAILY_MAX = 4    # 每天最多推几条
KEEP_DAYS = 120         # 保留天数（8 行/日 × 2KB ≈ 2MB/120 日，可忽略）

# 交易时段（分钟）：09:30-11:30 / 13:00-15:00
_SESSIONS = ((570, 690), (780, 900))

# 核心指数（与 `intraday_alerts._INDEX_WATCH` 同集；一次请求取全）
_INDEX_WATCH = (("000001", "上证指数"), ("399006", "创业板指"), ("000688", "科创50"))

# 高信号快讯关键词（"这半小时有没有事"的粗筛，不做语义判断）
_NEWS_KEYS = ("证监会", "央行", "降准", "降息", "加息", "暂停", "关税", "制裁",
              "突发", "暴跌", "暴涨", "美联储", "国务院", "监管")

_TABLE_READY = False


def _ensure_table() -> None:
    """建表（幂等）。★ 数值列一律 TEXT —— SQLite / PostgreSQL 双库零风险。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS workbench_scan (
            key TEXT PRIMARY KEY,
            date TEXT, slot TEXT, as_of TEXT,
            trading_min TEXT,
            score TEXT, worth TEXT, bias TEXT, verdict TEXT, reasons TEXT,
            snapshot_json TEXT, diff_json TEXT,
            created_at TEXT, notified_at TEXT
        )
    """)
    # 老库补列（CREATE IF NOT EXISTS 不会给已存在的表加列）
    for _col in ("bias TEXT", "verdict TEXT", "notified_at TEXT"):
        try:
            db.execute(f"ALTER TABLE workbench_scan ADD COLUMN IF NOT EXISTS {_col}")
        except Exception:
            try:
                db.execute(f"ALTER TABLE workbench_scan ADD COLUMN {_col}")
            except Exception as e2:
                print(f"[workbench_scan] add column {_col} failed: {str(e2)[:80]}")   # ASCII
    _TABLE_READY = True


# ══════════════════════════════════════════════════════════════════════════
#  采集（全部内存 / 1 次指数请求）
# ══════════════════════════════════════════════════════════════════════════

def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _collect_breadth() -> Dict:
    """市场宽度（纯内存，零网络）。口径与 `market_overview.stats` 一致。"""
    from app.routers.market import _breadth_now
    b = _breadth_now()
    if not b:
        return {}
    total = int(b.get("total") or 0)
    up = int(b.get("up") or 0)
    down = int(b.get("down") or 0)
    return {
        "total": total, "up": up, "down": down,
        "up_ratio": round(up / max(up + down, 1), 3),
        "limit_up": int(b.get("limit_up") or 0),
        "limit_down": int(b.get("limit_down") or 0),
        # `_breadth_now().amount` 单位是**元**
        "amount_yi": round(_num(b.get("amount")) / 1e8, 0),
    }


def _collect_sectors() -> Dict:
    """板块强弱（内存行情 + 落库行业映射，零外部请求）。

    口径：`industry_amount_share()` 的 `avg_change_pct` = 行业内**等权平均**
    涨跌幅（不是市值加权），与前端"板块"口径一致；只保留成分 ≥ 8 只的行业
    （小样本行业一个票就能拉出 ±5%，会把榜单污染成噪音）。
    """
    try:
        from app.sector_industry import industry_amount_share
        d = industry_amount_share(top=9999)
    except Exception as e:
        print(f"[workbench_scan] sector collect failed: {e}")      # ASCII
        return {"top": [], "bottom": [], "up_n": 0, "down_n": 0, "note": "failed"}
    rows = [r for r in (d.get("rows") or [])
            if not r.get("is_other")
            and r.get("industry") != "综合行业"      # 与"其它行业"同类的杂桶
            and (r.get("stock_n") or 0) >= 10        # 小样本板块一票就能拉出 ±5%
            and r.get("avg_change_pct") is not None]
    rows.sort(key=lambda x: x["avg_change_pct"], reverse=True)
    brief = lambda r: {"industry": r["industry"],
                       "avg_change_pct": r["avg_change_pct"],
                       "amount_yi": r.get("amount_yi"),
                       "stock_n": r.get("stock_n")}
    return {
        "top": [brief(r) for r in rows[:5]],
        "bottom": [brief(r) for r in rows[-5:]][::-1],
        "up_n": sum(1 for r in rows if r["avg_change_pct"] > 0),
        "down_n": sum(1 for r in rows if r["avg_change_pct"] < 0),
        "n": len(rows),
        "note": d.get("note"),
    }


def _collect_news(minutes: int = 30) -> Dict:
    """近 N 分钟快讯条数 + 高信号关键词命中（读 `load_raw_items` 的 120s 缓存）。

    ⚠️ `flash_news.time` 是 `YYYY-MM-DD HH:MM:SS`（北京时区）⇒ 字符串比较即时间序，
       不需要解析；表只保留最近 300 条，覆盖半小时绰绰有余。
    """
    try:
        from app.flash.store import load_raw_items
        items = load_raw_items() or []
    except Exception as e:
        print(f"[workbench_scan] news collect failed: {e}")        # ASCII
        return {"n": 0, "key_n": 0, "samples": []}
    cutoff = (beijing_now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")

    def _t(x):
        # 兼容 `2026-10-21T09:00:00` 与 `2026-10-21 09:00:00` 两种写法
        return str(x or "").replace("T", " ")[:19]

    recent = [i for i in items if _t(i.get("time")) >= cutoff]
    hits = [i for i in recent
            if any(k in str(i.get("content") or "") for k in _NEWS_KEYS)]
    return {
        "n": len(recent),
        "key_n": len(hits),
        "samples": [str(i.get("content") or "")[:40] for i in hits[:3]],
    }


def collect() -> Dict:
    """采一档快照（不写库）。失败项降级为空，绝不抛给调用方。"""
    now = beijing_now()
    snap = {
        "as_of": now.isoformat(timespec="seconds"),
        "slot": None,
        "from_snapshot": False,
        "data_date": None,
        "indices": {},
        "breadth": {},
        "sectors": {},
        "news": {},
    }
    # 行情缓存元信息（是否其实是收盘快照 —— 决定本档是否判分）
    try:
        from app.tencent import _cache
        # 缓存为空（进程刚起 / 脚本直跑）⇒ 先尝试本机快照兜底（零网络）。
        # 不兜底的话会写出"宽度全 0"的采样，把以后的对比全带偏。
        if not (_cache.get("stocks") or {}):
            try:
                from app.tencent import restore_market_snapshot
                restore_market_snapshot()
            except Exception:
                pass
        snap["from_snapshot"] = bool(_cache.get("from_snapshot"))
        snap["data_date"] = _cache.get("data_date")
        data_ts = _cache.get("data_ts") or 0
        if data_ts:
            from datetime import datetime
            snap["data_as_of"] = datetime.fromtimestamp(
                data_ts, tz=_BJ).strftime("%m-%d %H:%M")
    except Exception:
        pass

    try:
        from app.routers.market import _index_snapshot
        snap["indices"] = _index_snapshot() or {}
    except Exception as e:
        print(f"[workbench_scan] index collect failed: {e}")       # ASCII

    snap["breadth"] = _collect_breadth()
    snap["sectors"] = _collect_sectors()
    snap["news"] = _collect_news()
    return snap


# ══════════════════════════════════════════════════════════════════════════
#  差分 / 判定
# ══════════════════════════════════════════════════════════════════════════

def _hm(slot: str) -> int:
    try:
        h, m = str(slot).split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return -1


def _trading_minutes(slot_a: str, slot_b: str) -> int:
    """两个采样点之间的**交易分钟**（跨午休/跨收盘自动只计开市那部分）。"""
    a, b = _hm(slot_a), _hm(slot_b)
    if a < 0 or b <= a:
        return 0
    total = 0
    for s, e in _SESSIONS:
        lo, hi = max(a, s), min(b, e)
        if hi > lo:
            total += hi - lo
    return total


def _diff(cur: Dict, prev: Optional[Dict], trading_min: int) -> Dict:
    """与上一档做差。prev 为空 ⇒ baseline（当日第一档）。"""
    out = {"baseline": prev is None, "trading_min": trading_min}
    if not prev:
        return out
    pb = prev.get("breadth") or {}
    cb = cur.get("breadth") or {}
    if pb and cb:
        out["up_ratio_prev"] = round(_num(pb.get("up_ratio")), 3)
        out["up_ratio"] = round(_num(cb.get("up_ratio")), 3)
        out["d_up_ratio"] = round(_num(cb.get("up_ratio")) - _num(pb.get("up_ratio")), 3)
        out["d_limit_up"] = int(cb.get("limit_up") or 0) - int(pb.get("limit_up") or 0)
        out["d_limit_down"] = int(cb.get("limit_down") or 0) - int(pb.get("limit_down") or 0)
        out["d_amount_yi"] = round(_num(cb.get("amount_yi")) - _num(pb.get("amount_yi")), 0)
    pi = prev.get("indices") or {}
    ci = cur.get("indices") or {}
    out["idx_move"] = {}
    for code in set(pi) & set(ci):
        p = _num((pi.get(code) or {}).get("change_pct"))
        c = _num((ci.get(code) or {}).get("change_pct"))
        out["idx_move"][code] = round(c - p, 2)
    ps = [r.get("industry") for r in ((prev.get("sectors") or {}).get("top") or [])]
    cs = [r.get("industry") for r in ((cur.get("sectors") or {}).get("top") or [])]
    out["leader_prev"] = ps[0] if ps else None
    out["leader_cur"] = cs[0] if cs else None
    out["leader_switch"] = bool(ps and cs and ps[0] != cs[0])
    inter = len(set(ps) & set(cs))
    out["top_overlap"] = (round(inter / max(len(set(cs)), 1), 2) if cs else None)
    out["top_new"] = [x for x in cs if x not in ps][:3]
    # 快讯增量（上一档的 news 计数在 prev 快照里）
    pn = (prev.get("news") or {}).get("n")
    cn = (cur.get("news") or {}).get("n")
    if pn is not None and cn is not None:
        out["d_news"] = int(cn) - int(pn)
    return out


def _judge(snap: Dict, d: Dict) -> Tuple[int, bool, List[Dict], int, str]:
    """规则打分（0~100）+ 方向（2026-10-27 改造）。

    返回 `(score, worth, reasons, bias, verdict)`：
      · score/worth 语义**不变**（强度：这半小时有没有实质变化，阈值 40）；
      · bias = Σ(方向 × 分值)，只累加"能判方向"的规则（指数/宽度/涨停/跌停）；
      · verdict ∈ positive / negative / neutral / none：
          |bias| ≥ BIAS_MIN(20) ⇒ 正/反影响；否则 neutral（在动但方向不明）；
          score < 40 ⇒ none。
    每条 reason 带 `dir`（+1/-1/0），前端可逐条显示"利多/利空/中性"。

    ★ 刻意不判方向的规则：成交额放量、领涨切换、快讯条数 —— 它们只说明"有事"，
      不说明"好坏"（放量下跌也是放量）。
    """
    reasons = []
    score = 0
    bias = 0

    def add(tag: str, text: str, pts: int, dir_: int = 0) -> None:
        nonlocal score, bias
        score += pts
        bias += int(dir_) * int(pts)
        reasons.append({"tag": tag, "text": text, "pts": int(pts), "dir": int(dir_)})

    if snap.get("from_snapshot"):
        return 0, False, [{"tag": "stale", "pts": 0, "dir": 0,
                           "text": "行情缓存是收盘快照（非实时）⇒ 本档不计分"}], 0, "none"
    if d.get("baseline"):
        return 0, False, [{"tag": "baseline", "pts": 0, "dir": 0,
                           "text": "当日基线采样（无可比上一档）"}], 0, "none"

    tmin = int(d.get("trading_min") or 0)
    move_ok = 10 <= tmin <= 45
    if not move_ok:
        reasons.append({"tag": "gap", "pts": 0, "dir": 0,
                        "text": "与上一档相隔 %d 个交易分钟（跳点/停机）⇒ 移动类规则跳过" % tmin})

    if move_ok:
        k = (tmin / 30.0) ** 0.5          # 15 分钟档阈值 ×0.71，45 分钟档 ×1.22
        k = min(max(k, 0.7), 1.3)
        # 方向：取"绝对变动最大"的那个指数的**带符号**涨跌幅
        mv_signed = 0.0
        for _v in (d.get("idx_move") or {}).values():
            _n = _num(_v)
            if abs(_n) > abs(mv_signed):
                mv_signed = _n
        mv = abs(mv_signed)
        _dir_idx = 1 if mv_signed > 0 else -1
        if mv >= 1.0 * k:
            add("idx_move", "核心指数 %.2f%% / %d 分钟（急动）" % (mv, tmin), 35, _dir_idx)
        elif mv >= 0.6 * k:
            add("idx_move", "核心指数 %.2f%% / %d 分钟" % (mv, tmin), 25, _dir_idx)
        elif mv >= 0.35 * k:
            add("idx_move", "核心指数 %.2f%% / %d 分钟（小幅）" % (mv, tmin), 12, _dir_idx)

        du_raw = _num(d.get("d_up_ratio"))
        du = abs(du_raw)
        _dir_breadth = 1 if du_raw > 0 else -1
        if du >= 0.12:
            add("breadth_shift", "涨跌家数比变化 %.2f（大幅）" % du, 20, _dir_breadth)
        elif du >= 0.06:
            add("breadth_shift", "涨跌家数比变化 %.2f" % du, 10, _dir_breadth)
        up_p, up_c = _num(d.get("up_ratio_prev"), 0.5), _num(d.get("up_ratio"), 0.5)
        if (up_p - 0.5) * (up_c - 0.5) < 0 and du >= 0.04:
            add("breadth_flip", "宽度翻转：%.0f%% → %.0f%%（多空易手）"
                % (up_p * 100, up_c * 100), 10, 1 if up_c >= 0.5 else -1)

        dlu = int(d.get("d_limit_up") or 0)
        if abs(dlu) >= 10:
            add("limit_up", "涨停家数 %+d（半小时）" % dlu, 15, 1 if dlu > 0 else -1)
        elif abs(dlu) >= 5:
            add("limit_up", "涨停家数 %+d" % dlu, 8, 1 if dlu > 0 else -1)

        dld = int(d.get("d_limit_down") or 0)
        # 跌停家数增加 = 利空 ⇒ 方向取反
        if abs(dld) >= 5:
            add("limit_down", "跌停家数 %+d（半小时）" % dld, 20, -1 if dld > 0 else 1)
        elif abs(dld) >= 2:
            add("limit_down", "跌停家数 %+d" % dld, 10, -1 if dld > 0 else 1)

        if d.get("leader_switch"):
            add("sector_leader", "领涨板块切换：%s → %s"
                % (d.get("leader_prev"), d.get("leader_cur")), 15)   # 换主线≠好坏 ⇒ dir=0
        elif d.get("top_overlap") is not None and d["top_overlap"] <= 0.6:
            add("sector_rotate", "强势板块前五换掉 ≥2 个（重叠 %.0f%%）"
                % (d["top_overlap"] * 100), 10)

        need_yi = 2500 * (tmin / 30.0)     # 经验：全市场半小时约 1500~2000 亿
        if _num(d.get("d_amount_yi")) >= need_yi:
            add("volume", "区间成交额 %.0f 亿（放量）" % _num(d.get("d_amount_yi")), 10)

    # 快讯：不受跳点影响（条数本身就是本档的绝对量）；条数不分方向
    n = int((snap.get("news") or {}).get("n") or 0)
    key_n = int((snap.get("news") or {}).get("key_n") or 0)
    if n >= 25:
        add("news", "近 30 分钟快讯 %d 条（密集）" % n, 10)
    if key_n >= 2:
        add("news_key", "高信号快讯 %d 条：%s"
            % (key_n, "；".join((snap.get("news") or {}).get("samples") or [])[:60]), 10)
    elif key_n >= 1:
        add("news_key", "高信号快讯 1 条：%s"
            % "；".join((snap.get("news") or {}).get("samples") or [])[:40], 5)

    score = min(score, 100)
    # bias 也按同一比例截断，避免"分值截断但方向分虚高"
    if score >= WORTH_SCORE and bias != 0:
        bias = int(max(-100, min(100, bias)))
    worth = score >= WORTH_SCORE
    if not worth:
        verdict = "none"
    elif bias >= BIAS_MIN:
        verdict = "positive"
    elif bias <= -BIAS_MIN:
        verdict = "negative"
    else:
        verdict = "neutral"
    return score, worth, reasons, bias, verdict


# ══════════════════════════════════════════════════════════════════════════
#  落库 / 查询
# ══════════════════════════════════════════════════════════════════════════

def _load_prev(day: str, before_slot: str) -> Optional[Dict]:
    """取当日上一档（含解析后的 snapshot）。无则 None。"""
    try:
        row = db.fetch_one(
            "SELECT slot, snapshot_json FROM workbench_scan "
            "WHERE date=%s AND slot<%s ORDER BY slot DESC LIMIT 1",
            (day, before_slot))
    except Exception as e:
        print(f"[workbench_scan] prev load failed: {e}")           # ASCII
        return None
    if not row:
        return None
    try:
        snap = json.loads(row.get("snapshot_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(snap, dict) or not snap.get("breadth"):
        return None
    return {"slot": row.get("slot"), "snapshot": snap}


def run_slot(slot: Optional[str] = None, write: bool = True,
             force: bool = False) -> Dict:
    """采一档并（可选）落库。write=False = 只读预览，不写库。"""
    _ensure_table()
    now = beijing_now()
    day = now.strftime("%Y-%m-%d")
    slot = slot or _due_slot(now) or now.strftime("%H:%M")
    key = "%s|%s" % (day, slot)

    if write and not force:
        try:
            if db.fetch_one("SELECT key FROM workbench_scan WHERE key=%s", (key,)):
                return {"key": key, "skipped": "already"}
        except Exception as e:
            print(f"[workbench_scan] key check failed: {e}")       # ASCII

    snap = collect()
    snap["slot"] = slot
    # ★ 宽度为空 = 行情缓存没就绪（首次全量扫描尚未完成 / 刚重启）⇒ **不落库**，
    #   否则会写一行"涨跌全 0"的采样，之后所有差分都失真（同尾盘基线的防线）。
    #   返回 skipped ⇒ 调度器在宽限内下一分钟会重试。
    if not snap.get("breadth"):
        return {"key": key, "skipped": "no market cache"}

    prev = _load_prev(day, slot)
    tmin = _trading_minutes(prev["slot"], slot) if prev else 0
    diff = _diff(snap, prev["snapshot"] if prev else None, tmin)
    score, worth, reasons, bias, verdict = _judge(snap, diff)

    row = {
        "key": key, "date": day, "slot": slot,
        "as_of": snap.get("as_of"),
        "trading_min": str(tmin),
        "score": str(score), "worth": "1" if worth else "0",
        "bias": str(bias), "verdict": verdict,
        "reasons": json.dumps(reasons, ensure_ascii=False),
        "snapshot_json": json.dumps(snap, ensure_ascii=False),
        "diff_json": json.dumps(diff, ensure_ascii=False),
        "created_at": now.isoformat(timespec="seconds"),
    }
    result = {"key": key, "slot": slot, "score": score, "worth": worth,
              "bias": bias, "verdict": verdict,
              "reasons": reasons, "diff": diff, "snapshot": snap,
              "written": False}
    if not write:
        return result

    try:
        db.upsert("workbench_scan", row, conflict_columns=["key"])
        result["written"] = True
        # 保留期清理：每天只做一次（第一档），避免每档都发 DELETE
        if slot == SLOTS[0]:
            cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
            db.execute("DELETE FROM workbench_scan WHERE date < %s", (cutoff,))
        # ★ 2026-10-27：**方向明确且足够强**才推送（正/反影响 ≥30，且每天 ≤4 条）。
        #   语义：方向不明（neutral）不推 —— 用户要的是"利多/利空"，不是"又动了"。
        if verdict in ("positive", "negative") and abs(bias) >= NOTIFY_BIAS:
            try:
                if _daily_notify_count(day) < NOTIFY_DAILY_MAX:
                    db.execute("UPDATE workbench_scan SET notified_at=%s WHERE key=%s",
                               (now.isoformat(timespec="seconds"), key))
                    result["notified"] = True
            except Exception as e:
                print(f"[workbench_scan] notify mark failed: {e}")     # ASCII
    except Exception as e:
        print(f"[workbench_scan] save failed: {e}")                # ASCII
        result["error"] = str(e)[:120]
    return result


def _due_slot(now, grace: int = GRACE_MIN) -> Optional[str]:
    """当前应采样的槽位：最近一个已到点且仍在宽限内的；无则 None。"""
    t = now.hour * 60 + now.minute
    best = None
    for s in SLOTS:
        sm = _hm(s)
        if sm <= t <= sm + grace:
            best = s
    return best


def preview() -> Dict:
    """只读预览：采一档、跟当日最近一档比，**不写库**（可反复调用）。"""
    _ensure_table()
    day = beijing_now().strftime("%Y-%m-%d")
    slot = beijing_now().strftime("%H:%M")
    snap = collect()
    snap["slot"] = slot
    if not snap.get("breadth"):
        return {"ok": False, "date": day, "slot": slot, "written": False,
                "note": "行情缓存未就绪（首次全量扫描需 2-4 分钟）", "snapshot": snap}
    prev = _load_prev(day, "99:99")
    tmin = _trading_minutes(prev["slot"], slot) if prev else 0
    diff = _diff(snap, prev["snapshot"] if prev else None, tmin)
    score, worth, reasons, bias, verdict = _judge(snap, diff)
    return {"ok": True, "date": day, "slot": slot, "prev_slot": (prev or {}).get("slot"),
            "trading_min": tmin, "score": score, "worth": worth,
            "bias": bias, "verdict": verdict,
            "reasons": reasons, "diff": diff, "snapshot": snap, "written": False}


def _daily_notify_count(day: str) -> int:
    """当日已推送（打标）条数 —— 用于 NOTIFY_DAILY_MAX 限流。"""
    try:
        row = db.fetch_one(
            "SELECT COUNT(*) AS c FROM workbench_scan "
            "WHERE date=%s AND notified_at IS NOT NULL", (day,))
        return int((row or {}).get("c") or 0)
    except Exception:
        return 0


def recent_notifications(since_dt=None, newer_fn=None) -> List[Dict]:
    """供 `/flash/notifications`：把"已打标"的档位变成浏览器通知事件。

    只在 `run_slot` 判定为**正/反影响且 |bias| ≥ 30** 时打标；
    `newer_fn(time_str)` 由路由注入（与其它事件同一套时间过滤）。
    """
    _ensure_table()
    try:
        rows = db.fetch(
            "SELECT slot, score, bias, verdict, notified_at, reasons FROM workbench_scan "
            "WHERE notified_at IS NOT NULL ORDER BY notified_at DESC LIMIT 6") or []
    except Exception:
        return []
    out = []
    for r in rows:
        t = str(r.get("notified_at") or "")
        try:
            if newer_fn and not newer_fn(t):
                continue
        except Exception:
            pass
        v = str(r.get("verdict") or "")
        head = ("📈 盘中正影响" if v == "positive"
                else ("📉 盘中反影响" if v == "negative" else "盘中变化"))
        try:
            rs = json.loads(r.get("reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            rs = []
        top = next((str(x.get("text") or "") for x in rs
                    if int(x.get("dir") or 0) != 0 and int(x.get("pts") or 0) > 0), "")
        out.append({"type": "scan", "time": t,
                    "title": f"{head} · {r.get('slot')}",
                    "body": f"{top or '（点开看详情）'} · {r.get('score')}分"})
    return out


def day_detail(date: Optional[str] = None) -> Dict:
    """某日全部采样 + 当日汇总。"""
    _ensure_table()
    day = date or beijing_now().strftime("%Y-%m-%d")
    try:
        rows = db.fetch(
            "SELECT key, date, slot, as_of, trading_min, score, worth, bias, verdict, "
            "reasons, diff_json, created_at FROM workbench_scan WHERE date=%s ORDER BY slot",
            (day,)) or []
    except Exception as e:
        return {"date": day, "rows": [], "error": str(e)[:120]}
    out = []
    for r in rows:
        try:
            reasons = json.loads(r.get("reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            reasons = []
        try:
            diff = json.loads(r.get("diff_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            diff = {}
        _b = int(_num(r.get("bias")))
        _v = str(r.get("verdict") or "")
        if not _v:
            # 老数据（2026-10-27 之前）没有 verdict 列 ⇒ 按同一规则现算，前端不用兼容两套
            _v = "positive" if _b >= BIAS_MIN else ("negative" if _b <= -BIAS_MIN else
                  ("neutral" if str(r.get("worth")) == "1" else "none"))
        out.append({
            "slot": r.get("slot"), "as_of": r.get("as_of"),
            "trading_min": int(_num(r.get("trading_min"))),
            "score": int(_num(r.get("score"))),
            "worth": str(r.get("worth")) == "1",
            "bias": _b, "verdict": _v,
            "reasons": reasons,
            "leader_cur": diff.get("leader_cur"),
            "idx_move": diff.get("idx_move") or {},
        })
    def _has(x, tag):
        return any((r or {}).get("tag") == tag for r in x["reasons"])

    stale_n = sum(1 for x in out if _has(x, "stale"))
    base_n = sum(1 for x in out if _has(x, "baseline"))
    judged = max(len(out) - stale_n - base_n, 0)
    worth_n = sum(1 for x in out if x["worth"])
    # ★ 2026-10-27：三档计数（正影响 / 反影响 / 中性变化），worth_rate 保留给老前端
    pos_n = sum(1 for x in out if x["verdict"] == "positive")
    neg_n = sum(1 for x in out if x["verdict"] == "negative")
    neu_n = sum(1 for x in out if x["verdict"] == "neutral")
    return {"date": day, "samples": len(out), "judged": judged,
            "stale_n": stale_n, "baseline_n": base_n, "worth_n": worth_n,
            "pos_n": pos_n, "neg_n": neg_n, "neu_n": neu_n,
            # ★ 分母只算"真正被判定"的档：stale（快照当实时）与 baseline 不参与，
            #   否则"行情陈旧"会把 worth_rate 稀释成假低值。
            "worth_rate": (round(worth_n / judged, 2) if judged else None),
            "missing_slots": [s for s in SLOTS if s not in {x["slot"] for x in out}],
            "rows": out}


def summary(days: int = 7) -> Dict:
    """多日统计：回答"8 个采样点里几个真有料"。

    ★ 只读必要列（不取 snapshot_json），避免这个统计接口自己变成 egress 大头。
    """
    _ensure_table()
    days = max(1, min(int(days or 7), 60))
    since = (beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        rows = db.fetch(
            "SELECT date, slot, score, worth, bias, verdict, reasons FROM workbench_scan "
            "WHERE date >= %s ORDER BY date, slot", (since,)) or []
    except Exception as e:
        return {"days": days, "error": str(e)[:120], "samples": 0, "by_day": []}

    by_day: Dict[str, Dict] = {}
    tag_all: Dict[str, int] = {}
    tag_worth: Dict[str, int] = {}
    hist = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
    for r in rows:
        day = str(r.get("date"))
        g = by_day.setdefault(day, {"date": day, "samples": 0, "judged": 0,
                                    "stale_n": 0, "baseline_n": 0, "worth_n": 0,
                                    "pos_n": 0, "neg_n": 0, "neu_n": 0,
                                    "worth_slots": [], "score_sum": 0})
        score = int(_num(r.get("score")))
        worth = str(r.get("worth")) == "1"
        _b = int(_num(r.get("bias")))
        _v = str(r.get("verdict") or "")
        if not _v:
            _v = "positive" if _b >= BIAS_MIN else ("negative" if _b <= -BIAS_MIN else
                  ("neutral" if worth else "none"))
        g["samples"] += 1
        try:
            reasons = json.loads(r.get("reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            reasons = []
        tags = {str((x or {}).get("tag") or "") for x in reasons}
        stale = "stale" in tags
        base = "baseline" in tags
        if stale:
            g["stale_n"] += 1
        elif base:
            g["baseline_n"] += 1
        else:
            g["judged"] += 1
            g["score_sum"] += score
            if worth:
                g["worth_n"] += 1
                g["worth_slots"].append(r.get("slot"))
            if _v == "positive":
                g["pos_n"] += 1
            elif _v == "negative":
                g["neg_n"] += 1
            elif _v == "neutral":
                g["neu_n"] += 1
            if score < 20:
                hist["0-19"] += 1
            elif score < 40:
                hist["20-39"] += 1
            elif score < 60:
                hist["40-59"] += 1
            elif score < 80:
                hist["60-79"] += 1
            else:
                hist["80-100"] += 1
        for tag in tags:
            if not tag or tag in ("baseline", "stale", "gap"):
                continue
            tag_all[tag] = tag_all.get(tag, 0) + 1
            if worth:
                tag_worth[tag] = tag_worth.get(tag, 0) + 1

    days_out = []
    for day in sorted(by_day):
        g = by_day[day]
        g["avg_score"] = (round(g["score_sum"] / max(g["judged"], 1), 1)
                          if g["judged"] else None)
        g.pop("score_sum", None)
        g["missing_slots"] = [s for s in SLOTS if s not in
                              {r.get("slot") for r in rows if str(r.get("date")) == day}]
        days_out.append(g)
    total = len(rows)
    judged = sum(g["judged"] for g in days_out)
    worth_n = sum(g["worth_n"] for g in days_out)
    return {
        "days": days, "since": since,
        "samples": total, "judged": judged,
        "stale_n": sum(g["stale_n"] for g in days_out),
        "baseline_n": sum(g["baseline_n"] for g in days_out),
        "worth_n": worth_n,
        "pos_n": sum(g.get("pos_n", 0) for g in days_out),
        "neg_n": sum(g.get("neg_n", 0) for g in days_out),
        "neu_n": sum(g.get("neu_n", 0) for g in days_out),
        # ★ 分母 = judged（剔除 stale/baseline）：否则"行情陈旧"会稀释成假低值
        "worth_rate": (round(worth_n / judged, 2) if judged else None),
        "avg_score": (round(sum(g["avg_score"] or 0 for g in days_out) / max(len(days_out), 1), 1)
                      if judged else None),
        "score_hist": hist,
        "reason_counts": [{"tag": k, "n": v} for k, v in
                          sorted(tag_all.items(), key=lambda x: -x[1])],
        "reason_counts_worth": [{"tag": k, "n": v} for k, v in
                                sorted(tag_worth.items(), key=lambda x: -x[1])],
        "by_day": days_out,
        "note": ("结论看 worth_rate：若多数交易日 ≤0.25（8 档里 ≤2 档）⇒ "
                 "LLM 盘面分析应按**变化触发**，不要每 30 分钟固定生成。"
                 "stale_n 高说明行情缓存整段是收盘快照，先修数据新鲜度再看本指标。"),
    }
