# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】快讯解读卡（flash brief）—— 大盘 / 板块 / 个股 + 重要快讯「人话解读」
================================================================================
背景（2026-10-24，用户）：
  「快讯卡片内容过于简略，无法清晰呈现大盘、板块及个股的异动信息……
    补充大盘走势、板块轮动、个股异动的关键数据与变化说明，突出异动原因和影响范围。」
  确认口径：混合（规则筛 + 仅重要条批量 LLM）；卡片列表 + 可展开详情；
  **系统级**：盘中出现异动可直接触发 LLM，并通过 `/flash/notifications` 弹浏览器通知。

★ 数据全部来自既有数据源（零新增外部依赖）：
  · 大盘   ← `tencent._fetch_tencent`（指数）+ `tencent._cache["stocks"]`（涨跌家数/成交额）
  · 板块   ← `eastmoney.get_sectors` / `get_sector_flow`
  · 个股   ← 行情缓存 + `routers.stock._detect_anomaly`（同一套异动规则）
  · 快讯   ← `flash_news`（近 7 天保留，`cluster` 已回填）
  · 诊断   ← 既有 `/flash/diagnosis`（`dominant_narrative` 等）只作兜底，不重复烧 LLM

★ 成本纪律（项目铁律，必须遵守）：
  ① **批量**：一次刷新最多 1 次 LLM（5 条重要快讯打包进同一个 prompt）；
  ② **按日缓存 + 数据指纹**：`sig` 未变且 TTL 内 ⇒ 直接读库；
  ③ **盘中自动触发有闸门**：只有命中异动阈值才自动跑，且带**冷却 30min** + **日上限**
     （env `FLASH_BRIEF_DAILY_MAX`，默认 8）——不会每分钟烧一次；
  ④ 非交易时段**不自动触发**（周末/夜间只看不烧）。

★ 红线：解读只说明「发生了什么 / 为什么 / 影响谁」，**不给买卖与仓位建议**，
  不进评分、不进闸门、不碰模拟盘。
================================================================================
"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.database import db

_TABLE = "flash_brief"
# ★★ 2026-10-26（方案 B：一天多次解读要做 tab 回看）：
#   主表 `date` 是主键 ⇒ 每次解读都**覆盖**上一份，历史无法回看（只留 auto_runs 计数）。
#   ⇒ 新增 `flash_brief_runs`：**每次真实解读 append 一条**（不覆盖），供前端按时间切 tab。
#   主表仍是"最新一份"的权威读源（60s 轮询只读它，避免历史查询放大开销）。
_RUN_TABLE = "flash_brief_runs"
RUN_KEEP = int(os.environ.get("FLASH_BRIEF_RUN_KEEP") or 10)   # 每天最多留几条历史
_READY = False

TTL_MIN = int(os.environ.get("FLASH_BRIEF_TTL_MIN") or 30)          # 手动/读缓存有效期
AUTO_TTL_MIN = int(os.environ.get("FLASH_BRIEF_AUTO_TTL_MIN") or 20)  # 自动触发后的有效期
COOLDOWN_MIN = int(os.environ.get("FLASH_BRIEF_COOLDOWN_MIN") or 30)
DAILY_MAX = int(os.environ.get("FLASH_BRIEF_DAILY_MAX") or 8)
NEWS_LIMIT = int(os.environ.get("FLASH_BRIEF_NEWS") or 5)          # 每次解读的重要条数
POLL_NEWS_SCAN = int(os.environ.get("FLASH_BRIEF_SCAN") or 60)     # 扫描多少条原始快讯
# 自动触发阈值
IDX_TRIGGER = float(os.environ.get("FLASH_BRIEF_IDX_TRIGGER") or 1.0)   # 指数 |涨跌|%
RATIO_LOW = float(os.environ.get("FLASH_BRIEF_RATIO_LOW") or 0.4)       # 涨跌比（跌家/涨家 的倒数口径见下）
RATIO_HIGH = float(os.environ.get("FLASH_BRIEF_RATIO_HIGH") or 2.5)
NEWS_TRIGGER = int(os.environ.get("FLASH_BRIEF_NEWS_TRIGGER") or 2)     # 近 30min 重要条数

# 集群 → 影响范围（规则兜底；LLM 可用时以 LLM 为准）
CLUSTER_SCOPE = {
    "中东战争": "原油/军工/黄金；指数风险偏好",
    "伊朗局势": "原油/军工/黄金；指数风险偏好",
    "原油能源": "石油石化/油运/化工",
    "美联储利率": "美债/美元/外资敏感（成长股）",
    "国内政策": "券商/地产/基建；指数",
    "中美贸易": "出口链/半导体",
    "黄金贵金属": "黄金股/避险",
    "俄乌局势": "能源/粮食",
    # ★ 2026-10-28：库里实际有 12 个分类，原先只映射 9 个 ⇒ 下面 3 类的「影响范围」恒为
    #   `—`（实测各 3/3/2 条）。补上映射即可，**不动打分**（不进 `STRONG_CLUSTERS`，
    #   避免改变 Top5 口径）。
    "港股/中概股": "港股/中概互联；外资风险偏好",
    "农业粮食": "种植/养殖/化肥；粮价链",
    "美联储人事": "美债/美元；全球风险偏好（政策预期）",
    "其他": "—",
}

# 重要快讯打分词表（+2 = 强；+1 = 一般）
STRONG_KW = ["涨停", "跌停", "封板", "炸板", "降准", "降息", "加息", "主席", "国务院",
             "证监会", "汇金", "平准", "关税", "制裁", "空袭", "导弹", "封锁",
             "停火", "谈判", "非农", "CPI", "点阵图", "FOMC", "政治局"]
NORMAL_KW = ["央行", "回购", "增持", "减持", "业绩", "订单", "中标", "并购", "重组",
             "限售", "解禁", "龙虎榜", "北向", "融资", "批复", "投产", "涨价"]
STRONG_CLUSTERS = {"中东战争", "伊朗局势", "美联储利率", "国内政策", "中美贸易",
                   "原油能源", "黄金贵金属"}


