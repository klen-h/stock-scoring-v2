# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】宏观组合风险档：`滞胀交易` 状态位（2026-10-30，方案 A）
================================================================================
背景（docs/kimi/20261030_fed_chair_speech_brief.md 第 1 项）：
  主席讲话后确认的组合是「油价↑ + 10Y↓ + 美元↑」—— 这是**滞胀交易**，
  与 9 月的"利率冲击"档（油↑ + 10Y↑）方向相反：
    · 利率冲击：红利/防御最优；
    · 滞胀交易：黄金/能源/短久期占优，**成长股两头受压**（估值靠利率、盈利靠增长）。
  在这种组合下，机械评分（把"10Y 下行"直接当利多）的**因子权重假设不成立**，
  所以"宏观分偏多"是误导样本。

★★ 本模块的红线（用户 2026-10-30 明确）：**不覆盖 direction.score / mechanical_score**。
  只**并列输出**一个组合风险档，由下游/人自行取舍。
  理由：覆盖分数会直接改变闸门与仓位，属于"用未回测的判断替换已验证的机械规则"，
  风险远大于收益。状态位是**标注**，不是**改写**。

--------------------------------------------------------------------------------
一、判定口径（用户给定，2026-10-30）
--------------------------------------------------------------------------------
    油   : `brent.change_pct > +1.0%`  **或** `brent > 110`        （后者=状态型，非当日变动）
    债   : `us10y.bp_change < 0`
    美元 : `dxy.change_pct > +0.2%`     **或** `dxy > 101`
    三同时 = 滞胀交易成立（raw_hit）

  ★ 为什么油/美元各带一个"绝对水平"分支：日变动为 0 的日子（周末前后常见）会让
    纯变动口径漏判；而"油已经在 110 上方 + 美元在 101 上方"本身就是该组合的环境特征。
    代价是这两个分支会长期为真 ⇒ **实际起决定作用的是油侧**（见回测）。

  **滞回**：开启需**连续 2 日**成立；关闭需**连续 2 日**不满足。
    理由：单日抖动（油价一日反弹）不该让状态位来回翻转；2 日是能滤掉大部分噪音的最小窗口。

--------------------------------------------------------------------------------
二、落库
--------------------------------------------------------------------------------
  `macro_regime_state`：每日一行，**数据缺失也写**（state=unknown + 缺失原因），
  因为"今天没判出来"和"今天不是滞胀"必须能区分（前者是数据问题，后者是结论）。

  `contradiction_validations`：通用置信度校验表（signal_type, evidence jsonb, verdict, created_at），
  本次只作为**基础设施**建好，**不写 fed_event 专用逻辑**（用户第 4 项明确不做事件切片）。
================================================================================
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.database import db

_TABLE = "macro_regime_state"
_CONTRADICT = "contradiction_validations"
_READY = False

# ── 阈值（用户 2026-10-30 给定；env 可调，改阈值请在此留痕）──────────────
STAG_OIL_CHG = float(os.environ.get("STAG_OIL_CHG") or 1.0)       # 布伦特单日涨幅 %
STAG_OIL_LEVEL = float(os.environ.get("STAG_OIL_LEVEL") or 110.0)  # 布伦特绝对水平
STAG_USD_CHG = float(os.environ.get("STAG_USD_CHG") or 0.2)        # 美元指数单日涨幅 %
STAG_USD_LEVEL = float(os.environ.get("STAG_USD_LEVEL") or 101.0)  # 美元指数绝对水平
STAG_ON_DAYS = int(os.environ.get("STAG_ON_DAYS") or 2)            # 开启需连续成立天数
STAG_OFF_DAYS = int(os.environ.get("STAG_OFF_DAYS") or 2)          # 关闭需连续不满足天数

STATE_STAG = "滞胀交易"
STATE_NORMAL = "正常"
STATE_UNKNOWN = "数据不足"


