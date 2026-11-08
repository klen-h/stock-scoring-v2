# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】李大霄视角卡 —— xiaopage LLM 解读的接收 / 归一 / 读取（纯展示参考）
================================================================================
链路（事件驱动，.codebuddy/plans/李大霄视角卡片_xiaopage解读接入__3f8a2c61.md 定稿）：
  xiaopage（B站轮询→转写→LLM 解读，D:\\stock-scoring\\xiaopage）在 LLM 输出解读的
  时刻 POST /api/market/lidaxiao/push（payload 携带 新解读+前2条 自愈；fail-soft）
  ⇒ 本模块 ingest_entries()：防御解析 → 主键合成 → 归一 → 瘦身落库
  ⇒ GET /api/market/lidaxiao ← 工作台右栏只读卡片（60s 随教练卡轮询）。
  历史 484 条由 scripts/backfill_lidaxiao.py 一次性本地回填（同一 ingest 口径）。

上游 schema 实勘（2026-10-25，fresh 484 条全量核实）：
  · 字段：_id / bvid / title / date(北京时间 YYYY-MM-DD HH:MM) /
    sentiment_score(0-100，50=中性) / disclaimer_level(1-5) / summary / tags /
    ★ 2026-11-08 v2 新增：market(a_share/us/hk/other/mixed/unstated) / direction /
      schema_version(提示词+词表版本) / llm_model(实际出分模型)。v1 条目这四个为 None。
    structured{core_views, sectors, operation_advice, risk_tips} /
    deep_analysis{final_conclusion, …} /
    logic_factors[{factor_name, factor_value, weight 0-1, direction, description}] /
    hit_status(pending/hit/miss)。
  · 18 条缺 _id（含 2026-09-24 近期条）⇒ 主键合成兜底（_id ‖ bvid ‖ auto:date|title）；
    缺 date 才跳过（排序/lag 的硬依赖）。
  · direction 全量普查 33 种取值（看跌×1587 / 中性偏空×446 / 中性偏多×348 / 看涨×214 /
    中性×181 / 看多×2 / 变体 28 种，如"看跌（对美股）""看涨/看跌（分化）"）
    ⇒ 包含式模糊归一，不能 == 比较。
  · 情绪六级口径移植自 xiaopage src/utils/sentiment.ts（保证两站同分同色）。
  · deep_analysis 整体不入库（体积大头），唯一例外 final_conclusion ——
    ★ 2026-11-08 起**仅作兜底**：卡面主文案改取 `summary`（与 xiaopage 企微推送同口径）。
    新提示词下 final_conclusion 是「(1)明说 (2)未表态 (3)适用市场」三段式，大段是
    "原文未表态"，信息量低；summary 是 200 字以内、有原话依据的概述。

★ 预登记声明：不改分数、不进闸门、不改 regime、不碰模拟盘、不进任何决策链。
  卡面为 LLM 二手解读（xiaopage 产物，非本人原文），仅供研究参考。
================================================================================
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_TABLE = "lidaxiao_views"
_READY = False
_BJ = timezone(timedelta(hours=8))     # 北京时间（上游 date 列即按它写）

# ★ 2026-10-28（用户："李大霄视角盘后也会有更新的"）：原设 14 天（当时假设"不定期、滞后 2~7 天
#   是常态"），但实测 `videos.json` 里 10-23 有三条、10-27 17:00 有一条盘后视频 ⇒ **基本每个
#   交易日都有**，14 天等于永不告警（本次就是停在 10-24 落后 4 天却无任何提示）。
#   收紧到 5 天：正常隔 1~2 天（含周末）不会误报，真断档（推送失败/停更）能亮 ⚠。
_STALE_DAYS = 5

_NOTE = ("LLM 结构化解读（xiaopage 项目产物，非本人原文）；"
         "观点仅供参考，不构成投资建议，不进入本项目任何决策链")

# ★ 2026-11-08：xiaopage v2 新增的四个溯源/市场字段。
#   _EXTRA_COLS = None 表示「尚未探测」（单测直接调 _slim 的场景按全字段输出）；
#   ensure_table() 之后是实际可用的列集合。
_NEW_COLS = ("market", "direction", "schema_version", "llm_model")
_EXTRA_COLS = None


