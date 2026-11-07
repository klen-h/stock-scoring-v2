# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】政策定调窗口关键词账本（policy tone watch）—— **纯事实日志观察器**
================================================================================
预登记文档：`docs/plans/20261107_政策定调窗口观察计划_v1.md` §3.1（v1.1，2026-11-07 拍板冻结）

★★ 预登记声明（R2/R3，仿 `lidaxiao_views.py` / `macro_regime_class.py`）：
  · **判据已冻结**：关键词表 / 反向词表 / `KEYWORD_VERSION` 冻结于上述文档 v1.1。
    改任何一个词都必须先改文档留痕（追加新小节），再递增 `KEYWORD_VERSION`。
  · **定位是事实日志，不是信号**：命中次数不构成多空判断，不推送企微（默认零推送），
    不进评分 / 不进闸门 / 不碰仓位 / 不碰模拟盘 / 不进 position_hint。
    删掉本模块，系统其余部分照常运行（零决策链影响）。
  · **LLM 不裁决**：`tone_confirm` 的 `tone_tag` 只能由人工确认；
    `suggested_tag` 预留给未来的"建议"通道，默认 pending，且**永远不能覆盖人工已确认的行**。

★ 它补的是哪块缺口：项目全部判市逻辑（regime / scenario / 宏观四分类）都在看
  "市场与世界"，没有一个字节在看"政策说什么"。12 月政治局会议 + 中央经济工作会议
  的通稿措辞是 2026-11~2027-03 观察期内信息量最大的事件——本模块把"读通稿"工程化。

★ 数据源（零新增请求，2026-11-07 实测，见计划文档附录 B）：
    · `flash_news`       金十"爆/沸/热"热榜（~44 条/天；国内政策簇 ≈1.4 条/天）
    · `jin10_tidy_news`  金十整理稿 4 栏目（~55 条/交易日）
  实测结论：**大事件不漏（SFISF 三批/类平准报告/潘功胜表态全部在库），常规条目不齐
  （热榜子集的结构性盲区）** ⇒ 人工确认层（`tone_confirm`）在 12 月会议周是**主通道**，
  快讯命中只是辅助证据。
  ⚠️ `FLASH_COOKIE` 失效 ⇒ `flash_news` 静默断流（`fetch_jin10` 返回空不报错）；
     观察期每日看一眼本模块日志的语料条数，连续 2 日 <10 条 ⇒ 人工补录提示。

★ 日期口径：`date` = **数据所属交易日**（默认 `latest_completed_trading_day()`），
  不是运行日——周末伪日期纪律（同 `macro_regime_class._default_day` 的实测教训）。
================================================================================
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_LOG = "policy_tone_log"
_CONFIRM = "policy_tone_confirm"
_TABLE_READY = False

_BJ = timezone(timedelta(hours=8))

# 判据版本：**改任何关键词必须递增**（同 macro_regime_class.SCORER_VERSION 纪律）
KEYWORD_VERSION = "2026-11-07.1"

# 每个关键词最多保留几条样例（原句摘录，供人工读，不构成信号）
MAX_SAMPLES = 2
# 样例文本截断长度
SAMPLE_HEAD = 90

# ── 关键词表（冻结草案 v1.0 → v1.1 实测校准；来源 = 计划文档 §3.1）──────────
# ★ 后为 flash_news 近 30 天实测命中次数（2026-11-07 附录 B），0 = 未发生的预留词
KEYWORDS: Dict[str, List[str]] = {
    "tone": ["中央经济工作会议", "政治局会议", "适度宽松", "超常规", "逆周期调节",
             "更加积极", "积极的财政政策", "稳市机制", "平准基金", "资本市场"],
    "funds": ["互换便利", "SFISF", "回购增持再贷款", "中央汇金", "国新投资",
              "中国诚通", "险资", "社保基金", "平准"],
    "fundamental": ["PMI", "CPI", "PPI", "社融", "新增贷款", "金融数据",
                    "政府工作报告"],
}
# 反向词表（证伪词表）：宽松叙事怎么结束也要事前写死（2008→2009"动态微调"教训）
# 命中只落账 + 高亮，**不判级、不推送、不触发任何动作**。
NEGATIVE_KEYWORDS: Dict[str, List[str]] = {
    "reverse": ["稳健的货币政策", "动态微调"],
}
CATEGORY_CN = {"tone": "定调", "funds": "资金", "fundamental": "基本面",
               "reverse": "反向"}

# 人工基调标签（冻结草案：计划文档 §3.1 基调裁决表）
TONE_TAGS = ("宽松延续", "宽松升级", "宽松回退", "未定", "无会议")


