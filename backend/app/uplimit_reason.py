# -*- coding: utf-8 -*-
"""涨停原因归类观察器（盯盘计划 G7，设计冻结于专业盯盘计划 §0.2）——纯事后描述。

题材=zzshare `review_uplimit_reason`（白送映射）；公告/业绩=当日快讯关键词；
优先级：业绩 > 公告 > 题材 > 其他。**归类是事后描述不是预测信号**——不入评分/闸门/推送。
数据全来自 `zz_daily_snapshots` 与既有快讯表 ⇒ 零新增外呼。
"""
import json
import re
import re
from datetime import datetime
from typing import Dict, List, Optional

from app.database import db

_TABLE = "uplimit_reason"
_READY = False

_KW_业绩 = ("业绩预告", "预增", "预亏", "净利", "净利润", "财报", "年报", "季报", "业绩快报")
_KW_公告 = ("公告", "中标", "合同", "回购", "增持", "减持", "重组", "立案", "停牌",
           "股权转让", "股权激励", "签署", "获批")

uplimit_reason_schedule = None  # 占位：防误 import（无调度逻辑在本模块）


def _ensure() -> None:
    global _READY
    if _READY:
        return
    db.execute("""CREATE TABLE IF NOT EXISTS uplimit_reason (
        date TEXT, code TEXT, name TEXT, themes TEXT,
        reason_kind TEXT, evidence_json TEXT, created_at TEXT,
        PRIMARY KEY (date, code))""")
    _READY = True


def _zz_payload(day: str) -> Dict:
    try:
        r = db.fetch_one("SELECT payload FROM zz_daily_snapshots WHERE date=%s", (day,))
        return json.loads((r or {}).get("payload") or "{}")
    except Exception:
        return {}


def _news_blobs(day: str) -> List[str]:
    out = []
    for sql, args in (
        ("SELECT content AS t FROM flash_news WHERE time >= %s AND time < %s",
         (f"{day} 00:00:00", f"{day} 23:59:59")),
        ("SELECT title || '｜' || content AS t FROM jin10_tidy_news WHERE date=%s", (day,)),
    ):
        try:
            out += [str(x.get("t") or "") for x in (db.fetch(sql, args) or [])]
        except Exception:  # noqa: except_pass  某张表/某天缺数据就跳过，多源拼装本就有缺失可能
            pass
    return out


def classify(stocks: List[Dict], themes_of: Dict[str, List[str]],
             blobs: List[str]) -> List[Dict]:
    """纯函数：涨停清单 + 题材映射 + 当日快讯 → 归类行（离线可测）。"""
    rows = []
    for st in stocks:
        code = str(st.get("code") or st.get("stock_code") or "").split(".")[0][-6:]
        if not code:
            continue
        name = str(st.get("name") or "")
        blob_hits = [b for b in blobs if code in b or (name and name in b)]
        kind, ev = "其他", []
        for kw in _KW_业绩:
            hit = next((b for b in blob_hits if kw in b), None)
            if hit:
                kind, ev = "业绩", [f"{kw}：{hit[:80]}"]
                break
        if kind == "其他":
            for kw in _KW_公告:
                hit = next((b for b in blob_hits if kw in b), None)
                if hit:
                    kind, ev = "公告", [f"{kw}：{hit[:80]}"]
                    break
        themes = themes_of.get(code) or []
        if kind == "其他" and themes:
            kind, ev = "题材", [f"题材组：{'、'.join(themes[:2])}"]
        rows.append({"code": code, "name": name, "themes": "、".join(themes[:2]),
                     "reason_kind": kind, "evidence": ev})
    return rows


def snapshot(day: str) -> Dict:
    _ensure()
    p = _zz_payload(day)
    # ★ 2026-11-08 实测校准：slim 后 `uplimit_stocks` 为空 ⇒ 涨停股全集改从
    #   `review_uplimit_reason` 各组 stocks[] 去重（题材→个股映射完整保留，实测 122 组）；
    #   题材名 = `plate_name`（不是 name/theme）。
    themes_of: Dict[str, List[str]] = {}
    stock_map: Dict[str, Dict] = {}
    for grp in (p.get("review_uplimit_reason") or []):
        if not isinstance(grp, dict):
            continue
        theme = str(grp.get("plate_name") or grp.get("name") or "")
        for s in (grp.get("stocks") or []):
            c = str((s or {}).get("code") or (s or {}).get("stock_code") or "").split(".")[0][-6:]
            if not c:
                continue
            if c not in stock_map:
                stock_map[c] = {"code": c, "name": (s or {}).get("name") or ""}
            if theme and theme not in themes_of.setdefault(c, []):
                themes_of[c].append(theme)
    stocks = list(stock_map.values())
    if not stocks:
        return {"ok": False, "date": day, "note": "当日无涨停归因（快照缺失或字段口径变化）"}
    rows = classify(stocks, themes_of, _news_blobs(day))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        db.upsert(_TABLE, {"date": day, "code": r["code"], "name": r["name"],
                           "themes": r["themes"], "reason_kind": r["reason_kind"],
                           "evidence_json": json.dumps(r["evidence"], ensure_ascii=False),
                           "created_at": now}, conflict_columns=["date", "code"])
    kind_count: Dict[str, int] = {}
    for r in rows:
        kind_count[r["reason_kind"]] = kind_count.get(r["reason_kind"], 0) + 1
    return {"ok": True, "date": day, "n": len(rows), "kind_count": kind_count}


def load(day: str) -> Dict[str, Dict]:
    """{code: {reason_kind, themes, evidence}}（limit-review 接线用）。"""
    _ensure()
    try:
        rows = db.fetch(f"SELECT * FROM {_TABLE} WHERE date=%s", (str(day)[:10],)) or []
        out = {}
        for r in rows:
            try:
                ev = json.loads(r.get("evidence_json") or "[]")
            except Exception:
                ev = []
            out[str(r.get("code"))[-6:]] = {
                "reason_kind": r.get("reason_kind"), "themes": r.get("themes"),
                "evidence": ev}
        return out
    except Exception:
        return {}