def ensure_table() -> None:
    global _READY, _EXTRA_COLS
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS lidaxiao_views (
            entry_id TEXT PRIMARY KEY,
            bvid TEXT, title TEXT, date TEXT,
            sentiment_score INTEGER, sentiment_level TEXT,
            disclaimer_level INTEGER,
            market TEXT, direction TEXT, schema_version TEXT, llm_model TEXT,
            summary TEXT, tags TEXT,
            core_views TEXT, sectors TEXT, operation_advice TEXT, risk_tips TEXT,
            final_conclusion TEXT,
            factors_json TEXT,
            hit_status TEXT, created_at TEXT
        )
    """)
    # ★ 已有表不会因 CREATE TABLE IF NOT EXISTS 自动加列 ⇒ 必须显式补。
    #   SQLite 不支持 ADD COLUMN IF NOT EXISTS，故先探测再 ALTER（同 memory_watch 的写法）。
    #   加不上的列由 ingest_entries 从 row 里剔除，绝不能让一条溯源字段拖垮整批入库。
    ok = set()
    for col in _NEW_COLS:
        try:
            db.fetch_one(f"SELECT {col} FROM {_TABLE} LIMIT 1")
            ok.add(col)                      # 列已存在（空表时 SELECT 也不报错）
        except Exception:
            try:
                db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {col} TEXT")
                ok.add(col)
            except Exception as e:
                print(f"[lidaxiao] add column {col} failed, skip it: {str(e)[:80]}")
    _EXTRA_COLS = ok
    _READY = True


# ── 归一纯函数（单测锚点：backend/tests/generated/test_lidaxiao_views.py）──────

def sentiment_level(score) -> Optional[str]:
    """情绪六级（口径移植自 xiaopage src/utils/sentiment.ts，两站同分同色）。"""
    s = _int(score)
    if s is None:
        return None
    if s <= 15:
        return "极度恐慌"
    if s <= 35:
        return "悲观"
    if s <= 45:
        return "谨慎偏空"
    if s <= 55:
        return "谨慎偏多"
    if s <= 75:
        return "乐观"
    return "极度狂热"


def norm_direction(raw) -> str:
    """direction 模糊归一 → bull/bear/mixed/neutral（上游枚举漂移，见模块头普查）。"""
    d = str(raw or "").strip()
    if not d:
        return "neutral"
    bull = ("看涨" in d) or ("看多" in d)
    bear = ("看跌" in d) or ("看空" in d)
    if (bull and bear) or ("分化" in d):
        return "mixed"                       # "看跌（对高估股）/看涨（对防御股）"
    if bear or d.startswith("中性偏空"):
        return "bear"                        # "看跌（对美股）"
    if bull or d.startswith("中性偏多"):
        return "bull"                        # "看多"（不带"涨"字的变体）
    return "neutral"


# ── 防御解析 ────────────────────────────────────────────────────────────────

def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _entry_key(e: dict) -> Optional[str]:
    """主键合成：_id ‖ bvid ‖ auto:date|title（18 条缺 _id 的实证兜底）。"""
    k = str(e.get("_id") or "").strip()
    if k:
        return k
    k = str(e.get("bvid") or "").strip()
    if k:
        return k
    d = str(e.get("date") or "").strip()
    if d:
        return "auto:" + d + "|" + str(e.get("title") or "")[:32]
    return None


def _arr_json(v, limit: int = 0) -> Optional[str]:
    """数组字段 → JSON 字符串（防御：非 list / 空数组一律 None，未知元素转 str）。"""
    if not isinstance(v, list):
        return None
    vals = [str(x) for x in v if x is not None]
    if limit > 0:
        vals = vals[:limit]
    return json.dumps(vals, ensure_ascii=False) if vals else None


def _top_factors(e: dict, n: int = 3) -> Optional[str]:
    """logic_factors 按 weight 降序取 Top n（含归一 direction；入库备用，卡面不展示）。"""
    raw = e.get("logic_factors")
    if not isinstance(raw, list):
        return None
    fs = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        try:
            w = float(f.get("weight"))
        except (TypeError, ValueError):
            w = 0.0
        fs.append((w, f))
    fs.sort(key=lambda x: -x[0])
    out = [{"factor_name": str(f.get("factor_name") or ""),
            "weight": w,
            "direction": str(f.get("direction") or ""),
            "direction_norm": norm_direction(f.get("direction"))}
           for w, f in fs[:n]]
    return json.dumps(out, ensure_ascii=False) if out else None


def _slim(e: dict) -> Optional[dict]:
    """上游 entry → 落库行（防御解析；缺 date 返回 None = 跳过该条）。"""
    date = str(e.get("date") or "").strip()
    key = _entry_key(e)
    if not date or not key:
        return None
    st = e.get("structured") if isinstance(e.get("structured"), dict) else {}
    da = e.get("deep_analysis") if isinstance(e.get("deep_analysis"), dict) else {}
    score = _int(e.get("sentiment_score"))
    return {
        "entry_id": key,
        "bvid": str(e.get("bvid") or "").strip() or None,
        "title": str(e.get("title") or ""),
        "date": date,
        "sentiment_score": score,
        "sentiment_level": sentiment_level(score),
        "disclaimer_level": _int(e.get("disclaimer_level")),
        # ★ 2026-11-08：市场维度 + 规则/模型溯源（xiaopage v2 才有，v1 为 None）。
        #   不带 market，工作台会把 A 股 70 分和美股 20 分混进同一个均值。
        "market": str(e.get("market") or "") or None,
        "direction": str(e.get("direction") or "") or None,
        "schema_version": str(e.get("schema_version") or "") or None,
        "llm_model": str(e.get("llm_model") or "") or None,
        "summary": str(e.get("summary") or "") or None,
        "tags": _arr_json(e.get("tags")),
        "core_views": _arr_json(st.get("core_views"), limit=5),
        "sectors": _arr_json(st.get("sectors")),
        "operation_advice": str(st.get("operation_advice") or "") or None,
        "risk_tips": _arr_json(st.get("risk_tips"), limit=3),
        "final_conclusion": str(da.get("final_conclusion") or "") or None,
        "factors_json": _top_factors(e),
        "hit_status": str(e.get("hit_status") or "") or None,
        "created_at": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── 入库（POST /lidaxiao/push 与本地回填共用，幂等）─────────────────────────

def ingest_entries(entries) -> Dict:
    """批量入库：逐条防御校验，坏条目 skip 不炸整批（响应带 skipped 明细供推送方留痕）。"""
    ensure_table()
    stored, skipped, reasons = 0, 0, []
    for e in (entries or []):
        if not isinstance(e, dict):
            skipped += 1
            reasons.append("entry 非对象")
            continue
        row = _slim(e)
        if not row:
            skipped += 1
            reasons.append("缺 date：" + str(e.get("_id") or e.get("title") or "")[:30])
            continue
        # ★ 旧表补列失败时，把这些列从 row 剔掉再 upsert —— 多一列会让整批
        #   upsert 报错，连本来能入库的条目一起丢。
        if _EXTRA_COLS is not None:
            row = {k: v for k, v in row.items()
                   if k not in _NEW_COLS or k in _EXTRA_COLS}
        try:
            db.upsert(_TABLE, row, conflict_columns=["entry_id"])
            stored += 1
        except Exception as ex:
            skipped += 1
            reasons.append("落库失败：" + str(ex)[:60])
    return {"stored": stored, "skipped": skipped,
            "reasons": reasons[:5], "latest": _latest_meta()}


def _latest_meta() -> Optional[Dict]:
    try:
        r = db.fetch_one(f"SELECT entry_id, title, date FROM {_TABLE} "
                         f"ORDER BY date DESC LIMIT 1")
        return {"entry_id": r.get("entry_id"), "title": r.get("title"),
                "date": r.get("date")} if r else None
    except Exception:
        return None


def _latest_date() -> Optional[str]:
    """表内最新一期的日期（`YYYY-MM-DD HH:MM`），空表返回 None。"""
    try:
        r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE}")
        d = (r or {}).get("d")
        return str(d) if d else None
    except Exception:
        return None


# ── 手动补拉（★ 2026-10-28 用户："盘后也会有更新，加一个手动更新按钮"）────────
#
# 【为什么要"补拉"】原设计是**事件推送**（xiaopage 在 LLM 出解读的那一刻 POST），
#   而那个时刻 Actions 还没 commit `videos.json` ⇒ 推送链路一旦失败（网络/超时/token），
#   这条解读就永久缺失，卡片只能停在旧日期（本次实测停在 10-24，落后 4 天）。
#   ⇒ 补一条"事后回拉"通道：Actions 结束后 raw 已更新，此时拉是**准的**。
#
# 【成本】`videos.json` ≈ 6.4MB（484+ 条全量，xiaopage 未拆分小文件）。
#   · 只手动触发，**不进任何定时循环**；
#   · 带 `If-None-Match`（ETag）⇒ 上游没变时 304 零 body，重复点击不产生流量；
#   · 只把"表内最新日期及之后"的条目送 `ingest_entries`（含同日多条视频），
#     而非全量 484 条 upsert；空表/全无匹配时取前 3 条自愈。
# ★★ 2026-10-28（用户："6.4m 太大了，调整一下 json"）：
#   xiaopage 侧已派生一份**消费侧精简版** `views-lite.json`
#   （`scripts/build-views-lite.js`：只留最近 60 条 + 消费方要的字段，
#     6243KB → 166KB，-97.3%；每次写 videos.json 后自动重建、随 CI 提交）。
#   ⇒ 默认读 lite；老部署/未重建时**回退完整版** `videos.json`（幂等，口径一致）。
#   ⚠️ `videos.json` 仍是 xiaopage 站点自己的数据源，**不能**为了省流量去裁剪它。
_GH_RAW = "https://raw.githubusercontent.com/"
_LITE_PATH = "klen-h/xiaopage/main/public/data/views-lite.json"
_FULL_PATH = "klen-h/xiaopage/main/public/data/videos.json"
# 旧常量名保留（语义不变：默认上游 = lite 的**直连**地址）
_DEFAULT_UPSTREAM = _GH_RAW + _LITE_PATH
_LEGACY_UPSTREAM = _GH_RAW + _FULL_PATH
_ETAG: Dict[str, Optional[str]] = {}      # url → ETag（每个镜像各自一份，别串）


def _mirror_urls(path: str) -> List[str]:
    """一个 GitHub raw 路径 → **按实测可用性排序的多个镜像**。

    ★★ 2026-11-04（用户实测报错）：
        `更新失败：上游拉取失败: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))`
      —— 国内服务器**直连 raw.githubusercontent.com 被 RST/超时**。
      ⚠️ 迷惑点：同一台机器的 `git pull` 是好的（配了 `ghfast.top` 镜像），
         但 `requests` 直连**不走 git 的配置** ⇒ 两条链路行为完全不同，
         看日志会以为"网络没问题"，实际只是 git 走了代理。
      同一文件（views-lite.json，150.5KB）本地实测：
        raw 直连    ❌ 15s 超时
        gh-proxy    ✅ 0.53s
        jsDelivr    ✅ 1.33s
        ghfast      ✅ 2.09s
      ⇒ 依次尝试、**任意成功即返回**；全挂才报错。
      · `LIDX_MIRROR_PREFIX`（如 `https://gh-proxy.com`）可把自建/指定代理放到最前；
      · `LIDX_UPSTREAM_URL`（完整 URL）依然最高优先，且走它时**不**再试镜像。
    """
    p = path.lstrip("/")
    cands: List[str] = []
    pre = (os.environ.get("LIDX_MIRROR_PREFIX") or "").strip().rstrip("/")
    if pre:
        cands.append(f"{pre}/{_GH_RAW}{p}")
    cands += [
        f"https://gh-proxy.com/{_GH_RAW}{p}",
        f"https://ghfast.top/{_GH_RAW}{p}",
        # jsDelivr 语法不同：gh/user/repo@branch/path（只替换**第一个** /main/）
        f"https://cdn.jsdelivr.net/gh/{p.replace('/main/', '@main/', 1)}",
        f"{_GH_RAW}{p}",          # 直连兜底（海外可用；国内多半被 reset）
    ]
    seen, out = set(), []
    for u in cands:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _fetch_and_ingest(url: str, timeout) -> Dict:
    """拉一个上游文件 → 过滤出新增 → `ingest_entries`。失败返回 ok=False。"""
    import requests

    headers = {"User-Agent": "stock-scoring/lidaxiao-refresh"}
    etag = _ETAG.get(url)
    if etag:
        headers["If-None-Match"] = etag
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": f"上游拉取失败: {str(e)[:90]}", "url": url}
    if r.status_code == 304:
        return {"ok": True, "unchanged": True, "latest": _latest_meta(),
                "lag_days": _lag_days((_latest_meta() or {}).get("date")),
                "source": url, "bytes": 0}
    if r.status_code != 200:
        return {"ok": False, "error": f"上游 HTTP {r.status_code}", "url": url}
    _ETAG[url] = r.headers.get("ETag")
    try:
        data = r.json()
    except Exception:
        return {"ok": False, "error": "上游不是合法 JSON（可能被限流/返回 HTML）",
                "url": url}
    if not isinstance(data, list):
        return {"ok": False, "error": "上游不是 JSON 数组", "url": url}

    base = _latest_date()
    # 只送「表内最新日期及之后」的条目：normal 情况就是新增的 1~3 条；
    # 同日多条视频也一并覆盖（date 含时刻，比较用前 10 位）。
    subset = [e for e in data
              if isinstance(e, dict) and str(e.get("date") or "")[:10] >= (base or "")]
    if not subset:
        subset = data[:3]          # 自愈：上游最新日期反而更旧（回滚场景）→ 补前 3 条
    res = ingest_entries(subset)
    latest = res.get("latest") or {}
    print(f"[lidaxiao] refresh: fetched={len(data)} sent={len(subset)} "
          f"stored={res.get('stored')} skipped={res.get('skipped')} "
          f"bytes={len(r.content)}")   # ASCII（铁律⑥）
    return {"ok": True, "unchanged": False, "fetched": len(data), "sent": len(subset),
            "stored": res.get("stored"), "skipped": res.get("skipped"),
            "reasons": res.get("reasons"), "latest": latest,
            "lag_days": _lag_days(latest.get("date")),
            "source": url, "bytes": len(r.content)}


def refresh_from_upstream(url: str = None, timeout=(10.0, 60.0),
                          budget: float = 90.0) -> Dict:
    """补拉 xiaopage 的最新解读并落库（幂等）：**多镜像逐个试，先 lite 后完整版**。

    ⚠️ `timeout` 用 (connect, read)：lite 只有 ~150KB（镜像 0.5~2s）；
       回退完整版 6.4MB 时国内网络实测 59s ⇒ 给 60s 读超时。
    ★ `budget`（秒）：**总时间预算**。镜像全挂时若不设上限，4×lite + 4×full
      各自等到超时 ≈ 数分钟，按钮会一直转圈 ⇒ 超过预算立即放弃并返回最后一次错误。
    """
    if url:
        urls = [str(url).strip()]
    else:
        env_url = (os.environ.get("LIDX_UPSTREAM_URL") or "").strip()
        urls = ([env_url] if env_url else []) + _mirror_urls(_LITE_PATH)
        urls += _mirror_urls(_FULL_PATH)      # lite 全挂才回退完整版（6.4MB，慢）
    last = ""
    t0 = time.time()
    for u in urls:
        if time.time() - t0 > budget:
            print(f"[lidaxiao] 上游预算 {budget:.0f}s 用尽，放弃剩余 {len(urls)} 个镜像中的未试项")
            last = last or f"超过总预算 {budget:.0f}s"
            break
        r = _fetch_and_ingest(u, timeout)
        if r.get("ok"):
            # 打一行命中来源：以后排查"到底走的哪个镜像"不用再猜
            print(f"[lidaxiao] 上游命中: {str(r.get('source') or u)[:70]}")
            return r
        last = r.get("error") or last
        print(f"[lidaxiao] 上游 {str(u)[-42:]} 失败: {last}")   # ASCII（铁律⑥）
    return {"ok": False, "error": last or "上游不可用（所有镜像均失败）"}


# ── 读取（GET /market/lidaxiao）─────────────────────────────────────────────

def _jload_list(s) -> Optional[list]:
    if not s:
        return None
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else None
    except (TypeError, ValueError):
        return None


def _row_to_latest(r: dict) -> Dict:
    bvid = r.get("bvid")
    return {
        "entry_id": r.get("entry_id"),
        "bvid": bvid,
        "title": r.get("title"),
        "date": r.get("date"),
        "video_url": f"https://www.bilibili.com/video/{bvid}" if bvid else None,
        "sentiment_score": r.get("sentiment_score"),
        "sentiment_level": r.get("sentiment_level"),
        "disclaimer_level": r.get("disclaimer_level"),
        # ★ 2026-11-08：SELECT * 若旧表缺列，r.get() 返回 None（不报错，向后兼容）
        "market": r.get("market"),
        "direction": r.get("direction"),
        "schema_version": r.get("schema_version"),
        "llm_model": r.get("llm_model"),
        "summary": r.get("summary"),
        "tags": _jload_list(r.get("tags")),
        "final_conclusion": r.get("final_conclusion"),
        "sectors": _jload_list(r.get("sectors")),
        # ↓ 以下字段卡面不渲染（入库备用，Phase 2 日报/折叠区直接取）
        "core_views": _jload_list(r.get("core_views")),
        "operation_advice": r.get("operation_advice"),
        "risk_tips": _jload_list(r.get("risk_tips")),
        "factors": _jload_list(r.get("factors_json")) or [],
        "hit_status": r.get("hit_status"),
    }


def _lag_days(date_str) -> Optional[int]:
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return (datetime.now(_BJ).date() - d.date()).days


def load(limit: int = 14) -> Dict:
    """最新一期全字段 + 近 N 期轨迹 + 滞后/过期标记（前端卡片唯一数据源）。"""
    ensure_table()
    n = max(1, min(int(limit or 14), 60))
    try:
        rows = db.fetch(f"SELECT * FROM {_TABLE} ORDER BY date DESC LIMIT {n + 1}")
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {str(e)[:80]}",
                "latest": None, "trend": []}
    if not rows:
        return {"ok": True, "latest": None, "trend": [],
                "lag_days": None, "stale": False, "note": _NOTE}
    latest = _row_to_latest(rows[0])
    trend = [{"date": str(r.get("date") or "")[:10],
              "score": r.get("sentiment_score"),
              "title": r.get("title")} for r in rows[1:n + 1]]
    # ★★ 2026-10-26（用户："一天可能有几个视频，卡片内部 tab 切换"）：
    #   增加 `items` = 最近 N 期**精简投影**（只带卡面要渲染的字段，不带 factors/core_views）
    #   ⇒ 12 期约 5KB；前端用它渲染卡内 tab（选中态按 entry_id 记忆）。
    #   ★ 2026-11-01：**前提已变** —— `items` 虽精简，但 `SELECT * ... LIMIT 15` 的**原始行**
    #     每行约 3KB（factors_json / core_views / operation_advice 等大字段），
    #     实测单次响应 ≈45KB、全周 124MB。前端已改为**不轮询**（进 tab 才取）。
    # ★ 2026-11-08：卡面主文案切到 summary ⇒ items 投影必须带上它。
    #   前端 lidxCur 优先取 items（卡内 tab 切换选中的那条），items 少一个字段
    #   就会显示空文案 —— 只有 latest 带 summary 是不够的。
    #   final_conclusion 保留作兜底（旧数据 / 异常条目）。
    # ★ 2026-11-08：market/direction 卡面渲染；schema_version/llm_model 进 tooltip
    #   追溯口径。四个都放进 items，否则切到非最新 tab 时这些字段会消失。
    _ITEM_FIELDS = ("entry_id", "bvid", "title", "date", "video_url",
                    "sentiment_score", "sentiment_level", "disclaimer_level",
                    "market", "direction", "schema_version", "llm_model",
                    "summary", "final_conclusion", "sectors")
    items = []
    for r in rows[:min(n, 12)]:
        full = _row_to_latest(r)
        items.append({k: full.get(k) for k in _ITEM_FIELDS})
    lag = _lag_days(latest.get("date"))
    return {"ok": True, "latest": latest, "items": items, "trend": trend,
            "lag_days": lag, "stale": lag is not None and lag > _STALE_DAYS,
            "note": _NOTE}