def _ensure() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT PRIMARY KEY,
            sig TEXT,
            as_of TEXT,
            payload_json TEXT,
            llm_json TEXT,
            conclusion TEXT,
            created_at TEXT,
            notified_at TEXT
        )
    """)
    for _col in ("sig TEXT", "as_of TEXT", "llm_json TEXT", "conclusion TEXT",
                 "notified_at TEXT", "auto_runs INTEGER DEFAULT 0"):
        try:
            db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS {_col}")
        except Exception:
            try:
                db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {_col}")
            except Exception:
                pass
    # 历史解读表（append-only，主键用字符串 run_id ⇒ 不依赖 SERIAL/AUTOINCREMENT 双库差异）
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_RUN_TABLE} (
            run_id TEXT PRIMARY KEY,
            date TEXT,
            ts TEXT,
            trigger TEXT,
            source TEXT,
            conclusion TEXT,
            llm_json TEXT,
            created_at TEXT
        )
    """)
    try:
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_fbr_date_ts ON {_RUN_TABLE} (date, ts)")
    except Exception:
        pass
    _READY = True


def _now() -> datetime:
    from app.flash.rules import beijing_now
    return beijing_now()


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _r2(v) -> Optional[float]:
    n = _num(v)
    return None if n is None else round(n, 2)


# ── ① 大盘 ────────────────────────────────────────────────────────────────
INDEX_LIST = [("sh000001", "上证指数"), ("sz399001", "深证成指"),
              ("sz399006", "创业板指"), ("sh000688", "科创50")]


def _stocks() -> Dict:
    """行情缓存（进程重启/夜间为空时，先尝试从落盘快照恢复一次）。

    ★ 为什么不在接口里现拉全市场：`refresh_all_stocks` 实测 14s+，会阻塞事件循环
      （项目铁律：接口内不做全市场同步拉取）⇒ 只读缓存，缓存空就如实降级。
    """
    try:
        from app.tencent import _cache as tq
        s = (tq or {}).get("stocks") or {}
        if s:
            return s
        from app.tencent import restore_market_snapshot
        restore_market_snapshot(True)
        return (tq or {}).get("stocks") or {}
    except Exception:
        return {}


def _market_section() -> Dict:
    out = {"indices": [], "up": None, "down": None, "flat": None,
           "amount_yi": None, "limit_up": None, "limit_down": None,
           "ratio": None, "as_of": None, "error": ""}
    try:
        from app.tencent import _fetch_tencent
        data = _fetch_tencent(",".join(c for c, _ in INDEX_LIST)) or {}
        for code, name in INDEX_LIST:
            info = data.get(code) or {}
            if _num(info.get("price")):
                out["indices"].append({
                    "name": name, "price": _r2(info.get("price")),
                    "chg_pct": _r2(info.get("change_pct")),
                })
    except Exception as e:
        out["error"] = f"指数: {str(e)[:60]}"

    try:
        from app.tencent import _cache as tq_cache
        stocks = _stocks()
        if stocks:
            up = sum(1 for s in stocks.values() if (_num(s.get("change_pct")) or 0) > 0)
            down = sum(1 for s in stocks.values() if (_num(s.get("change_pct")) or 0) < 0)
            flat = len(stocks) - up - down
            amount = sum((_num(s.get("amount")) or 0) for s in stocks.values())
            # ★ 2026-10-24：涨跌停用项目唯一口径 `limit_stats`（板幅：ST5/10/20/30cm），
            #   不再 `>=9.8` 一刀切（20cm 涨 10% 不是涨停，实测会高估近一倍）。
            try:
                from app import limit_stats as _ls
                lu, ld, ls_src, _lm = _ls.counts_best(stocks)
            except Exception:
                lu, ld, ls_src = None, None, "unavailable"
            out["limit_src"] = ls_src
            out.update({"up": up, "down": down, "flat": flat,
                        "amount_yi": round(amount / 1e8) if amount else None,
                        "limit_up": lu, "limit_down": ld,
                        "ratio": round(up / down, 2) if down else None})
            ts = (tq_cache or {}).get("last_update")
            if ts:
                try:
                    out["as_of"] = datetime.fromtimestamp(float(ts)).strftime("%H:%M")
                except (TypeError, ValueError, OSError):
                    pass
    except Exception as e:
        out["error"] = (out["error"] + f"；涨跌统计: {str(e)[:50]}").strip("；")
    return out


# ── ② 板块 ────────────────────────────────────────────────────────────────
def _sector_section() -> Dict:
    out = {"top": [], "bottom": [], "inflow": [], "outflow": [], "error": ""}
    try:
        from app.eastmoney import get_sectors
        rows = [r for r in (get_sectors("industry", 200) or [])
                if _num(r.get("change_pct")) is not None]
        rows.sort(key=lambda r: _num(r.get("change_pct")) or 0, reverse=True)
        for r in rows[:4]:
            out["top"].append({"name": r.get("name"), "chg_pct": _r2(r.get("change_pct")),
                               "leader": r.get("leader"),
                               "leader_chg": _r2(r.get("leader_change_pct"))})
        for r in rows[-4:][::-1]:
            out["bottom"].append({"name": r.get("name"), "chg_pct": _r2(r.get("change_pct")),
                                  "leader": r.get("leader"),
                                  "leader_chg": _r2(r.get("leader_change_pct"))})
    except Exception as e:
        out["error"] = f"板块: {str(e)[:60]}"
    try:
        from app.eastmoney import get_sector_flow
        flow = [r for r in (get_sector_flow("industry", 100) or [])
                if _num(r.get("net_inflow")) is not None]
        flow.sort(key=lambda r: _num(r.get("net_inflow")) or 0, reverse=True)
        for r in flow[:3]:
            out["inflow"].append({"name": r.get("name"),
                                  "net_inflow_yi": round((_num(r.get("net_inflow")) or 0) / 1e8, 2)})
        for r in flow[-3:][::-1]:
            out["outflow"].append({"name": r.get("name"),
                                   "net_inflow_yi": round((_num(r.get("net_inflow")) or 0) / 1e8, 2)})
    except Exception as e:
        out["error"] = (out["error"] + f"；资金流: {str(e)[:50]}").strip("；")
    return out


