"""
矛盾扫描引擎数据持久化层
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from app.database import db


def _now_iso() -> str:
    from app.flash.rules import beijing_now
    return beijing_now().isoformat()


def _today() -> str:
    from app.flash.rules import beijing_now
    return beijing_now().strftime("%Y-%m-%d")


def ensure_tables() -> None:
    """确保矛盾扫描相关表已创建（schema.sql 中已定义，此处作二次保险）。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS contradictions (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            level TEXT NOT NULL,
            type TEXT NOT NULL,
            severity TEXT NOT NULL,
            persistence INTEGER DEFAULT 1,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            signal TEXT,
            resolved INTEGER DEFAULT 0,
            resolved_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, type)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS contradiction_reports (
            date TEXT PRIMARY KEY,
            markdown TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 验证闭环字段（2026-09-06）：predicted_direction/actual_change/validation_result
    # 由 validator.validate_yesterday 回写，形成"识别→预判→验证→统计"学习闭环
    _ensure_validation_columns()


def _ensure_validation_columns() -> None:
    cols = db._use_postgres and [
        r["column_name"] for r in db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='contradictions'")
    ] or None
    has = (lambda c: c in (cols or [])) if cols is not None else None
    stmts = [
        ("validated", "ALTER TABLE contradictions ADD COLUMN validated INTEGER DEFAULT 0"),
        ("validation_date", "ALTER TABLE contradictions ADD COLUMN validation_date TEXT"),
        ("predicted_direction", "ALTER TABLE contradictions ADD COLUMN predicted_direction TEXT"),
        ("actual_change", "ALTER TABLE contradictions ADD COLUMN actual_change DOUBLE PRECISION"),
        ("validation_result", "ALTER TABLE contradictions ADD COLUMN validation_result TEXT"),
    ]
    for name, sql in stmts:
        if has is None:          # sqlite：直接尝试，重复列会报错吞掉
            try:
                db.execute(sql)
            except Exception:
                pass
        elif not has(name):
            db.execute(sql)


def save_contradictions(date: str, items: List[Dict]) -> int:
    """保存某日扫描结果，自动继承 persistence（与上一交易日同类型合并计数）。"""
    ensure_tables()
    if not items:
        return 0

    # 读取上一交易日的 persistence（跳过节假日/非交易日）
    prev_rows = db.fetch(
        "SELECT type, persistence FROM contradictions WHERE date = "
        "(SELECT MAX(date) FROM contradictions WHERE date < %s)",
        (date,)
    )
    prev_map = {r["type"]: r["persistence"] for r in (prev_rows or [])}

    count = 0
    for item in items:
        ctype = item["type"]
        evidence = item.get("evidence") or {}
        # 如果今日已存在同类型，保留并更新；否则新增
        existing = db.fetch_one(
            "SELECT id, persistence FROM contradictions WHERE date = %s AND type = %s",
            (date, ctype))
        persistence = prev_map.get(ctype, 0) + 1
        if existing:
            persistence = max(persistence, existing.get("persistence") or 1)
            db.execute(
                "UPDATE contradictions SET severity=%s, persistence=%s, "
                "summary=%s, evidence_json=%s, signal=%s, created_at=%s "
                "WHERE id=%s",
                (item["severity"], persistence, item["summary"],
                 json.dumps(evidence, ensure_ascii=False), item.get("signal"),
                 _now_iso(), existing["id"]))
        else:
            db.execute(
                "INSERT INTO contradictions (date, level, type, severity, persistence, "
                "title, summary, evidence_json, signal, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (date, item["level"], ctype, item["severity"], persistence,
                 item["title"], item["summary"],
                 json.dumps(evidence, ensure_ascii=False), item.get("signal"),
                 _now_iso()))
        count += 1
    return count


def load_contradictions(date: Optional[str] = None,
                        level: Optional[str] = None,
                        severity: Optional[str] = None,
                        resolved: Optional[int] = None) -> List[Dict]:
    """查询矛盾列表。date 缺省返回库中最新日期。"""
    ensure_tables()
    target = date or load_latest_date() or _today()
    conds = ["date = %s"]
    params = [target]
    if level:
        conds.append("level = %s")
        params.append(level)
    if severity:
        conds.append("severity = %s")
        params.append(severity)
    if resolved is not None:
        conds.append("resolved = %s")
        params.append(resolved)

    sql = "SELECT * FROM contradictions"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY date DESC, level, severity, created_at DESC"
    rows = db.fetch(sql, tuple(params))
    out = []
    for r in rows or []:
        evidence = {}
        try:
            evidence = json.loads(r.get("evidence_json") or "{}")
        except (ValueError, TypeError):
            pass
        out.append({
            "id": r.get("id"),
            "date": r.get("date"),
            "level": r.get("level"),
            "type": r.get("type"),
            "severity": r.get("severity"),
            "persistence": r.get("persistence"),
            "title": r.get("title"),
            "summary": r.get("summary"),
            "evidence": evidence,
            "signal": r.get("signal"),
            "resolved": bool(r.get("resolved")),
            "resolved_note": r.get("resolved_note"),
            "validated": bool(r.get("validated")),
            "validation_date": r.get("validation_date"),
            "predicted_direction": r.get("predicted_direction"),
            "actual_change": r.get("actual_change"),
            "validation_result": r.get("validation_result"),
            "created_at": r.get("created_at"),
        })
    return out


