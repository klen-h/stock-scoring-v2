# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】观察器 · LLM 解读（observer review）—— **只解读，不进决策链**
================================================================================
背景（2026-10-24，用户："观察器什么时候能加上 llm 分析？今天的变化确实是一个反弹的机会"）：
  观察器（推演证伪 / 关键位 / 清单 / AI 篮子）已经把"今天发生了什么"量化了，
  但**读数本身不回答**"这次变化意味着什么、算不算机会、还差什么才算数"。
  本模块把这批读数压成一份 digest 交给 LLM，产出：
    · `conclusion` 一句话定性（反弹 / 反转 / 无意义，或"证据不足"）
    · `reasons` 3~6 条依据 + **推翻条件**（用户推演里的"验证信号/推翻条件"口径）
  与 `flash/radar-analysis`（审查**系统自己**的提示是否互相矛盾）分工不同：
  这里审查的是**市场读数**本身。

★ 三条硬约束（写进 prompt，也写在这里）：
  ① **只能基于给定读数**：不编造数字、不引入外部行情；读数缺失就说缺失。
  ② **不给仓位/买卖建议**：观察器不改分、不进闸门、不碰模拟盘（项目红线）。
  ③ **不迎合提问者**：证据不足就直说"这是反弹不是反转"，反之亦然。

★ 成本：一次 LLM 调用；结果按日**落库缓存**（`observer_review` 表），
  30 分钟内且数据指纹未变 ⇒ 直接读缓存；`refresh=True` 强制重算。
  数据指纹（`sig`）= 情景/上证收盘/各信号读数/清单计数 —— 盘中读数变了会自动重算，
  避免"上午生成的解读在收盘后还挂着"（trader_brief 早产简报就踩过这个坑）。
================================================================================
"""
import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.database import db

_TABLE = "observer_review"
_READY = False
CACHE_TTL_MIN = int(os.environ.get("OBS_REVIEW_TTL_MIN") or 30)

# 清单键 → 中文（喂 LLM 用；与前端 PBW_LABELS 同口径）
_PB_LABELS = {
    "nation_team_ann": "国家队公告/汇金申购",
    "amount_dry_up": "成交连续地量",
    "chinext_weekly": "创业板周线止跌",
    "adv_dec_ratio": "涨跌比修复",
    "etf_share": "宽基 ETF 份额放大",
    "bank_catchdown": "强势板块补跌",
}


def _ensure() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT PRIMARY KEY,
            sig TEXT,
            as_of TEXT,
            markdown TEXT,
            conclusion TEXT,
            reasons TEXT,
            created_at TEXT
        )
    """)
    for _col in ("sig TEXT", "as_of TEXT", "conclusion TEXT", "reasons TEXT"):
        try:
            db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS {_col}")
        except Exception:
            try:
                db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {_col}")
            except Exception:
                pass
    _READY = True


def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.0f}" if abs(f - round(f)) < 1e-9 else f"{f:.2f}"