# ── ③ 个股异动 ────────────────────────────────────────────────────────────
def _movers_section(limit: int = 6) -> Dict:
    out = {"rows": [], "error": ""}
    try:
        from app.routers.stock import _detect_anomaly
        stocks = _stocks()
        rows = []
        for code, s in stocks.items():
            try:
                types = _detect_anomaly(code, s)
            except Exception:
                types = []
            if not types:
                continue
            nm = str(s.get("name") or "")
            if "退" in nm or "退市" in nm:      # 退市整理股（-59%）不是"异动信号"，是噪音
                continue
            chg = _num(s.get("change_pct")) or 0
            rows.append({
                "code": code, "name": s.get("name") or code,
                "chg_pct": _r2(chg), "turnover": _r2(s.get("turnover_rate")),
                "volume_ratio": _r2(s.get("volume_ratio")),
                "tags": [t.get("type") for t in types if isinstance(t, dict)][:3],
                "severity": max([t.get("severity") or 0 for t in types
                                 if isinstance(t, dict)] or [0]),
            })
        rows.sort(key=lambda r: (-(r["severity"] or 0), -abs(r["chg_pct"] or 0)))
        out["rows"] = rows[:limit]
    except Exception as e:
        out["error"] = f"个股异动: {str(e)[:60]}"
    return out


# ── ④ 重要快讯（规则打分，不烧 LLM）────────────────────────────────────────
def _score_news(content: str, cluster: str) -> int:
    score = 0
    if cluster in STRONG_CLUSTERS:
        score += 2
    if any(k in content for k in STRONG_KW):
        score += 2
    if any(k in content for k in NORMAL_KW):
        score += 1
    if len(content) >= 120:          # 长文多为分析稿，信息量高但也可能是聚合稿
        score += 0
    if any(k in content for k in ("金十数据整理", "重要新闻汇总", "早间要闻")):
        score -= 2
    return score


def _news_section(day: str, scan: int = POLL_NEWS_SCAN, limit: int = NEWS_LIMIT) -> Dict:
    """近 N 条原始快讯 → 规则打分取前 limit 条（带 cluster / 影响范围 / 方向）。

    ⚠️ `day` 参数**有意不使用**：本函数服务于"最新一份"卡片，语义是"**当前**最重要的几条"。
       若按 `time >= day` 过滤，凌晨/休市（当天还没有快讯）会直接空列表，反而更差。
       回放历史判断由 `flash_brief_runs` 负责（存的是当时那份 payload），不靠重算。
    """
    out = {"rows": [], "scanned": 0, "error": ""}
    try:
        rows = db.fetch("SELECT id, time, content, cluster FROM flash_news "
                        "ORDER BY time DESC LIMIT %s", (int(scan),)) or []
    except Exception as e:
        out["error"] = f"快讯: {str(e)[:60]}"
        return out
    out["scanned"] = len(rows)
    scored = []
    for r in rows:
        content = (r.get("content") or "").replace("\n", " ").strip()
        if not content:
            continue
        cluster = r.get("cluster") or "其他"
        sc = _score_news(content, cluster)
        if sc <= 0:
            continue
        scored.append({
            "id": r.get("id"), "time": str(r.get("time") or ""),
            "cluster": cluster, "score": sc,
            "text": content[:400],
            "gist": _template_gist(content),
            "scope": CLUSTER_SCOPE.get(cluster, "—"),
            "direction": _template_direction(content),
            "source": "rule",
        })
    # ★★ 2026-10-28（用户："最新快讯替换的方式吗？更新了哪里感知不强"）：
    #   原 `sort(key=lambda x: (-x["score"], x["time"]), reverse=False)` —— 副键 `time`
    #   是**升序**（旧→新），于是**同分时最旧的高分条排在最前** ⇒ 列表被"最旧的高分新闻"
    #   长期占满，最新快讯排在队尾被 `[:limit]` 截掉。
    #   实测（10-28 00:5x）：Top5 全是 score=4 的旧条（最早 10-26 12:05，最新只到 10-27 19:56），
    #   而 60 条窗口里 10-27 23:49 的最新快讯**根本没进列表** ⇒ 用户"感知不强"。
    #   修法：两次**稳定**排序 —— 先时间降序（新→旧），再分数降序（同分保持时间降序）。
    #   ⚠️ 不能写 `sort(..., reverse=True)` 一把梭：那会把分数也弄成升序。
    scored.sort(key=lambda x: x["time"], reverse=True)
    scored.sort(key=lambda x: x["score"], reverse=True)
    out["rows"] = scored[:limit]
    return out


def _template_gist(text: str) -> str:
    """规则兜底摘要：第一句（到句号/分号/问号），限 60 字。"""
    seg = re.split(r"[。；;！!?？]", text, maxsplit=1)[0]
    seg = seg.strip()
    return (seg[:60] + "…") if len(seg) > 60 else (seg or text[:60])


def _template_direction(text: str) -> str:
    pos = ["涨停", "中标", "增持", "回购", "降准", "降息", "停火", "协议", "超预期", "上调"]
    neg = ["跌停", "减持", "制裁", "空袭", "加息", "下调", "亏损", "立案", "退市"]
    p = sum(1 for k in pos if k in text)
    n = sum(1 for k in neg if k in text)
    return "多" if p > n else ("空" if n > p else "中性")


_SNAP_CACHE = {"ts": 0.0, "day": "", "p": None}