# ══════════════════════════════════════════════════════════════════════════
# 纯函数：语料条目 → 关键词命中账本（离线可测）
# ══════════════════════════════════════════════════════════════════════════
def compute(items: List[Dict],
            keywords: Dict[str, List[str]] = None,
            negative: Dict[str, List[str]] = None) -> Dict:
    """语料 → 命中账本。**纯函数**（不连库/不调 LLM/不读全局/不改入参）。

    输入 items：[{"src": "flash"|"tidy", "time": str, "text": str}, ...]
      · hit_count = **命中条数**（同一关键词在一条快讯里出现 N 次只算 1 条），
        语义是"今天有几条快讯提到它"，不是"出现了几次"。
      · 拉丁词（SFISF/PMI/CPI/PPI）大小写不敏感。
      · 交叉词（如"平准基金"⊂"平准"、"稳健的货币政策"⊃"货币政策"语义）各自计入
        各自类别——账本是并列视图，不做去重（冻结口径，文档 §3.1 有说明）。

    输出：{"rows": [{category, keyword, hit_count, samples:[{src,time,head}]}...],
           "n_items": int, "by_category": {cat: total_hits}}
      ★ rows 覆盖**全部**冻结关键词（含 0 命中）——0 是"预留词未翻正"的
        召回核对点（计划文档 §7.4），必须显式落库，不能只在命中时写行。
    """
    kw = keywords if keywords is not None else KEYWORDS
    neg = negative if negative is not None else NEGATIVE_KEYWORDS
    merged: Dict[str, List[str]] = dict(kw)
    for cat, words in (neg or {}).items():
        merged[cat] = list(merged.get(cat) or []) + list(words)

    norm = []
    for it in (items or []):
        text = str((it or {}).get("text") or "")
        norm.append({
            "src": str((it or {}).get("src") or "?"),
            "time": str((it or {}).get("time") or ""),
            "text": text,
            "low": text.lower(),
        })

    rows: List[Dict] = []
    by_category: Dict[str, int] = {}
    for cat, words in merged.items():
        cat_total = 0
        for word in words:
            needle = str(word).lower()
            samples = []
            n = 0
            for it in norm:
                if needle and needle in it["low"]:
                    n += 1
                    if len(samples) < MAX_SAMPLES:
                        samples.append({"src": it["src"], "time": it["time"],
                                        "head": it["text"][:SAMPLE_HEAD]})
            rows.append({"category": cat, "keyword": word, "hit_count": n,
                         "samples": samples})
            cat_total += n
        by_category[cat] = cat_total
    return {"rows": rows, "n_items": len(norm), "by_category": by_category}


# ══════════════════════════════════════════════════════════════════════════
# 建表
# ══════════════════════════════════════════════════════════════════════════
def ensure_tables() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_LOG} (
            date TEXT,
            category TEXT,
            keyword TEXT,
            hit_count INTEGER,
            sample_json TEXT,
            keyword_version TEXT,
            updated_at TEXT,
            PRIMARY KEY (date, category, keyword)
        )
    """)
    # suggested_tag：预留给未来"LLM 建议"通道；source ∈ {manual, llm}
    # ★ 人工已确认（source=manual）的行永远不允许被 source=llm 覆盖（铁律：LLM 不能
    #   给自己写成绩单——与 thesis_tracker 同一条）。
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_CONFIRM} (
            date TEXT PRIMARY KEY,
            tone_tag TEXT,
            note TEXT,
            suggested_tag TEXT,
            source TEXT,
            manual_at TEXT,
            created_at TEXT
        )
    """)
    _TABLE_READY = True


# ══════════════════════════════════════════════════════════════════════════
# 取数：两张既有快讯表 → 语料条目（只读，零外呼）
# ══════════════════════════════════════════════════════════════════════════
def _bj_now() -> datetime:
    return datetime.now(_BJ)


def _default_day() -> str:
    """默认日期 = 最近已完成交易日（**不是今天**——周末伪日期纪律，
    与 `macro_regime_class._default_day` 同源同教训）。"""
    try:
        from app.flash.rules import latest_completed_trading_day
        d = str(latest_completed_trading_day())[:10]
        if d:
            return d
    except Exception:
        pass
    return _bj_now().strftime("%Y-%m-%d")


def collect_items(day: str) -> List[Dict]:
    """读当日语料（flash_news 按时间窗、jin10_tidy_news 按日）。只读。"""
    items: List[Dict] = []
    try:
        nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)
               ).strftime("%Y-%m-%d")
        rows = db.fetch("SELECT time, content FROM flash_news "
                        "WHERE time >= %s AND time < %s ORDER BY time",
                        (f"{day} 00:00:00", f"{nxt} 00:00:00")) or []
        for r in rows:
            items.append({"src": "flash", "time": str(r.get("time") or ""),
                          "text": str(r.get("content") or "")})
    except Exception as e:
        print(f"[tone] flash_news 读取失败（按空处理）: {str(e)[:80]}")
    try:
        rows = db.fetch("SELECT title, content, seq FROM jin10_tidy_news "
                        "WHERE date = %s ORDER BY seq", (day,)) or []
        for r in rows:
            title = str(r.get("title") or "")
            body = str(r.get("content") or "")
            items.append({"src": "tidy",
                          "time": f"{day} tidy#{r.get('seq') or 0}",
                          "text": f"{title}｜{body}" if body else title})
    except Exception as e:
        print(f"[tone] jin10_tidy_news 读取失败（按空处理）: {str(e)[:80]}")
    return items