def build_digest(day: Optional[str] = None) -> Dict:
    """把观察器读数压成 (digest 文本, sig, as_of)。**只读既有结果，零新数据源**。"""
    from app import scenario_watch as sw
    from app import key_level_watch as kl
    from app import policy_bottom_watch as pbw

    sc = sw.load(day) or {}
    d = str(sc.get("date") or day or "")[:10]

    # ── 指数（关键位表：收盘/涨跌）──
    klv = kl.load(day) or {}
    kl_rows = klv.get("rows") or []
    as_of = str(klv.get("date") or sc.get("data_as_of") or d)[:10]
    idx_lines = []
    for r in kl_rows:
        nm = r.get("name") or r.get("code")
        det = str(r.get("detail") or "")[:60]
        idx_lines.append(f"- {nm} {_fmt(r.get('close'))}"
                         f"（{_fmt(r.get('chg_pct'))}%）"
                         + (f"｜{det}" if det else ""))

    # ── 情景与 9 项信号 ──
    sig_lines = []
    for s in (sc.get("signals") or []):
        sig_lines.append(f"- {s.get('label')}：{_fmt(s.get('value'))}{s.get('unit') or ''}"
                         f" → **{s.get('lean') or '—'}**"
                         + (f"（{s.get('note')}）" if s.get("note") else ""))

    # ── 政策底清单（优先读已落库结果，避免每次重算）──
    try:
        pb = (pbw.load(d) if hasattr(pbw, "load") else None) or pbw.evaluate(d) or {}
    except Exception:
        pb = {}
    pbitems = pb.get("items") or {}
    pb_lines = []
    for k, it in pbitems.items():
        if not isinstance(it, dict):
            continue
        mark = "✅" if it.get("checked") else ("⬜数据不足" if it.get("insufficient") else "⬜")
        det = str(it.get("detail") or (_fmt(it.get("value")) + (it.get("unit") or "")))[:90]
        pb_lines.append(f"- {mark} {_PB_LABELS.get(k, k)}：{det}")

    # ── 关键结构位（上证 3574 / 3674 / 3681 / 3750）──
    extra_lines = []
    for r in kl_rows:
        for e in (r.get("extra") or []):
            extra_lines.append(f"- {r.get('name')} {_fmt(e.get('level'))}："
                               f"距 {_fmt(e.get('dist_pct'))}%"
                               f"{'（已跌破）' if e.get('broke') else '（在其上）'}"
                               + ("★今日收回" if e.get("reclaimed") else ""))

    anchor = sc.get("anchor") or {}
    chk = sc.get("checklist") or {}
    geo = next((s for s in (sc.get("signals") or []) if s.get("key") == "geo"), {}) or {}

    digest = "\n".join([
        f"## 交易日 {d}｜数据截至 {as_of}（收盘口径）",
        "",
        "### 指数",
        *(idx_lines or ["- （无指数读数）"]),
        "",
        f"### 情景倾向：{sc.get('scenario') or '—'}"
        f"｜多空倾向 {sc.get('lean') or '—'}"
        f"（偏多 {sc.get('n_up', 0)} / 偏空 {sc.get('n_down', 0)}）",
        *(sig_lines or ["- （无信号）"]),
        "",
        "### 关键结构位",
        *(extra_lines or ["- （无）"]),
        (f"- 锚点 {_fmt(anchor.get('level'))}：{anchor.get('state')}"
         f"（收盘 {_fmt(anchor.get('close'))}）；跌破下看 {anchor.get('downside')}；"
         f"{anchor.get('fix')}") if anchor else "- （无锚点）",
        "",
        f"### 政策底清单 {chk.get('n_checked', pb.get('n_checked'))}/"
        f"{chk.get('n_total', pb.get('n_total'))}"
        + (f"｜{chk.get('hint')}" if chk.get("hint") else ""),
        *(pb_lines or ["- （无）"]),
        "",
        "### 地缘（金十快讯 7 日窗口）",
        (f"- {geo.get('note')}" if geo else "- （无）"),
        "",
        "### 关键日历",
        *[f"- {e.get('date')} {e.get('name')}（T-{e.get('t_minus')}d）｜{e.get('note')}"
          for e in (sc.get("events") or [])],
    ])

    raw = json.dumps({
        "d": d, "as_of": as_of, "scenario": sc.get("scenario"),
        "lean": sc.get("lean"), "close": anchor.get("close"),
        "n": [chk.get("n_checked"), chk.get("n_total")],
        "sig": [[s.get("key"), s.get("value"), s.get("lean")] for s in (sc.get("signals") or [])],
    }, ensure_ascii=False, sort_keys=True)
    sig = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    return {"date": d, "digest": digest, "sig": sig, "as_of": as_of}