def build_snapshot(day: Optional[str] = None, ttl_sec: int = 20) -> Dict:
    """组装快照（不调 LLM）+ 指纹 sig。

    ★ 20s 进程内缓存：前端 60s 轮询 + 手动解读会在同一分钟内各算一次，
      而板块/指数都是外部请求 ⇒ 缓存一下（跨进程无效，可接受）。
    """
    import time as _t
    d = str(day or _today())[:10]
    if (_SNAP_CACHE["p"] and _SNAP_CACHE["day"] == d
            and _t.time() - (_SNAP_CACHE["ts"] or 0) < ttl_sec):
        return _SNAP_CACHE["p"]
    market = _market_section()
    sectors = _sector_section()
    movers = _movers_section()
    news = _news_section(d)
    as_of = market.get("as_of") or _now().strftime("%H:%M")
    payload = {"date": d, "as_of": as_of, "market": market, "sectors": sectors,
               "movers": movers, "news": news}
    raw = json.dumps({
        "d": d, "m": market.get("indices"), "ratio": market.get("ratio"),
        "amt": market.get("amount_yi"), "lu": market.get("limit_up"),
        "sec": [(x.get("name"), x.get("chg_pct")) for x in sectors.get("top", [])],
        "mv": [(x.get("code"), x.get("chg_pct")) for x in movers.get("rows", [])],
        "n": [(x.get("id"), x.get("score")) for x in news.get("rows", [])],
    }, ensure_ascii=False, sort_keys=True)
    import hashlib
    payload["sig"] = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    _SNAP_CACHE.update({"ts": _t.time(), "day": d, "p": payload})
    return payload


# ── ⑤ LLM 批量解读 ────────────────────────────────────────────────────────
SYSTEM = (
    "你是 A 股盘中『异动解读员』。输入是系统抓到的：大盘指数与量能、涨跌家数、"
    "板块涨跌与主力资金、个股异动（系统规则命中）、以及若干条重要快讯原文。\n"
    "任务：把每一条重要快讯翻译成「发生了什么 / 为什么重要 / 影响谁」，并给一句盘面定性。\n\n"
    "★ 硬约束：\n"
    "1) **只能用输入里出现的数字与事实**，不得编造点位、涨跌幅、公司、政策；缺失就写「数据不足」。\n"
    "2) **不给买卖/仓位建议**，不预测点位。\n"
    "3) 快讯与盘面矛盾时（如利好但指数跌），要指出矛盾，不要强行圆场。\n"
    "4) 影响范围要具体到**板块或个股类别**（如「石油石化/油运」「半导体设备」），不要写「市场情绪」这种空话。\n\n"
    "5) 必须给出**推翻条件**：写出「看到哪些可观测读数就说明我这个结论错了」，"
    "必须是本输入里能观察到的量（指数涨跌幅/涨跌家数/成交额/涨停家数/板块涨跌），"
    "不能写「如果走势反转」这类空话；"
    "**阈值必须由输入数字推导并写明推导式**（如 3690×0.99≈3654），"
    "不得出现输入中不存在的数（2026-10-25 实测：模型曾编造'跌破3650/低于15000亿'，"
    "两个数输入里都没有）。\n\n"
    "★ 只输出一个 JSON 对象（不要 markdown、不要代码围栏）：\n"
    '{"conclusion": "<盘面定性；若快讯中有改变 A 股风险偏好的信息，点名最关键的几条（各一句话，不限总字数；快讯与盘面一致时如实说明即可）>", '
    '"invalidate": "<推翻条件：≤50字，1~2 条可观测阈值>", '
    '"market": "<一句话，引用指数/涨跌家数/成交额说明大盘>", '
    '"sectors": [{"name":"板块","note":"轮动含义，≤30字"}], '
    '"movers": [{"name":"个股","note":"异动原因，≤30字"}], '
    '"news": [{"idx":1,"gist":"一句话摘要≤40字","cause":"原因≤40字",'
    '"scope":"影响范围（板块/个股类别）",'
    '"direction":"利好/利空/中性（对 A 股整体风险偏好，不是对油价/黄金）"}]}'
)


def _user_prompt(p: Dict) -> str:
    m, s, mv, n = p.get("market") or {}, p.get("sectors") or {}, \
        p.get("movers") or {}, p.get("news") or {}
    lines = [f"## 交易日 {p.get('date')}（数据时点 {p.get('as_of')}）", "", "### 大盘"]
    for x in m.get("indices") or []:
        lines.append(f"- {x.get('name')} {x.get('price')}（{x.get('chg_pct')}%）")
    lines.append(f"- 涨/跌/平：{m.get('up')}/{m.get('down')}/{m.get('flat')}"
                 f"；涨跌比 {m.get('ratio')}；成交额 {m.get('amount_yi')} 亿")
    lines.append(f"- 涨停/跌停（板幅精确口径 limit_stats）：{m.get('limit_up')}/{m.get('limit_down')}")
    lines += ["", "### 板块（涨跌幅）"]
    lines += [f"- 领涨 {x.get('name')} {x.get('chg_pct')}%（龙头 {x.get('leader')}"
              f" {x.get('leader_chg')}%）" for x in (s.get("top") or [])]
    lines += [f"- 领跌 {x.get('name')} {x.get('chg_pct')}%" for x in (s.get("bottom") or [])]
    lines += ["", "### 板块主力资金（亿元）"]
    lines += [f"- 流入 {x.get('name')} {x.get('net_inflow_yi')}" for x in (s.get("inflow") or [])]
    lines += [f"- 流出 {x.get('name')} {x.get('net_inflow_yi')}" for x in (s.get("outflow") or [])]
    lines += ["", "### 个股异动（系统规则命中）"]
    lines += [f"- {x.get('name')}({x.get('code')}) {x.get('chg_pct')}%"
              f"｜换手 {x.get('turnover')}｜量比 {x.get('volume_ratio')}"
              f"｜命中 {'、'.join(x.get('tags') or [])}" for x in (mv.get("rows") or [])]
    lines += ["", f"### 重要快讯（规则打分取前 {len(n.get('rows') or [])} 条，原文截断 400 字）"]
    for i, x in enumerate(n.get("rows") or [], 1):
        lines.append(f"{i}. [{x.get('time')}][{x.get('cluster')}] {x.get('text')}")
    lines += ["", "## 请按 system 的 schema 输出 JSON：news 的 idx 必须对应上面快讯的序号；"
                  "sectors/movers 各最多 3 条，没有就空数组；"
                  "**invalidate 必填**（可观测阈值，不是「走势反转」这种空话）。"]
    return "\n".join(lines)