def ensure_tables() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT PRIMARY KEY,
            state TEXT,
            raw_hit INTEGER,
            run_on INTEGER,
            run_off INTEGER,
            evidence TEXT,
            note TEXT,
            created_at TEXT
        )
    """)
    # ★ 通用校验表（用户第 1 项要求；第 4 项的事件切片机制不做，此表只作基础设施）
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_CONTRADICT} (
            id SERIAL PRIMARY KEY,
            signal_type TEXT,
            evidence JSONB,
            verdict TEXT,
            created_at TEXT
        )
    """)
    _READY = True


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(s) -> Optional[datetime]:
    s = str(s or "")[:10].replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _load_panels() -> List[Tuple[datetime, Dict, Dict]]:
    """读 macro_daily 全部快照，按日期正序返回 [(dt, panel, derived)]。

    ★ `macro_daily.date` 是 TEXT 且混着 `2026/9/7` 斜杠格式 ⇒ 必须解析成 datetime 排序
      （字符串排序会把 10 月排到 9 月前面，见 policy_bottom_watch 的同型坑）。
    """
    try:
        rows = db.fetch("SELECT date, data_json FROM macro_daily") or []
    except Exception as e:
        print(f"[stagflation] macro_daily 不可读: {str(e)[:80]}")
        return []
    out = []
    for r in rows:
        dt = _parse_date(r.get("date"))
        if dt is None:
            continue
        try:
            obj = json.loads(r.get("data_json") or "{}")
        except (ValueError, TypeError):
            continue
        out.append((dt, obj.get("panel") or {}, obj.get("derived") or {}))
    out.sort(key=lambda x: x[0])
    return out


def _raw_hit(panel: Dict, derived: Dict) -> Tuple[Optional[bool], Dict]:
    """单日三条件判定。返回 (hit | None, evidence)。

    `None` 表示**数据不足**（不是"不成立"）—— 调用方必须区分，否则会把
    缺数据当成"滞胀结束"，造成状态位误关闭。
    """
    b = panel.get("brent") or {}
    u = panel.get("us10y") or {}
    x = panel.get("dxy") or {}
    brent, bchg = _num(b.get("price")), _num(b.get("change_pct"))
    us10y = _num(u.get("price"))
    bp = _num(derived.get("us10y_bp_change"))
    if bp is None:
        bp = _num(u.get("bp_change"))
    dxy, dchg = _num(x.get("price")), _num(x.get("change_pct"))

    ev = {
        "oil": {"value": brent, "chg_pct": bchg,
                "rule": f"chg>{STAG_OIL_CHG:g}% 或 价格>{STAG_OIL_LEVEL:g}",
                "pass": None},
        "bond": {"value": us10y, "bp_change": bp, "rule": "bp_change<0", "pass": None},
        "usd": {"value": dxy, "chg_pct": dchg,
                "rule": f"chg>{STAG_USD_CHG:g}% 或 价格>{STAG_USD_LEVEL:g}",
                "pass": None},
    }
    if brent is None or us10y is None or dxy is None:
        ev["missing"] = [k for k, v in (("brent", brent), ("us10y", us10y), ("dxy", dxy))
                         if v is None]
        return None, ev

    c_oil = ((bchg is not None and bchg > STAG_OIL_CHG) or brent > STAG_OIL_LEVEL)
    c_bond = (bp is not None and bp < 0)
    c_usd = ((dchg is not None and dchg > STAG_USD_CHG) or dxy > STAG_USD_LEVEL)
    ev["oil"]["pass"] = c_oil
    ev["bond"]["pass"] = c_bond
    ev["usd"]["pass"] = c_usd
    return (c_oil and c_bond and c_usd), ev