SYSTEM = (
    "你是 A 股量化系统的『观察器解读员』。观察器每天给出：推演证伪记分卡（9 项信号的"
    "多/空/中性）、指数关键位、政策底确认清单（打勾进度）、地缘快讯热度。\n"
    "你的任务：**判断「今天这个变化」的性质**，并指出它还缺什么才算数。\n\n"
    "★ 硬约束（违反即视为输出无效）：\n"
    "1) **只能使用用户提供的读数**：不得编造任何数字、点位、新闻、日期；读数缺失就写「数据不足」。\n"
    "2) **不给买卖/仓位/加减仓建议**，不预测具体点位。你的产出是「性质判断 + 验证条件」，\n"
    "   不是交易指令（本模块是观察器，不进决策链）。\n"
    "3) **不迎合提问者**：如果读数是「放量长下影但清单仍 1/6、业绩未出清」，就要直说\n"
    "   「属反弹而非反转」，并给出推翻该结论的条件。反之若多项打勾，也要敢说「结构在修复」。\n"
    "4) 单日 K 线不足以定性时，明确写「单日样本不足」，并列出需要连续几日确认的信号。\n\n"
    "★ 输出格式硬要求：**只输出一个 JSON 对象**（不要 markdown、不要代码围栏、不要解释）：\n"
    '{"conclusion": "<一句话，不超过 40 字，回答「今天的变化是什么性质」>", '
    '"reasons": ["<依据，最多 6 条，每条一句；至少 1 条必须以「推翻条件：」开头>"]}'
)


def _user_prompt(digest: str) -> str:
    return (
        f"{digest}\n\n"
        "## 请按 system 的 schema 输出 JSON\n"
        "reasons 建议覆盖（没有就跳过，不要硬凑）：\n"
        "1. **性质**：今天的变化更像「反弹」还是「反转」？用哪几个读数支撑（引用具体数字）。\n"
        "2. **缺口**：清单/信号里还有哪几条没打勾？为什么它们决定性质（如成交额是放量而非地量）。\n"
        "3. **对比**：与历史同类形态相比，这次多/少了什么条件（只能就给定读数比较）。\n"
        "4. **推翻条件**：明确写 1~2 条「看到 X 就说明我这个判断错了」"
        "（以「推翻条件：」开头，X 必须是本 digest 里可观测的量）。\n"
        "5. 结论若为「证据不足/中性」，不要为了给结论而拔高或贬低。"
    )


def analyze(day: Optional[str] = None, refresh: bool = False) -> Dict:
    """生成/读取当日观察器解读（异常不外抛，返回 error 字段）。"""
    built = build_digest(day)
    d, digest, sig, as_of = built["date"], built["digest"], built["sig"], built["as_of"]
    if not d:
        return {"ok": False, "error": "无观察器数据（先跑日批）"}

    _ensure()
    if not refresh:
        try:
            row = db.fetch_one(f"SELECT * FROM {_TABLE} WHERE date = %s", (d,))
        except Exception:
            row = None
        if row and row.get("markdown"):
            fresh = False
            try:
                ts = datetime.fromisoformat(str(row.get("created_at")))
                fresh = (datetime.now() - ts) < timedelta(minutes=CACHE_TTL_MIN)
            except (TypeError, ValueError):
                fresh = False
            if fresh and (row.get("sig") == sig):
                return {"ok": True, "date": d, "cached": True, "as_of": row.get("as_of") or as_of,
                        "markdown": row.get("markdown"),
                        "conclusion": row.get("conclusion") or "",
                        "reasons": _loads(row.get("reasons")),
                        "structured": bool(row.get("conclusion")), "sig": sig}

    try:
        from app.flash.llm import call_llm
        from app.insight import parse_insight
        txt = (call_llm(SYSTEM, _user_prompt(digest), temperature=0.2,
                        tier="fast", json_mode=True) or "").strip()
    except Exception as e:
        return {"ok": False, "date": d, "error": f"LLM 调用失败: {str(e)[:160]}"}
    if not txt:
        return {"ok": False, "date": d, "error": "LLM 返回空（可稍后重试）"}

    ins = parse_insight(txt)
    try:
        db.upsert(_TABLE, {
            "date": d, "sig": sig, "as_of": as_of,
            "markdown": ins["markdown"], "conclusion": ins["conclusion"],
            "reasons": json.dumps(ins["reasons"], ensure_ascii=False),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }, conflict_columns=["date"])
    except Exception as e:
        print(f"[observer_review] 落库失败（本次仍返回）: {str(e)[:100]}")

    return {"ok": True, "date": d, "cached": False, "as_of": as_of,
            "markdown": ins["markdown"], "conclusion": ins["conclusion"],
            "reasons": ins["reasons"], "structured": ins["structured"], "sig": sig}


def _loads(v) -> List[str]:
    try:
        x = json.loads(v or "[]")
        return [str(i) for i in x] if isinstance(x, list) else []
    except (TypeError, ValueError):
        return []