# ══════════════════════════════════════════════════════════════════════════
# 落库 / 读取 / 人工确认
# ══════════════════════════════════════════════════════════════════════════
def snapshot(day: str = None, items: List[Dict] = None,
             write: bool = True) -> Dict:
    """算当日命中账本并落库。`write=False` 只算不写（试跑/核对用）。"""
    ensure_tables()
    day = str(day or _default_day())[:10]
    if items is None:
        items = collect_items(day)
    res = compute(items)
    if not write:
        return {"date": day, **res}

    now = _bj_now().strftime("%Y-%m-%d %H:%M:%S")
    for row in res["rows"]:
        db.upsert(_LOG, {
            "date": day,
            "category": row["category"],
            "keyword": row["keyword"],
            "hit_count": int(row["hit_count"]),
            "sample_json": json.dumps(row["samples"], ensure_ascii=False),
            "keyword_version": KEYWORD_VERSION,
            "updated_at": now,
        }, conflict_columns=["date", "category", "keyword"])
    return {"date": day, **res}


def validate_tag(tone_tag) -> Optional[str]:
    """tone_tag 校验（**纯函数**，供离线单测）。返回 None=合法，否则为错误说明。"""
    tag = str(tone_tag or "").strip()
    if tag not in TONE_TAGS:
        return f"tone_tag 必须是 {TONE_TAGS} 之一，收到「{tag}」"
    return None


def confirm(day: str, tone_tag: str, note: str = "",
            suggested_tag: Optional[str] = None, source: str = "manual") -> Dict:
    """人工基调确认（12 月会议周主通道）。

    · `tone_tag` 必须在 TONE_TAGS 里，否则拒绝（不猜、不自动归类）。
    · `source="llm"` 只能写 `suggested_tag`；**已有人工确认的行不允许被 LLM 覆盖**。
    · 同日重复确认 = 幂等覆盖（允许人改主意），但 `manual_at` 只在 manual 写入时更新。
    """
    ensure_tables()
    tag = str(tone_tag or "").strip()
    err = validate_tag(tag)
    if err:
        return {"ok": False, "error": err}
    prev = db.fetch_one(f"SELECT source FROM {_CONFIRM} WHERE date=%s",
                        (str(day)[:10],))
    if prev and str(prev.get("source") or "") == "manual" and source != "manual":
        return {"ok": False, "error": "该日已有人工确认，LLM 建议不得覆盖（先例：thesis 铁律 2）"}
    now = _bj_now().strftime("%Y-%m-%d %H:%M:%S")
    db.upsert(_CONFIRM, {
        "date": str(day)[:10],
        "tone_tag": tag,
        "note": str(note or "")[:500],
        "suggested_tag": (str(suggested_tag)[:20] if suggested_tag else None),
        "source": "manual" if source == "manual" else "llm",
        "manual_at": now if source == "manual" else None,
        "created_at": now,
    }, conflict_columns=["date"])
    return {"ok": True, "date": str(day)[:10], "tone_tag": tag}


_ROWS_PER_DAY = sum(len(v) for v in {**KEYWORDS, **NEGATIVE_KEYWORDS}.values())


def load(day: str = None, days: int = 7) -> Dict:
    """读最近若干日的命中摘要 + 基调确认。零网络。"""
    ensure_tables()
    try:
        if day:
            rows = db.fetch(f"SELECT * FROM {_LOG} WHERE date=%s "
                            f"ORDER BY category, keyword", (str(day)[:10],)) or []
            confirms = db.fetch(f"SELECT * FROM {_CONFIRM} WHERE date=%s",
                                (str(day)[:10],)) or []
        else:
            rows = db.fetch(f"SELECT * FROM {_LOG} ORDER BY date DESC "
                            f"LIMIT %s", (int(days) * _ROWS_PER_DAY + 10,)) or []
            confirms = db.fetch(f"SELECT * FROM {_CONFIRM} ORDER BY date DESC "
                                f"LIMIT %s", (int(days),)) or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}

    def _j(v):
        try:
            return json.loads(v) if v else None
        except Exception:
            return None

    hit_rows = [{"date": r.get("date"), "category": r.get("category"),
                 "keyword": r.get("keyword"), "hit_count": r.get("hit_count"),
                 "samples": _j(r.get("sample_json")),
                 "keyword_version": r.get("keyword_version")} for r in rows]
    return {"ok": True, "rows": hit_rows,
            "confirms": confirms,
            "note": ("事实日志观察器：命中不构成多空信号，不进决策链；"
                     "tone_tag 仅人工可确认。")}