def _fallback_llm(p: Dict) -> Dict:
    """LLM 不可用/未触发时的模板输出（仍然有信息量，只是没有「原因」推断）。"""
    m = p.get("market") or {}
    idx = (m.get("indices") or [{}])[0]
    concl = f"{idx.get('name') or '指数'} {idx.get('chg_pct')}%"
    if m.get("ratio") is not None:
        concl += f"｜涨跌比 {m.get('ratio')}"
    if m.get("amount_yi"):
        concl += f"｜成交 {m.get('amount_yi')} 亿"
    _nm = idx.get("name") or "指数"
    ratio_v = _num(m.get("ratio"))
    if ratio_v is not None and ratio_v >= 1:
        inv = f"推翻条件：涨跌比回落至 1 以下，或{_nm}由涨转跌"
    elif ratio_v is not None:
        inv = f"推翻条件：涨跌比回升至 1 以上，或{_nm}转涨并站上日内高点"
    else:
        inv = f"推翻条件：{_nm}反向运行超过 1%"
    return {
        "conclusion": concl,
        "invalidate": inv,
        "market": "（模板）" + ("；".join(
            f"{x.get('name')} {x.get('chg_pct')}%" for x in (m.get("indices") or [])) or "数据不足"),
        "sectors": [{"name": x.get("name"), "note": f"{x.get('chg_pct')}%"}
                    for x in (p.get("sectors") or {}).get("top", [])[:3]],
        "movers": [{"name": f"{x.get('name')}({x.get('code')})",
                    "note": "、".join(x.get("tags") or [])}
                   for x in (p.get("movers") or {}).get("rows", [])[:3]],
        "news": [{"idx": i, "gist": x.get("gist"), "cause": "（待解读）",
                  "scope": x.get("scope"), "direction": x.get("direction")}
                 for i, x in enumerate((p.get("news") or {}).get("rows", []), 1)],
    }


def _parse_json(txt: str) -> Optional[Dict]:
    txt = (txt or "").strip()
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


# ── 板块联动（2026-10-26，第②步：消息面投影到「主线板块 Top5」的行上）────────
#   与 `_holdings_impact` 同一套确定性规则（news 文本 × 行业名），不调 LLM、不查外部：
#     · 行业名集合 = `industry_map` 的 value（~50 个，模块级 60s 缓存）⇒ 不会每次查库；
#     · 只匹配「利好/利空」：中性消息不制造"这个板块有消息"的错觉；
#     · 纯派生数据（不落库），50 行业 × 5 条 = 250 次子串判断，成本可忽略。
_SECTOR_ALIAS = {
    "房地产": ["地产"],
    "非银金融": ["券商", "保险"],
    "电子": ["半导体", "芯片", "消费电子"],
    "计算机": ["软件", "信创", "数字经济"],
    "电力设备": ["光伏", "储能", "新能源"],
    "食品饮料": ["白酒", "食品"],
    "医药生物": ["创新药", "医药", "医疗器械"],
    "国防军工": ["军工", "国防"],
    "传媒": ["游戏", "影视"],
    "有色金属": ["黄金", "铜价", "锂矿"],
    "石油石化": ["原油", "石油"],
    "煤炭": ["动力煤", "焦煤"],
    "通信": ["5G", "光模块", "算力"],
    "汽车": ["整车", "汽车零部件"],
}


def _industry_names() -> List[str]:
    try:
        from app.industry_map import get_industry_map
        vals = {str(v).strip() for v in (get_industry_map() or {}).values()}
        return [v for v in vals if len(v) >= 2 and v != "其他"]
    except Exception:
        return []


def _sector_hits(llm_news: list) -> list:
    """news × 行业 → 板块命中列表（供前端在主线板块行显示「消息N」徽标）。

    返回 `[{industry, idx, direction, match, gist, cause}]`，按 (行业, 快讯序号) 去重。
    """
    inds = _industry_names()
    if not inds:
        return []
    out, seen = [], set()
    for n in (llm_news or []):
        d = str(n.get("direction") or "")
        if d not in ("利好", "利空"):
            continue
        hay = " ".join(str(n.get(k) or "") for k in ("gist", "cause", "scope"))
        if not hay.strip():
            continue
        for ind in inds:
            kws = [ind] + _SECTOR_ALIAS.get(ind, [])
            hit_kw = next((k for k in kws if k and k in hay), None)
            if not hit_kw:
                continue
            key = (ind, n.get("idx"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "industry": ind,
                "idx": n.get("idx"),
                "direction": d,
                "match": ("行业命中" if hit_kw == ind else f"行业命中({hit_kw})"),
                "gist": str(n.get("gist") or "")[:60],
                "cause": str(n.get("cause") or "")[:60],
            })
            if len(out) >= 30:
                return out
    return out


def _save_run(day: str, llm: Dict, trigger: str, source: str,
              ts: Optional[str] = None) -> Optional[str]:
    """把**本次解读**追加到历史表（不覆盖），并裁掉每天超量的旧记录。

    run_id 用"时间戳+4位随机"而非自增：SQLite 是 AUTOINCREMENT、PG 是 SERIAL，
    本项目已有 `_ensure` 里的双库差异坑，字符串主键可以直接绕开。

    `ts` 可指定**本次解读的真实时刻**：老数据回填时必须传主表的 `created_at`，
    否则 tab 上会显示"刚补的这一刻"，而内容其实是上午生成的（谎报时刻）。
    """
    if not isinstance(llm, dict):
        return None
    now = _now()
    ts_text = str(ts or now.isoformat(timespec="seconds"))
    rid = now.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]
    try:
        db.upsert(_RUN_TABLE, {
            "run_id": rid,
            "date": day,
            "ts": ts_text,
            "trigger": trigger or "auto",
            "source": source or "llm",
            "conclusion": llm.get("conclusion") or "",
            "llm_json": json.dumps(llm, ensure_ascii=False),
            "created_at": now.isoformat(timespec="seconds"),
        }, conflict_columns=["run_id"])
    except Exception as e:
        print(f"[flash_brief] 历史解读写入失败（不影响本次返回）: {str(e)[:100]}")
        return None
    # 只留最近 RUN_KEEP 条（LIMIT 子查询 SQLite/PG 都支持）
    try:
        db.execute(
            f"DELETE FROM {_RUN_TABLE} WHERE date = %s AND run_id NOT IN "
            f"(SELECT run_id FROM {_RUN_TABLE} WHERE date = %s ORDER BY ts DESC LIMIT {RUN_KEEP})",
            (day, day))
    except Exception as e:
        print(f"[flash_brief] 历史解读裁剪失败: {str(e)[:80]}")
    return rid


