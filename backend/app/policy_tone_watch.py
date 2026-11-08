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
    ★ 2026-11-07 修订（计划文档 §3.1a 留痕）：新增**用户自管观察词**层（`tone_watch_words`
    表，与冻结关键词表**无关**），命中推企微一条/词/日——推送是提示层不是信号层；
    冻结账本本身依旧零推送、零决策链。
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
# ★ 2026-11-08.1：剔除"资本市场"（套话词，把资金工具条目稀释成"政策定调"——
#   11-08 用户实测报告，计划文档 §3.1b 留痕）；历史行带旧版本号 2026-11-07.1 保留不回改。
KEYWORD_VERSION = "2026-11-08.1"

# 每个关键词最多保留几条样例（原句摘录，供人工读，不构成信号）
MAX_SAMPLES = 2
# 样例文本截断长度
SAMPLE_HEAD = 90

# ── 关键词表（冻结草案 v1.0 → v1.1 实测校准 → v1.2(§3.1b) 剔除"资本市场"；来源 = 计划文档 §3.1）──
# ★ 后为 flash_news 近 30 天实测命中次数（2026-11-07 附录 B），0 = 未发生的预留词
KEYWORDS: Dict[str, List[str]] = {
    "tone": ["中央经济工作会议", "政治局会议", "适度宽松", "超常规", "逆周期调节",
             "更加积极", "积极的财政政策", "稳市机制", "平准基金"],
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


# ══════════════════════════════════════════════════════════════════════════
# 观察词命中提醒（2026-11-07，计划文档 §3.1a 留痕的变更）
# ══════════════════════════════════════════════════════════════════════════
# ★ 与冻结关键词表（KEYWORDS/NEGATIVE_KEYWORDS）**无关**：词表由用户自管
#   （CLI tone_cli.py watch-add/remove/list），加什么词是人的订阅行为，
#   不改判据、不递增 KEYWORD_VERSION、不写 policy_tone_log。
# ★ 推送语义：多词合并一条企微（push_markdown_batched），**每词每日至多 1 条**，
#   幂等靠 last_pushed_at；文案首行写明"提示非信号"（R2）。

_WATCH = "tone_watch_words"
_WATCH_READY = False

# 每词推送正文最多带几条原句
WATCH_MAX_SAMPLES = 2


def _ensure_watch_table() -> None:
    global _WATCH_READY
    if _WATCH_READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_WATCH} (
            word TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            note TEXT,
            last_hit_date TEXT,
            last_pushed_at TEXT,
            created_at TEXT
        )
    """)
    _WATCH_READY = True


def watch_add(word: str, note: str = "") -> Dict:
    """新增观察词（2~30 字；去重幂等，重复添加只更新 note）。"""
    _ensure_watch_table()
    w = str(word or "").strip()
    if not (2 <= len(w) <= 30):
        return {"ok": False, "error": "观察词长度须在 2~30 字之间"}
    db.upsert(_WATCH, {
        "word": w,
        "enabled": 1,
        "note": str(note or "")[:200],
        "created_at": _bj_now().strftime("%Y-%m-%d %H:%M:%S"),
    }, conflict_columns=["word"])
    return {"ok": True, "word": w}


def watch_remove(word: str) -> Dict:
    """移除观察词（软删：enabled=0，保留历史便于审计）。"""
    _ensure_watch_table()
    w = str(word or "").strip()
    if not w:
        return {"ok": False, "error": "word 不能为空"}
    db.execute(f"UPDATE {_WATCH} SET enabled=0 WHERE word=%s", (w,))
    return {"ok": True, "word": w}


def watch_list() -> List[Dict]:
    _ensure_watch_table()
    rows = db.fetch(f"SELECT word, enabled, note, last_hit_date, last_pushed_at, "
                    f"created_at FROM {_WATCH} ORDER BY created_at DESC") or []
    return rows


def match_watch_words(words: List[str], items: List[Dict]) -> Dict[str, List[Dict]]:
    """观察词 × 语料 匹配（**纯函数**，离线可测）。

    返回 {word: [sample, ...]}——只含有命中的词；sample 最多 WATCH_MAX_SAMPLES 条
    （{src, time, head}）。大小写不敏感。
    """
    out: Dict[str, List[Dict]] = {}
    norm = [{"src": str(i.get("src") or "?"), "time": str(i.get("time") or ""),
             "low": str(i.get("text") or "").lower()} for i in (items or [])]
    for w in (words or []):
        needle = str(w or "").strip().lower()
        if len(needle) < 2:
            continue
        samples = []
        for it in norm:
            if needle in it["low"]:
                if len(samples) < WATCH_MAX_SAMPLES:
                    samples.append({"src": it["src"], "time": it["time"]})
                else:
                    break
        if samples:
            out[w] = samples
    return out


def watch_check_push(items: List[Dict], today: str = None,
                     push: bool = True) -> Dict:
    """检查观察词命中并推送（幂等：每词每日至多 1 条）。

    调用方（两处）：
      · `flash/service.poll_flash_once` 盘中轮询（新快讯 → 即时提醒）；
      · 日批 `task_policy_tone_watch` 兜底（轮询停摆期间的漏网命中）。
    幂等性：以 `last_pushed_at` 的日期部分判"今天推过没有"；推送与记账之间
    无原子性 ⇒ 极端情况（推完进程死）可能重复一条——与既有观察器同容忍度。
    """
    _ensure_watch_table()
    day = str(today or _default_day())[:10]
    rows = db.fetch(f"SELECT word, last_pushed_at FROM {_WATCH} "
                    f"WHERE enabled=1") or []
    pending, hit_map = [], {}
    for r in rows:
        w = str(r.get("word") or "")
        if str(r.get("last_pushed_at") or "")[:10] == day:
            continue                      # 今天已推过 ⇒ 幂等跳过
        pending.append(w)
    if not pending:
        return {"ok": True, "pushed": False, "hits": {}, "note": "无待查词或今日已推"}
    hit_map = match_watch_words(pending, items or [])
    if not hit_map:
        return {"ok": True, "pushed": False, "hits": {}, "note": "无命中"}

    now = _bj_now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    for w, samples in hit_map.items():
        lines.append(f"**{w}**（{len(samples)} 条）")
        for s in samples:
            lines.append(f"· {s['time']} ({s['src']})")
    if push:
        # ★★ 2026-11-08：**先推送、确认送达后再记账**（原顺序是反的）。
        #   改前先写 `last_pushed_at` 再推 ⇒ 企微一抖动，记账已落，而
        #   `last_pushed_at[:10] == day` 会把该词**当天后续全部跳过**
        #   ⇒ **静默漏推**。这比重复推一条严重得多：用户以为在盯着，其实早失效。
        #   原注释写"可能重复一条"，与实现正好相反 —— 记录在此免得再被误导。
        #   现在：`push_markdown_batched` 返回 bool（本次一并改造，原签名 `-> None`
        #   把 `_send_batched` 的 ok 丢掉了），**只有 True 才记账**；
        #   False（未配 webhook / 业务推送关闭 / 发送失败）一律不记账，
        #   留给下一次盘中轮询或日批兜底重试。
        #   代价：极端情况重复一条（推成功但记账时进程死）——**可接受的错方向**。
        from app.flash import wechat
        ok = False
        try:
            ok = bool(wechat.push_markdown_batched(
                "🔔 观察词命中（提示非信号）",
                "\n".join(lines) +
                "\n\n> 用户自管观察词命中提醒；检索页可看原句，不构成任何操作建议。"))
        except Exception as e:
            print(f"[tone] 观察词推送异常（不记账，下次重试）: {str(e)[:80]}")
            ok = False
        if not ok:
            return {"ok": True, "pushed": False, "hits": hit_map, "date": day,
                    "error": "推送未确认送达，不记账（下次轮询/日批重试）"}
        for w in hit_map:
            try:
                db.execute(f"UPDATE {_WATCH} SET last_hit_date=%s, "
                           f"last_pushed_at=%s WHERE word=%s", (day, now, w))
            except Exception as e:
                print(f"[tone] 观察词记账失败 {w}: {str(e)[:60]}")
    return {"ok": True, "pushed": bool(push), "hits": hit_map, "date": day}


# ══════════════════════════════════════════════════════════════════════════
# 时滞仪表（计划文档 §4 纪律 3 的第一产出）
# ══════════════════════════════════════════════════════════════════════════
# 「政策底领先市场底 X 天」从叙事变成实测值：口径（冻结）——
#   政策侧 = tone_confirm 首个**非 未定/无会议** 标签日；
#   市场侧 = policy_bottom_watch 首个 n_checked ≥ 4 日。
_TONE_LAG_TAGS = ("宽松延续", "宽松升级", "宽松回退")


# ══════════════════════════════════════════════════════════════════════════
# 三底时序第三层：经济底人工标注（2026-11-08，月粒度）
# ══════════════════════════════════════════════════════════════════════════
# 出处：2008 复盘三底序列（政策底 → 市场底 ≈6 周 → 经济底 ≈1 季度）。
# ★ 为什么是**人工月粒度标注**而不是观察器：经济底是月频/季频的**后验确认**
#   （PMI 连续回荣枯线上、GDP 止跌等），规则化即未检验信号（R2）——
#   与 tone_confirm 同款人工层：人打标，系统只计时与展示。
_ECON = "economy_bottom"
_ECON_READY = False


def _ensure_econ_table() -> None:
    global _ECON_READY
    if _ECON_READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_ECON} (
            period TEXT PRIMARY KEY,
            note TEXT,
            marked_at TEXT
        )
    """)
    _ECON_READY = True