def load_unvalidated_before(before_date: str) -> List[Dict]:
    """读取某日期之前所有未验证的矛盾（验证闭环用）。"""
    ensure_tables()
    rows = db.fetch(
        "SELECT * FROM contradictions WHERE date < %s AND validated = 0 "
        "ORDER BY date ASC", (before_date,))
    out = []
    for r in rows or []:
        out.append({
            "id": r.get("id"), "date": r.get("date"), "level": r.get("level"),
            "type": r.get("type"), "title": r.get("title"),
        })
    return out


def update_validation(cid: int, predicted_direction: str, actual_change: float,
                      result: str, validation_date: str) -> bool:
    """回写矛盾验证结果（预判方向 / 次日实际涨跌 / 正确与否）。"""
    ensure_tables()
    return db.execute(
        "UPDATE contradictions SET validated = 1, predicted_direction = %s, "
        "actual_change = %s, validation_result = %s, validation_date = %s "
        "WHERE id = %s",
        (predicted_direction, actual_change, result, validation_date, cid)) > 0


def validation_stats(days: int = 30) -> Dict:
    """验证闭环统计：整体 + 分类型的预判准确率。"""
    ensure_tables()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetch(
        "SELECT type, validation_result, COUNT(*) AS n FROM contradictions "
        "WHERE validated = 1 AND validation_date >= %s "
        "GROUP BY type, validation_result", (cutoff,))
    by_type = {}
    total = {"correct": 0, "wrong": 0, "flat": 0}
    for r in rows or []:
        t = by_type.setdefault(r["type"], {"correct": 0, "wrong": 0, "flat": 0})
        t[r["validation_result"]] = t.get(r["validation_result"], 0) + r["n"]
        total[r["validation_result"]] = total.get(r["validation_result"], 0) + r["n"]
    def _acc(d):
        n = d["correct"] + d["wrong"] + d["flat"]
        return round(d["correct"] / n * 100, 1) if n else None, n
    for t in by_type.values():
        t["accuracy"], t["n"] = _acc(t)
    acc, n = _acc(total)
    return {"overall": {**total, "accuracy": acc, "n": n}, "by_type": by_type}


def load_latest_date() -> Optional[str]:
    """返回库中最新矛盾日期。"""
    ensure_tables()
    row = db.fetch_one("SELECT date FROM contradictions ORDER BY date DESC LIMIT 1")
    return row["date"] if row else None


def get_contradiction(date: str, ctype: str) -> Optional[Dict]:
    """获取指定日期 + 类型的单条矛盾。"""
    ensure_tables()
    row = db.fetch_one(
        "SELECT * FROM contradictions WHERE date = %s AND type = %s", (date, ctype))
    if not row:
        return None
    evidence = {}
    try:
        evidence = json.loads(row.get("evidence_json") or "{}")
    except (ValueError, TypeError):
        pass
    return {
        "id": row.get("id"),
        "date": row.get("date"),
        "level": row.get("level"),
        "type": row.get("type"),
        "severity": row.get("severity"),
        "persistence": row.get("persistence"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "evidence": evidence,
        "signal": row.get("signal"),
        "resolved": bool(row.get("resolved")),
        "resolved_note": row.get("resolved_note"),
        "created_at": row.get("created_at"),
    }


def mark_resolved(date: str, ctype: str, note: str = "") -> bool:
    """标记某条矛盾已兑现/失效。"""
    ensure_tables()
    r = db.execute(
        "UPDATE contradictions SET resolved = 1, resolved_note = %s WHERE date = %s AND type = %s",
        (note, date, ctype))
    return r > 0


def save_report(date: str, markdown: str) -> None:
    """保存某日矛盾解读报告。"""
    ensure_tables()
    db.upsert("contradiction_reports", {
        "date": date,
        "markdown": markdown,
        "created_at": _now_iso(),
    }, conflict_columns=["date"])


def load_report(date: Optional[str] = None) -> Optional[Dict]:
    """读取报告；date 缺省返回最新。"""
    ensure_tables()
    if date:
        row = db.fetch_one("SELECT * FROM contradiction_reports WHERE date = %s", (date,))
    else:
        row = db.fetch_one("SELECT * FROM contradiction_reports ORDER BY date DESC LIMIT 1")
    return dict(row) if row else None


def summary_by_date(date: Optional[str] = None) -> Dict:
    """按日期聚合矛盾统计（用于前端顶部卡片）。"""
    ensure_tables()
    target = date or load_latest_date() or _today()
    rows = db.fetch(
        "SELECT level, severity, COUNT(*) AS n FROM contradictions WHERE date = %s "
        "GROUP BY level, severity", (target,))
    stats = {}
    total = 0
    for r in rows or []:
        key = f"{r['level']}_{r['severity']}"
        stats[key] = r["n"]
        total += r["n"]
    return {"date": target, "total": total, "breakdown": stats}