def list_runs(day: str, limit: int = 8) -> List[Dict]:
    """当日历史解读的**精简列表**（不含正文）：供前端 tab 用。

    ⚠️ 只回 run_id/时间/触发原因/来源/结论摘要 —— 全文另走 `get_run()`，
      否则 60s 轮询每次都要传 8×几 KB（本项目对 egress 有硬要求）。
    """
    try:
        rows = db.fetch(
            f"SELECT run_id, ts, trigger, source, conclusion FROM {_RUN_TABLE} "
            f"WHERE date = %s ORDER BY ts DESC LIMIT {int(limit)}", (day,)) or []
    except Exception as e:
        print(f"[flash_brief] 历史解读读取失败: {str(e)[:80]}")
        return []
    out = []
    for r in rows:
        c = str(r.get("conclusion") or "")
        out.append({
            "run_id": r.get("run_id"),
            "ts": r.get("ts"),
            "time": str(r.get("ts") or "")[11:16],      # HH:MM（前端 tab 标签直接用）
            "trigger": r.get("trigger") or "",
            "source": r.get("source") or "",
            "conclusion": c[:160],                      # 摘要足够 tab 悬停
        })
    return out


def get_run(run_id: str) -> Dict:
    """取某次历史解读的全文（含 llm.news 逐条）。找不到返回 {ok: False}。"""
    try:
        row = db.fetch_one(f"SELECT * FROM {_RUN_TABLE} WHERE run_id = %s", (run_id,))
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {str(e)[:80]}"}
    if not row:
        return {"ok": False, "error": "该解读不存在或已被清理"}
    try:
        llm = json.loads(row.get("llm_json") or "{}")
    except (ValueError, TypeError):
        llm = {}
    return {"ok": True, "run_id": row.get("run_id"), "date": row.get("date"),
            "ts": row.get("ts"), "trigger": row.get("trigger"),
            "source": row.get("source"), "conclusion": row.get("conclusion") or "",
            "llm": llm}


def analyze(day: Optional[str] = None, refresh: bool = False,
            auto: bool = False) -> Dict:
    """生成/读取快讯解读。`refresh=False` 且缓存新鲜 ⇒ 不烧 LLM。"""
    p = build_snapshot(day)
    d, sig = p["date"], p["sig"]
    _ensure()

    row = None
    try:
        row = db.fetch_one(f"SELECT * FROM {_TABLE} WHERE date = %s", (d,))
    except Exception:
        row = None

    ttl = AUTO_TTL_MIN if auto else TTL_MIN
    if row and not refresh:
        fresh = False
        try:
            ts = datetime.fromisoformat(str(row.get("created_at")))
            now = _now()   # aware（北京时间）
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=now.tzinfo)   # 历史行无 tz ⇒ 按北京时间解释
            # ★ 2026-10-24（BK-52）：旧实现 `_now().replace(tzinfo=None) - ts` =
            #   naive − aware ⇒ 必抛 TypeError 被吞 ⇒ fresh 恒 False，
            #   "TTL 内读缓存"永不命中（潜伏成本漏洞）
            fresh = (now - ts) < timedelta(minutes=ttl)
        except (TypeError, ValueError):
            fresh = False
        if fresh and row.get("sig") == sig and row.get("llm_json"):
            try:
                llm = json.loads(row["llm_json"])
            except (ValueError, TypeError):
                llm = None
            if isinstance(llm, dict):
                # ★ 2026-10-26：缓存命中同样带 `runs`（前端 tab 需要历史列表；
                #   meta 很小 ≈1KB，不破坏 60s 轮询的体积纪律）
                _runs = list_runs(d)
                return {"ok": True, "date": d, "cached": True, "auto": bool(auto),
                        "as_of": p["as_of"], "sig": sig,
                        "snapshot": p, "llm": llm, "runs": _runs,
                        "latest_run_id": (_runs[0]["run_id"] if _runs else ""),
                        "conclusion": llm.get("conclusion") or "",
                        "holdings_impact": _holdings_impact(llm.get("news") or []),
                        "sector_hits": _sector_hits(llm.get("news") or []),
                        "source": "cache"}

    # ── LLM：一次批量（重要条全进同一个 prompt）──
    llm = None
    err = ""
    if p["news"]["rows"] or p["movers"]["rows"]:
        try:
            from app.flash.llm import call_llm
            txt = call_llm(SYSTEM, _user_prompt(p), temperature=0.25,
                           tier="fast", json_mode=True)
            llm = _parse_json(txt)
            if not llm:
                err = "LLM 未按 JSON 输出"
        except Exception as e:
            err = f"LLM 失败: {str(e)[:100]}"
    if not llm:
        llm = _fallback_llm(p)

    try:
        db.upsert(_TABLE, {
            "date": d, "sig": sig, "as_of": p["as_of"],
            "payload_json": json.dumps(p, ensure_ascii=False),
            "llm_json": json.dumps(llm, ensure_ascii=False),
            "conclusion": llm.get("conclusion") or "",
            "created_at": _now().isoformat(timespec="seconds"),
            "notified_at": row.get("notified_at") if row else None,
        }, conflict_columns=["date"])
    except Exception as e:
        print(f"[flash_brief] 落库失败（本次仍返回）: {str(e)[:100]}")

    # ★ 2026-10-26（方案 B）：**只有真正跑了一次解读**才追加历史（缓存命中不追加），
    #   否则 tab 里会出现一堆内容相同的重复条目。
    _src = "llm" if not err else "template"
    _trigger = "auto" if auto else ("manual" if refresh else "read")
    _rid = _save_run(d, llm, _trigger, _src)
    _runs = list_runs(d)

    return {"ok": True, "date": d, "cached": False, "auto": bool(auto),
            "as_of": p["as_of"], "sig": sig, "snapshot": p, "llm": llm,
            "runs": _runs, "latest_run_id": _rid or (_runs[0]["run_id"] if _runs else ""),
            "conclusion": llm.get("conclusion") or "",
            "holdings_impact": _holdings_impact(llm.get("news") or []),
            "sector_hits": _sector_hits(llm.get("news") or []),
            "error": err, "source": _src}




