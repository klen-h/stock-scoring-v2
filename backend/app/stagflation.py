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
    三同时 = 组合成立（raw_hit）

    ★★ 2026-10-31 新增第四变量「黄金」（用户确认做 ①）：
      三同时只说明"这个组合像滞胀"，但**分不开滞胀和衰退前夜** —— 两者在
      油/债/美元三个维度上是**同一组输入**，这是原实现的盲区：
        黄金涨（> +0.2%）= 通胀对冲有效      ⇒ **滞胀交易**（黄金/能源/短久期占优）
        黄金跌（< -0.2%）= 实际利率上行、市场赌需求塌缩
                          ⇒ **衰退前夜**（比滞胀更该降仓：股债金三杀）
      确认窗口同样是连续 2 日；方向未确认（中性/缺数据）时**保守默认滞胀**。
      ⚠️ 为什么用"单日涨跌 + 2 日确认"而不是 N 日累计：与油/债/美元既有口径一致
         （都是单日 + 滞回），不引入第二套时间尺度；±0.2% 死区滤掉噪音。
      ⚠️ 黄金**缺数据**（`gold_dir=None`）不等于"中性"：缺数据时不动方向计数，
         避免用"没有观测"去推翻已有判断。

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
STATE_PRE_RECESSION = "衰退前夜"      # ★ 2026-10-31：三条件 + 黄金跌（实际利率上行）
STATE_NORMAL = "正常"
STATE_UNKNOWN = "数据不足"

# ── 黄金裁决（2026-10-31）────────────────────────────────────────────
# 单日涨跌死区：|chg| <= 该值视为"中性"，不推进方向计数（避免 ±0.05% 的噪音翻转）。
STAG_GOLD_CHG = float(os.environ.get("STAG_GOLD_CHG") or 0.2)
# 方向确认所需连续天数（与组合的 ON/OFF 窗口一致，同为 2 日）。
STAG_GOLD_DAYS = int(os.environ.get("STAG_GOLD_DAYS") or 2)


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