def _valid_period(p) -> Optional[str]:
    """YYYY-MM 校验（**纯函数**，月粒度）。合法返回原值，否则 None。"""
    import re
    s = str(p or "").strip()
    return s if re.match(r"^\d{4}-(0[1-9]|1[0-2])$", s) else None


def _month_diff(period: str, day: str) -> Optional[int]:
    """period(YYYY-MM) 与 day(YYYY-MM-DD) 的月差（period − day；**纯函数**）。

    例：_month_diff("2009-01", "2008-10-28") = 3（2008 复盘的市场底→经济底 ≈1 季度）。
    """
    try:
        return (int(period[:4]) - int(day[:4])) * 12 + (int(period[5:7]) - int(day[5:7]))
    except (TypeError, ValueError):
        return None


def economy_mark(period: str, note: str = "") -> Dict:
    """人工标注经济底（月粒度）。同月重复标注=覆盖改判；不同月=新判并保留历史。"""
    _ensure_econ_table()
    p = _valid_period(period)
    if not p:
        return {"ok": False, "error": "period 须为 YYYY-MM（月粒度，如 2027-01）"}
    db.upsert(_ECON, {"period": p, "note": str(note or "")[:300],
                      "marked_at": _bj_now().strftime("%Y-%m-%d %H:%M:%S")},
              conflict_columns=["period"])
    return {"ok": True, "period": p}