# ── 持仓联动（2026-10-25，用户需求：快讯 × 持仓影响分析）────────────────────
# ★★ 设计红线：
#   ① 持仓**不出网**——LLM prompt 保持只含快讯（README 披露清单零新增）；
#     影响分析 = 本机确定性规则联动（消息 scope/原文 × 持仓行业/名称 × 实时行情
#     × 主力行为状态），不是 LLM 生成。
#   ② Guardrails #5：只输出【影响方向 + 影响逻辑 + 当前个股状态】，不给买卖/
#     加减仓指令；措辞为"受益/承压方向（观察）"，决策由人。
#   ③ 不落库：纯派生数据，读取时现算；60s 进程缓存对齐卡片轮询频次。

_HOLDINGS_CACHE = {"ts": 0.0, "val": []}


def _holdings_impact(llm_news: list) -> list:
    """快讯集合 × 真实持仓 → 每只持仓的影响观察（本机数据联动，60s 进程缓存）。

    匹配规则（任一命中才算关联，中性 direction 不计）：
      ① 名称命中：news 的 gist/cause/scope 提及持仓名称；
      ② 行业命中：news 文本提及持仓所属行业（industry_map）。
    持仓为空 / 无 llm_news ⇒ 返回 []（前端隐藏该区）。
    """
    import time as _t
    now = _t.time()
    if _HOLDINGS_CACHE["ts"] and now - _HOLDINGS_CACHE["ts"] < 60:
        return _HOLDINGS_CACHE["val"]
    out = []
    try:
        from app.database import db
        from app.tencent import _cache as _tq
        from app.industry_map import get_industry_map
        from app.mainforce.state import load_latest

        pf = db.fetch("SELECT code, name, created_at FROM user_portfolio "
                      "ORDER BY code, created_at DESC") or []
        seen = set()
        holdings = []
        for x in pf:
            c = str(x.get("code") or "")
            if c and c not in seen:
                seen.add(c)
                holdings.append({"code": c, "name": str(x.get("name") or c)})
        if holdings:
            codes = [h["code"] for h in holdings]
            stocks = (_tq.get("stocks") or {})
            imap = {}
            try:
                imap = get_industry_map() or {}
            except Exception:
                imap = {}
            states = {}
            try:
                states = load_latest(codes) or {}
            except Exception:
                states = {}
            for h in holdings:
                code, name = h["code"], h["name"]
                q = stocks.get(code) or {}
                st = states.get(code) or {}
                industry = str(imap.get(code) or "").strip()
                hits, ben, bear = [], 0, 0
                for n in (llm_news or []):
                    d = str(n.get("direction") or "")
                    if d not in ("利好", "利空"):
                        continue
                    hay = " ".join(str(n.get(k) or "") for k in ("gist", "cause", "scope"))
                    how = None
                    if name and name in hay:
                        how = "名称命中"
                    elif industry and industry in hay:
                        how = "行业命中"
                    if not how:
                        continue
                    hits.append({"idx": n.get("idx"), "gist": str(n.get("gist") or "")[:60],
                                 "direction": d, "match": how,
                                 "cause": str(n.get("cause") or "")[:60]})
                    if d == "利好":
                        ben += 1
                    else:
                        bear += 1
                lean = ("受益方向" if ben > bear else
                        ("承压方向" if bear > ben else
                         ("多空混合" if hits else "无直接关联")))
                out.append({
                    "code": code, "name": name, "industry": industry,
                    "price": _num(q.get("price")), "chg_pct": _num(q.get("change_pct")),
                    "phase": st.get("phase"), "signal": st.get("signal"),
                    "hits": hits, "lean": lean + "（观察）",
                })
    except Exception as e:
        print(f"[flash_brief] 持仓联动失败（不影响解读本体）: {type(e).__name__}: {str(e)[:80]}")
        out = []
    _HOLDINGS_CACHE.update(ts=now, val=out)
    return out


def load(day: Optional[str] = None) -> Dict:
    """只读：返回缓存（可能为空）—— 前端轮询用，**绝不触发 LLM**。"""
    d = str(day or _today())[:10]
    _ensure()
    try:
        row = db.fetch_one(f"SELECT * FROM {_TABLE} WHERE date = %s", (d,))
    except Exception:
        row = None
    if not row:
        return {"ok": False, "date": d, "error": "当日尚未生成解读"}
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (ValueError, TypeError):
        payload = {}
    try:
        llm = json.loads(row.get("llm_json") or "{}")
    except (ValueError, TypeError):
        llm = {}
    # ★ 2026-10-24：落库的 snapshot 会"定格"在生成那一刻。盘中读数分钟级变化 ⇒
    #   超过 10 分钟就**重算快照**（20s 进程缓存兜底），只复用 LLM 结论，
    #   避免「卡片上的指数/涨跌比停在上午 10 点」。LLM 不会因此重跑。
    stale_snap = True
    try:
        ts = datetime.fromisoformat(str(row.get("created_at")))
        stale_snap = (_now().replace(tzinfo=None) - ts) > timedelta(minutes=10)
    except (TypeError, ValueError):
        stale_snap = True
    if stale_snap:
        try:
            payload = build_snapshot(d)
        except Exception:
            pass
    # ★ 2026-10-26（方案 B）：带上历史解读的**精简列表**（≈1KB）。
    #   老数据回填：本功能上线前生成的当日解读没有历史行 ⇒ 用主表内容补一条，
    #   否则用户当天看到"0 个 tab"会以为坏了。
    _runs = list_runs(d)
    if not _runs and llm:
        # 用主表 `created_at` 作为这次"历史解读"的真实时刻（不能用"现在"）
        _save_run(d, llm, "auto", "backfill", ts=row.get("created_at"))
        _runs = list_runs(d)
    return {"ok": True, "date": d, "cached": True, "as_of": row.get("as_of"),
            "sig": row.get("sig"), "snapshot": payload, "llm": llm,
            "conclusion": row.get("conclusion") or "",
            "runs": _runs, "latest_run_id": (_runs[0]["run_id"] if _runs else ""),
            "created_at": row.get("created_at"),
            "notified_at": row.get("notified_at"),
            "holdings_impact": _holdings_impact((llm or {}).get("news") or []),
            "sector_hits": _sector_hits((llm or {}).get("news") or [])}