def _raw_hit(panel: Dict, derived: Dict) -> Tuple[Optional[bool], Optional[str], Dict]:
    """单日三条件判定 + 黄金裁决。返回 (hit | None, gold_dir | None, evidence)。

    · `hit=None`    = **组合数据不足**（不是"不成立"）；
    · `gold_dir`    ∈ {"up", "down", "flat", None}；
      `None` = **黄金缺数据**（≠ flat：缺观测不得推翻已有判断）。
    """
    b = panel.get("brent") or {}
    u = panel.get("us10y") or {}
    x = panel.get("dxy") or {}
    g = panel.get("gold") or {}
    brent, bchg = _num(b.get("price")), _num(b.get("change_pct"))
    us10y = _num(u.get("price"))
    bp = _num(derived.get("us10y_bp_change"))
    if bp is None:
        bp = _num(u.get("bp_change"))
    dxy, dchg = _num(x.get("price")), _num(x.get("change_pct"))
    gold, gchg = _num(g.get("price")), _num(g.get("change_pct"))

    ev = {
        "oil": {"value": brent, "chg_pct": bchg,
                "rule": f"chg>{STAG_OIL_CHG:g}% 或 价格>{STAG_OIL_LEVEL:g}",
                "pass": None},
        "bond": {"value": us10y, "bp_change": bp, "rule": "bp_change<0", "pass": None},
        "usd": {"value": dxy, "chg_pct": dchg,
                "rule": f"chg>{STAG_USD_CHG:g}% 或 价格>{STAG_USD_LEVEL:g}",
                "pass": None},
        # ★ 2026-10-31：第四变量（裁决器）。`pass` 语义=支持"滞胀"，
        #   但衰退前夜时黄金跌才是"支持"，故前端改看 `dir` 而非 `pass`。
        "gold": {"value": gold, "chg_pct": gchg,
                 "rule": (f"涨>{STAG_GOLD_CHG:g}%=滞胀 / "
                          f"跌<-{STAG_GOLD_CHG:g}%=衰退前夜 / 其余中性"),
                 "dir": None},
    }
    if brent is None or us10y is None or dxy is None:
        ev["missing"] = [k for k, v in (("brent", brent), ("us10y", us10y), ("dxy", dxy))
                         if v is None]
        return None, None, ev

    c_oil = ((bchg is not None and bchg > STAG_OIL_CHG) or brent > STAG_OIL_LEVEL)
    c_bond = (bp is not None and bp < 0)
    c_usd = ((dchg is not None and dchg > STAG_USD_CHG) or dxy > STAG_USD_LEVEL)
    ev["oil"]["pass"] = c_oil
    ev["bond"]["pass"] = c_bond
    ev["usd"]["pass"] = c_usd

    # 黄金方向（缺数据 → None；死区内 → flat）
    if gchg is None:
        gold_dir = None
    elif gchg > STAG_GOLD_CHG:
        gold_dir = "up"
    elif gchg < -STAG_GOLD_CHG:
        gold_dir = "down"
    else:
        gold_dir = "flat"
    ev["gold"]["dir"] = gold_dir
    ev["gold"]["pass"] = (gold_dir == "up")

    return (c_oil and c_bond and c_usd), gold_dir, ev


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
    gold_up_run = gold_down_run = 0
    rows_written = 0
    timeline: List[Dict] = []
    # 显式带时区：卫生门 naive_now 会拦"无时区的 now 调用"（生产容器是 UTC）
    now = datetime.now(timezone.utc)

    for dt, panel, derived in panels:
        day = dt.strftime("%Y-%m-%d")
        hit, gold_dir, ev = _raw_hit(panel, derived)
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
                # ★ 黄金方向计数：只累计"同向连续"
                if gold_dir == "up":
                    gold_up_run += 1
                    gold_down_run = 0
                elif gold_dir == "down":
                    gold_down_run += 1
                    gold_up_run = 0
                elif gold_dir == "flat":
                    # 死区内 = 方向不明 ⇒ 重置连续性（不把"涨-中性-涨"算成连续）
                    gold_up_run = gold_down_run = 0
                # gold_dir is None（缺数据）⇒ 不动计数，不用"没观测"推翻已有判断
            else:
                run_off += 1
                run_on = 0
                gold_up_run = gold_down_run = 0

            # 退出：组合不成立持续 N 日 ⇒ 回正常（两个风险档都退）
            if state in (STATE_STAG, STATE_PRE_RECESSION) and run_off >= STAG_OFF_DAYS:
                state = STATE_NORMAL
            # 进入/切换：组合成立持续 N 日
            elif run_on >= STAG_ON_DAYS:
                if gold_down_run >= STAG_GOLD_DAYS:
                    state = STATE_PRE_RECESSION
                elif gold_up_run >= STAG_GOLD_DAYS:
                    state = STATE_STAG
                elif state == STATE_NORMAL:
                    # 方向未确认 ⇒ **保守默认滞胀**（不因黄金单日就走更严重的档）
                    state = STATE_STAG
                # 已在风险档且方向未确认 ⇒ 保持，避免来回翻
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
                         "raw_hit": hit, "gold": gold_dir,
                         "run_on": cur_on, "run_off": cur_off})

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
                "state": None, "is_stagflation": False,
                "is_pre_recession": False, "is_alert": False, "evidence": {},
                "note": "尚无组合风险档数据（需先跑一次 stagflation.compute）"}

    try:
        ev = json.loads(row.get("evidence") or "{}")
    except (ValueError, TypeError):
        ev = {}
    st = row.get("state")
    is_stag = (st == STATE_STAG)
    is_pre = (st == STATE_PRE_RECESSION)
    if is_stag:
        advisory = (f"组合风险档：当前宏观组合为【{st}】，"
                    f"机械评分因子权重假设不成立，仅供参考")
    elif is_pre:
        # 衰退前夜比滞胀更该降仓 —— 文案必须让人一眼看出"这不是原来那个档"
        advisory = (f"组合风险档：当前宏观组合为【{st}】"
                    f"（黄金破位=通胀对冲失效，市场在赌需求塌缩），"
                    f"风险高于滞胀档，仅供参考")
    else:
        advisory = ""
    return {
        "date": d,
        "requested_date": requested,
        "fallback": fallback,
        "state": st,
        "is_stagflation": is_stag,
        "is_pre_recession": is_pre,
        # ★ 前端只需判断这一个字段即可决定"是否显示风险档横幅"
        "is_alert": is_stag or is_pre,
        "raw_today": bool(row.get("raw_hit")),
        "run_on": row.get("run_on"),
        "run_off": row.get("run_off"),
        "evidence": ev,
        # ★ 前端文案（用户指定措辞）：只标注，不改分
        "advisory": advisory,
        "note": (row.get("note") or "") or
                "组合风险档=油↑+10Y↓+美元↑（连续2日成立才开启）；"
                "黄金涨⇒滞胀交易 / 黄金跌⇒衰退前夜（方向需连续2日确认，"
                "未确认时默认滞胀）；仅标注组合风险档，**不改 direction.score**。",
    }