def economy_remove(period: str) -> Dict:
    """撤销某月的经济底标注（低频人工操作，误标纠错用）。"""
    _ensure_econ_table()
    p = _valid_period(period)
    if not p:
        return {"ok": False, "error": "period 须为 YYYY-MM"}
    db.execute(f"DELETE FROM {_ECON} WHERE period=%s", (p,))
    return {"ok": True, "period": p}


def economy_list() -> List[Dict]:
    _ensure_econ_table()
    return db.fetch(f"SELECT period, note, marked_at FROM {_ECON} "
                    f"ORDER BY marked_at DESC") or []


def _current_economy() -> Optional[str]:
    """当前有效的经济底标注（最近一次 marked_at 的 period）。"""
    try:
        _ensure_econ_table()
        r = db.fetch_one(f"SELECT period FROM {_ECON} ORDER BY marked_at DESC LIMIT 1")
        return _valid_period((r or {}).get("period"))
    except Exception:
        return None


def lag_report() -> Dict:
    """**三底时序**：政策底 → 市场底 → 经济底（零网络，只读三表）。

    · 政策底 = tone_confirm 首个**非 未定/无会议** 标签日（人工）
    · 市场底 = policy_bottom_watch 首个 n_checked ≥ 4 日（自动）
    · 经济底 = economy_bottom 最近人工标注（**月粒度**，与数据频率一致）
    """
    ensure_tables()
    try:
        # ⚠️ 双库兼容：不用 PG 专有的 = ANY(%s)（BK-41 同款教训），用 IN + 占位符
        ph = ",".join(["%s"] * len(_TONE_LAG_TAGS))
        tone = db.fetch_one(
            f"SELECT MIN(date) AS d FROM {_CONFIRM} "
            f"WHERE tone_tag IN ({ph})", tuple(_TONE_LAG_TAGS))
        pbw = db.fetch_one(
            "SELECT MIN(date) AS d FROM policy_bottom_watch WHERE n_checked >= 4")
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}

    def _s(v):
        return str(v or "")[:10] or None

    tone_date, pbw_date = _s((tone or {}).get("d")), _s((pbw or {}).get("d"))
    lag = None
    if tone_date and pbw_date:
        try:
            lag = (datetime.strptime(pbw_date, "%Y-%m-%d")
                   - datetime.strptime(tone_date, "%Y-%m-%d")).days
        except Exception:
            lag = None

    # 第三层：经济底（月粒度人工标注）
    econ = _current_economy()
    econ_lag_m = _month_diff(econ, pbw_date) if (econ and pbw_date) else None

    if not tone_date:
        note = "基调未确认（12 月会议周人工打标后开始计时）"
    elif not pbw_date:
        note = "市场底清单尚未达成 4/5"
    else:
        note = (f"政策侧 {tone_date} → 市场侧 {pbw_date}：滞后 {lag} 天"
                f"（2008 年参照 ≈ 42 天：09-18 政策底 → 10-28 市场底）"
                if lag is not None and lag >= 0 else
                f"市场侧 {pbw_date} 早于政策侧 {tone_date}（{-lag} 天）——市场底先行")
    if econ:
        # ★ 边角（2026-11-08 实测）：市场底未确认时谈不上"先后顺序"——
        #   正确的话术是"待计时"，不是拿 None 去比（曾打出"早于市场底 ? 个月"）
        if not pbw_date:
            econ_note = f"经济底 {econ}（人工标注）：待市场底确认后开始第二段计时"
        elif econ_lag_m is not None and econ_lag_m >= 0:
            econ_note = (f"经济底 {econ}（人工标注）：较市场底约 {econ_lag_m} 个月"
                         f"（2008 年参照 ≈ 3 个月；月粒度，与数据频率一致）")
        else:
            econ_note = (f"经济底 {econ}（人工标注）早于市场底 "
                         f"{-econ_lag_m if econ_lag_m is not None else '?'} 个月——顺序异常，建议复核标注")
        note = f"{note}；{econ_note}" if note else econ_note
    else:
        note = f"{note}；经济底未标注（月度复盘人工打标：tone_cli economy mark --period YYYY-MM）"
    return {"ok": True, "tone_date": tone_date, "pbw_date": pbw_date,
            "lag_days": lag,
            "economy_period": econ, "economy_lag_months": econ_lag_m,
            "note": note}