# ── ⑥ 盘中自动触发（系统级）──────────────────────────────────────────────
_last_auto_ts = {"ts": 0.0}


def _daily_auto_count(d: str) -> int:
    """当日自动解读已执行次数。

    ★ 2026-10-24（BK-52）：旧实现 `COUNT(*) WHERE date=d`——本表 date 是主键
      （一天最多一行）⇒ 计数恒 ≤1，`FLASH_BRIEF_DAILY_MAX`（默认 8）**永不生效**，
      自动解读实际只受冷却约束。⇒ 改为行上 `auto_runs` 计数列（_ensure 幂等补列，
      每次自动解读成功后 +1）。
    """
    try:
        row = db.fetch_one(f"SELECT COALESCE(auto_runs, 0) AS c FROM {_TABLE} WHERE date = %s",
                           (d,))
        return int((row or {}).get("c") or 0)
    except Exception:
        return 0


def detect_trigger(p: Dict) -> List[str]:
    """命中的异动理由（空 = 不触发）。只看**可观测阈值**，不做主观判断。"""
    reasons = []
    m = p.get("market") or {}
    for x in m.get("indices") or []:
        chg = _num(x.get("chg_pct"))
        if chg is not None and abs(chg) >= IDX_TRIGGER:
            reasons.append(f"{x.get('name')}{chg:+.2f}%")
    ratio = _num(m.get("ratio"))
    if ratio is not None and (ratio <= RATIO_LOW or ratio >= RATIO_HIGH):
        reasons.append(f"涨跌比{ratio}")
    # 近 30 分钟内的强快讯条数
    strong = 0
    try:
        cutoff = (_now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        rows = db.fetch("SELECT content, cluster, time FROM flash_news "
                        "WHERE time >= %s ORDER BY time DESC LIMIT 50", (cutoff,)) or []
        for r in rows:
            if _score_news((r.get("content") or ""), r.get("cluster") or "其他") >= 4:
                strong += 1
    except Exception:
        pass
    if strong >= NEWS_TRIGGER:
        reasons.append(f"30min强快讯{strong}条")
    return reasons


def maybe_auto_run() -> Optional[Dict]:
    """调度器每 2 分钟调用：只有命中异动 + 冷却到期 + 未超日上限才真跑。

    非交易时段直接返回 None（不烧 token）。
    """
    try:
        from app.flash import rules
        st = rules.get_china_market_status() or {}
        if not st.get("is_open"):
            return None
        import time as _t
        now_ts = _t.time()
        if now_ts - (_last_auto_ts.get("ts") or 0.0) < COOLDOWN_MIN * 60:
            return None
        d = _today()
        if _daily_auto_count(d) >= DAILY_MAX:
            print(f"[flash_brief] 当日自动解读已达上限 {DAILY_MAX}，跳过")
            return None
        p = build_snapshot(d)
        reasons = detect_trigger(p)
        if not reasons:
            return None
        print(f"[flash_brief] 触发自动解读：{'、'.join(reasons)}")
        r = analyze(d, refresh=True, auto=True)
        _last_auto_ts["ts"] = now_ts
        if r.get("ok"):
            mark_notified(d)
            # ★ BK-52：自动解读成功 ⇒ 计数 +1（PG upsert 不覆盖该列；sqlite 的
            #   INSERT OR REPLACE 全行替换会重置——单用户本地场景影响可忽略）
            try:
                db.execute(f"UPDATE {_TABLE} SET auto_runs = COALESCE(auto_runs, 0) + 1 "
                           f"WHERE date = %s", (d,))
            except Exception as e:
                print(f"[flash_brief] auto_runs 计数失败: {str(e)[:80]}")
            r["trigger"] = reasons
        return r
    except Exception as e:
        print(f"[flash_brief] 自动触发异常（已忽略）: {str(e)[:120]}")
        return None


def mark_notified(d: str) -> None:
    try:
        db.execute(f"UPDATE {_TABLE} SET notified_at = %s WHERE date = %s",
                   (_now().isoformat(timespec="seconds"), str(d)[:10]))
    except Exception as e:
        print(f"[flash_brief] 标记通知失败: {str(e)[:80]}")


def recent_notifications(since_dt, newer_fn) -> List[Dict]:
    """供 `/flash/notifications`：把「刚生成且已通知」的解读变成一条事件。"""
    out = []
    try:
        rows = db.fetch(f"SELECT date, conclusion, notified_at FROM {_TABLE} "
                        f"ORDER BY notified_at DESC LIMIT 3") or []
    except Exception:
        return out
    for r in rows:
        ts = r.get("notified_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace(" ", "T"))
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:            # 落库是北京时间 naive ⇒ 补 +08:00 再比较
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        if since_dt and since_dt.tzinfo is None:
            from datetime import timezone
            since_dt = since_dt.replace(tzinfo=timezone(timedelta(hours=8)))
        if since_dt and dt <= since_dt:
            continue
        out.append({"type": "brief",
                    "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "title": "⚡ 盘中异动解读",
                    "body": (r.get("conclusion") or "")[:120]})
    return out