def compute(days: int = None) -> Dict:
    """从头重算全部历史（含滞回），把每日状态落库。

    ★ 为什么每次全量重算而不是增量：滞回依赖**前一日状态 + 连续计数**，
      增量写一旦中间断档（日批失败一天）就会永久错位；
      而全量只有几十行 panel，成本可忽略。
    ★ **数据不足的日子也写**（state=数据不足），否则"缺数据"会被误读成"不是滞胀"。
    """
    ensure_tables()
    panels = _load_panels()
    if not panels:
        return {"ok": False, "error": "macro_daily 无可用快照"}

    state = STATE_NORMAL
    run_on = run_off = 0
    rows_written = 0
    timeline: List[Dict] = []
    # 显式带时区：卫生门 naive_now 会拦"无时区的 now 调用"（生产容器是 UTC）
    now = datetime.now(timezone.utc)

    for dt, panel, derived in panels:
        day = dt.strftime("%Y-%m-%d")
        hit, ev = _raw_hit(panel, derived)
        note = ""
        if hit is None:
            # 数据不足：**不动状态与计数**（缺数据既不能开启也不能关闭）
            note = "数据不足：" + ",".join(ev.get("missing") or []) + " 缺失"
            rec_state = STATE_UNKNOWN
            cur_on, cur_off = run_on, run_off
        else:
            if hit:
                run_on += 1
                run_off = 0
            else:
                run_off += 1
                run_on = 0
            if state != STATE_STAG and run_on >= STAG_ON_DAYS:
                state = STATE_STAG
            elif state == STATE_STAG and run_off >= STAG_OFF_DAYS:
                state = STATE_NORMAL
            rec_state = state
            cur_on, cur_off = run_on, run_off

        try:
            db.execute(
                f"INSERT INTO {_TABLE} (date, state, raw_hit, run_on, run_off, "
                f"evidence, note, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                f"ON CONFLICT (date) DO UPDATE SET state=EXCLUDED.state, "
                f"raw_hit=EXCLUDED.raw_hit, run_on=EXCLUDED.run_on, "
                f"run_off=EXCLUDED.run_off, evidence=EXCLUDED.evidence, "
                f"note=EXCLUDED.note",
                (day, rec_state, None if hit is None else int(hit), cur_on, cur_off,
                 json.dumps(ev, ensure_ascii=False), note,
                 now.strftime("%Y-%m-%d %H:%M:%S")),
            )
            rows_written += 1
        except Exception as e:
            print(f"[stagflation] 写库失败 {day}: {str(e)[:80]}")
        timeline.append({"date": day, "state": rec_state,
                         "raw_hit": hit, "run_on": cur_on, "run_off": cur_off})

    # 有状态切换时留一条通用校验记录（表只作基础设施，不做事件切片）
    flips = [t["date"] for i, t in enumerate(timeline)
             if i and t["state"] != timeline[i - 1]["state"]]
    if flips:
        try:
            db.execute(
                f"INSERT INTO {_CONTRADICT} (signal_type, evidence, verdict, created_at) "
                f"VALUES (%s, %s, %s, %s)",
                ("stagflation_flip",
                 json.dumps({"flips": flips[-6:],
                             "thresholds": {"oil_chg": STAG_OIL_CHG, "oil": STAG_OIL_LEVEL,
                                            "usd_chg": STAG_USD_CHG, "usd": STAG_USD_LEVEL,
                                            "on": STAG_ON_DAYS, "off": STAG_OFF_DAYS}},
                            ensure_ascii=False),
                 "ok", now.strftime("%Y-%m-%d %H:%M:%S")),
            )
        except Exception as e:
            print(f"[stagflation] 校验记录失败: {str(e)[:80]}")

    last = timeline[-1] if timeline else {}
    return {"ok": True, "rows": rows_written, "days": len(timeline),
            "current": last.get("state"), "date": last.get("date"),
            "flips": flips[-6:]}


def load(day: str = None) -> Dict:
    """读某日的组合风险档（含 `fallback`，与 crowding_watch 同语义）。"""
    ensure_tables()
    requested = str(day)[:10] if day else ""
    fallback = False
    d = requested
    row = None
    try:
        if d:
            row = db.fetch_one(f"SELECT * FROM {_TABLE} WHERE date=%s", (d,))
            if not row:
                r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE} WHERE date <= %s", (d,))
                latest = str((r or {}).get("d") or "")[:10]
                if latest and latest != d:
                    row = db.fetch_one(f"SELECT * FROM {_TABLE} WHERE date=%s", (latest,))
                    if row:
                        fallback, d = True, latest
        else:
            r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE}")
            d = str((r or {}).get("d") or "")[:10]
            if d:
                row = db.fetch_one(f"SELECT * FROM {_TABLE} WHERE date=%s", (d,))
    except Exception as e:
        return {"date": d, "state": None, "note": f"读取失败: {str(e)[:80]}"}

    if not row:
        return {"date": d, "requested_date": requested, "fallback": False,
                "state": None, "is_stagflation": False, "evidence": {},
                "note": "尚无组合风险档数据（需先跑一次 stagflation.compute）"}

    try:
        ev = json.loads(row.get("evidence") or "{}")
    except (ValueError, TypeError):
        ev = {}
    st = row.get("state")
    return {
        "date": d,
        "requested_date": requested,
        "fallback": fallback,
        "state": st,
        "is_stagflation": (st == STATE_STAG),
        "raw_today": bool(row.get("raw_hit")),
        "run_on": row.get("run_on"),
        "run_off": row.get("run_off"),
        "evidence": ev,
        # ★ 前端文案（用户指定措辞）：只标注，不改分
        "advisory": (f"组合风险档：当前宏观组合为【{STATE_STAG}】，"
                     f"机械评分因子权重假设不成立，仅供参考"
                     if st == STATE_STAG else ""),
        "note": (row.get("note") or "") or
                "滞胀交易=油↑+10Y↓+美元↑（需连续2日成立才开启）；"
                "仅标注组合风险档，**不改 direction.score**。",
    }
