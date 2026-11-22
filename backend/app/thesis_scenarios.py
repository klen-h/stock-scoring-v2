# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】推演登记 + 每日确定性对照 + LLM 修正（2026-11-22，用户："开始做 P0"）
================================================================================

【解决什么】
  用户每周末会自己做一轮"情景推演"（3 个情景 + 概率 + 验证清单），
  但推演只活在聊天记录里：**次日要人工再问一遍 LLM 才能修正**。
  本模块把它变成可登记的资产：
    登记（接口/前端） → 每日收盘自动对照实际数据 → LLM 修正 → 推企微 + 落库

【与 `scenario_watch` 的边界（别搞混）】
  · `scenario_watch`（2026-10-23）：**硬编码**的"磨底 2.0"记分卡，
    情景固定 A/B/C/D、验证项固定 8 条。历史数据有价值，**保持不动**。
  · 本模块：**任意推演可登记**，情景/验证项都由用户定义，且带 LLM 修正。
  两者共用底层数据源（market_amount_daily / market_emotion_daily /
  key_level_watch / breadth_divergence），口径不会漂。

【两条硬纪律】
  1. **取不到数据就标 `nodata`，绝不填 0、绝不猜** ——
     0 会被读成"实现了"，比缺数据危险得多。
  2. **LLM 必须回答"什么情况说明我这次修正也错了"** ——
     用户原始推演最大的毛病就是"周一低开几成定局"没有反面。

【红线】纯复盘，不进决策链：不改评分、不进闸门、不碰模拟盘。
================================================================================
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.database import db

_T_SCEN = "thesis_scenarios"
_T_DAILY = "thesis_daily"
_BJ = timezone(timedelta(hours=8))
_READY = False

# LLM 开关（关了也能跑确定性对照，只是没有"修正建议"）
LLM_ENABLED = (os.environ.get("THESIS_LLM") or "1").strip() != "0"
PUSH_ENABLED = (os.environ.get("THESIS_PUSH") or "1").strip() != "0"
# 只对"仍在有效期内"的推演做每日复盘
MAX_ACTIVE = int(os.environ.get("THESIS_MAX_ACTIVE") or 3)

# 支持的验证项 —— key 是稳定契约，前端/接口都用它
METRIC_LABELS = {
    "sh_close": "上证收盘",
    "sh_chg_pct": "上证涨跌%",
    "amount_yi": "两市成交额(亿)",
    "limit_down": "跌停家数",
    "limit_up": "涨停家数",
    "down_ratio": "下跌占比",
    "breadth_gap": "广度背离(pp)",
}
# 比较符：只支持这四种（避免 eval / 注入）
_OPS = ("<=", ">=", "<", ">", "==")


# ══════════════════════════════════════════════════════════════════════════
#  建表
# ══════════════════════════════════════════════════════════════════════════
def ensure_tables() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_SCEN} (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            horizon_start TEXT,
            horizon_end TEXT,
            scenarios_json TEXT,
            checks_json TEXT,
            invalidators_json TEXT,
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'manual',
            created_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_DAILY} (
            thesis_id TEXT NOT NULL,
            date TEXT NOT NULL,
            checked_json TEXT,
            scenario_now TEXT,
            probs_json TEXT,
            llm_json TEXT,
            pushed INTEGER DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY (thesis_id, date)
        )
    """)
    _READY = True


# ══════════════════════════════════════════════════════════════════════════
#  工具
# ══════════════════════════════════════════════════════════════════════════
def _now() -> str:
    return datetime.now(_BJ).isoformat(timespec="seconds")


def _loads(s) -> list:
    if not s:
        return []
    try:
        v = json.loads(s) if isinstance(s, str) else s
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _dumps(v) -> str:
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return "[]"


def _f(v) -> Optional[float]:
    """转 float；转不动返回 None（**不返回 0** —— 0 会被读成"已实现"）。"""
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cmp(op: str, actual: Optional[float], target: Optional[float]) -> str:
    """pass / fail / nodata。"""
    if actual is None or target is None:
        return "nodata"
    op = op if op in _OPS else "<="
    if op == "<=":
        return "pass" if actual <= target else "fail"
    if op == ">=":
        return "pass" if actual >= target else "fail"
    if op == "<":
        return "pass" if actual < target else "fail"
    if op == ">":
        return "pass" if actual > target else "fail"
    return "pass" if abs(actual - target) < 1e-9 else "fail"


# ══════════════════════════════════════════════════════════════════════════
#  指标取数（确定性，零 LLM）
# ══════════════════════════════════════════════════════════════════════════
def metric_value(key: str, day: str) -> Tuple[Optional[float], str]:
    """取某个验证项的当日实际值 → (value, note)。取不到返回 (None, 原因)。

    ⚠️ 每个分支都必须能回答"为什么没有"—— 空白原因会让人以为指标不存在。
    """
    try:
        if key == "amount_yi":
            r = db.fetch_one(
                "SELECT amount_yi FROM market_amount_daily WHERE date=%s", (day,)) or {}
            v = _f(r.get("amount_yi"))
            return (v, "" if v is not None else "无当日成交额记录")

        if key in ("limit_down", "limit_up", "down_ratio", "up_ratio"):
            r = db.fetch_one("""
                SELECT close_limit_down, close_limit_up, close_up, close_down,
                       up, down
                FROM market_emotion_daily WHERE date=%s
            """, (day,)) or {}
            ld = _f(r.get("close_limit_down"))
            lu = _f(r.get("close_limit_up"))
            up = _f(r.get("close_up"))
            down = _f(r.get("close_down"))
            if key == "limit_down":
                return (ld, "" if ld is not None else "无收盘跌停家数")
            if key == "limit_up":
                return (lu, "" if lu is not None else "无收盘涨停家数")
            if key == "down_ratio":
                if up is None or down is None or (up + down) <= 0:
                    return (None, "无收盘涨跌家数")
                return (round(down / (up + down), 4), "")
            if up is None or down is None or (up + down) <= 0:
                return (None, "无收盘涨跌家数")
            return (round(up / (up + down), 4), "")

        if key in ("sh_close", "sh_chg_pct"):
            r = db.fetch_one("""
                SELECT close, chg_pct FROM key_level_watch
                WHERE date=%s AND code='000001'
            """, (day,)) or {}
            v = _f(r.get("close") if key == "sh_close" else r.get("chg_pct"))
            return (v, "" if v is not None
                    else "无上证指数记录（key_level_watch 当日未落库）")

        if key == "breadth_gap":
            from app import breadth_divergence as bd
            s = bd.snapshot(day)
            v = _f((s or {}).get("gap_pp")) if (s or {}).get("ok") else None
            return (v, "" if v is not None else "广度背离当日无数据")
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）
        print(f"[thesis] metric {key} failed: {str(e)[:80]}")
        return (None, f"取数失败: {str(e)[:40]}")
    return (None, f"未知指标 {key}")


# ══════════════════════════════════════════════════════════════════════════
#  登记 / 查询 / 归档
# ══════════════════════════════════════════════════════════════════════════
def create(payload: Dict) -> Dict:
    """登记一份推演。返回 {ok, id} 或 {ok:False, error}。

    校验（宁可拒绝也不写脏数据）：
      · title / horizon_start / horizon_end 必填
      · scenarios 非空、每项有 name、prob 合计 100±1
      · checks 非空、每项 key 在 METRIC_LABELS 内、op 合法、value 是数字
    """
    ensure_tables()
    title = str(payload.get("title") or "").strip()
    hs = str(payload.get("horizon_start") or "").strip()[:10]
    he = str(payload.get("horizon_end") or "").strip()[:10]
    if not title:
        return {"ok": False, "error": "title 必填"}
    for nm, v in (("horizon_start", hs), ("horizon_end", he)):
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": f"{nm} 需 YYYY-MM-DD"}
    if hs > he:
        return {"ok": False, "error": "horizon_start 不能晚于 horizon_end"}

    scenarios = payload.get("scenarios") or []
    if not isinstance(scenarios, list) or not scenarios:
        return {"ok": False, "error": "scenarios 不能为空"}
    norm_sc = []
    total = 0.0
    for s in scenarios:
        if not isinstance(s, dict):
            return {"ok": False, "error": "scenarios 每项需为对象"}
        name = str(s.get("name") or "").strip()
        prob = _f(s.get("prob"))
        if not name or prob is None:
            return {"ok": False, "error": "scenarios 每项需有 name 与数字 prob"}
        total += prob
        norm_sc.append({"name": name, "prob": round(prob, 1),
                        "path": str(s.get("path") or "").strip()})
    if abs(total - 100) > 1:
        return {"ok": False, "error": f"情景概率合计需为 100（当前 {total:g}）"}

    checks = payload.get("checks") or []
    if not isinstance(checks, list) or not checks:
        return {"ok": False, "error": "checks 不能为空"}
    norm_ck = []
    for c in checks:
        if not isinstance(c, dict):
            return {"ok": False, "error": "checks 每项需为对象"}
        key = str(c.get("key") or "").strip()
        if key not in METRIC_LABELS:
            return {"ok": False,
                    "error": f"未知指标 {key}；可用: {', '.join(METRIC_LABELS)}"}
        op = str(c.get("op") or "<=").strip()
        if op not in _OPS:
            return {"ok": False, "error": f"op 只支持 {_OPS}"}
        val = _f(c.get("value"))
        if val is None:
            return {"ok": False, "error": f"checks[{key}] 的 value 需为数字"}
        norm_ck.append({"key": key,
                        "label": str(c.get("label") or METRIC_LABELS[key]),
                        "op": op, "value": val})

    tid = uuid.uuid4().hex[:12]
    try:
        db.insert(_T_SCEN, {
            "id": tid, "title": title,
            "horizon_start": hs, "horizon_end": he,
            "scenarios_json": _dumps(norm_sc),
            "checks_json": _dumps(norm_ck),
            "invalidators_json": _dumps([
                str(x).strip() for x in (payload.get("invalidators") or [])
                if str(x).strip()]),
            "status": "active",
            "source": str(payload.get("source") or "manual"),
            "created_at": _now(),
        })
    except Exception as e:
        print(f"[thesis] create failed: {str(e)[:100]}")
        return {"ok": False, "error": f"落库失败: {str(e)[:60]}"}

    # 归档旧的同标题推演，避免同一周跑两份
    try:
        db.execute(f"UPDATE {_T_SCEN} SET status='archived' "
                   f"WHERE status='active' AND title=%s AND id<>%s", (title, tid))
    except Exception as e:
        print(f"[thesis] archive same-title failed: {str(e)[:80]}")
    return {"ok": True, "id": tid}


def _row_to_thesis(r: Dict) -> Dict:
    return {
        "id": r.get("id"),
        "title": r.get("title"),
        "horizon_start": r.get("horizon_start"),
        "horizon_end": r.get("horizon_end"),
        "scenarios": _loads(r.get("scenarios_json")),
        "checks": _loads(r.get("checks_json")),
        "invalidators": _loads(r.get("invalidators_json")),
        "status": r.get("status"),
        "source": r.get("source"),
        "created_at": r.get("created_at"),
    }


def list_theses(active_only: bool = False, day: str = None) -> List[Dict]:
    ensure_tables()
    try:
        if active_only:
            d = str(day or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
            rows = db.fetch(f"""
                SELECT * FROM {_T_SCEN}
                WHERE status='active' AND horizon_start<=%s AND horizon_end>=%s
                ORDER BY created_at DESC LIMIT %s
            """, (d, d, MAX_ACTIVE)) or []
        else:
            rows = db.fetch(f"SELECT * FROM {_T_SCEN} "
                            f"ORDER BY created_at DESC LIMIT 50") or []
    except Exception as e:
        print(f"[thesis] list failed: {str(e)[:90]}")
        return []
    return [_row_to_thesis(r) for r in rows]


def get_thesis(tid: str) -> Optional[Dict]:
    ensure_tables()
    try:
        r = db.fetch_one(f"SELECT * FROM {_T_SCEN} WHERE id=%s", (tid,))
    except Exception as e:
        print(f"[thesis] get failed: {str(e)[:80]}")
        return None
    return _row_to_thesis(r) if r else None


def archive(tid: str) -> bool:
    ensure_tables()
    try:
        db.execute(f"UPDATE {_T_SCEN} SET status='archived' WHERE id=%s", (tid,))
        return True
    except Exception as e:
        print(f"[thesis] archive failed: {str(e)[:80]}")
        return False


# ══════════════════════════════════════════════════════════════════════════
#  每日对照（确定性）
# ══════════════════════════════════════════════════════════════════════════
def evaluate_day(thesis: Dict, day: str) -> Dict:
    """逐项对照 → {checked:[...], n_pass, n_fail, n_nodata}。不调 LLM。"""
    checked = []
    for c in thesis.get("checks") or []:
        key = c.get("key")
        val, note = metric_value(key, day)
        verdict = _cmp(c.get("op"), val, c.get("value"))
        checked.append({
            "key": key,
            "label": c.get("label") or METRIC_LABELS.get(key, key),
            "op": c.get("op"), "target": c.get("value"),
            "actual": val, "verdict": verdict,
            "note": note or "",
        })
    return {
        "checked": checked,
        "n_pass": sum(1 for x in checked if x["verdict"] == "pass"),
        "n_fail": sum(1 for x in checked if x["verdict"] == "fail"),
        "n_nodata": sum(1 for x in checked if x["verdict"] == "nodata"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  LLM 修正
# ══════════════════════════════════════════════════════════════════════════
_SYSTEM = """你是交易系统的"推演复盘官"。任务：拿用户**事先登记**的推演，
对照今天已经发生的实际数据，判断哪个情景正在兑现、概率该怎么修正。

铁律：
1. 只复盘，不做新预测，不给买卖建议。
2. 只依据给定的实际数据；**没有的数据就是没有，不许推测或编造**。
3. 必须指出推演里**已经被证伪的假设**（没有就返回空数组）。
4. 必须回答"什么情况说明我这次的修正也错了"（falsifier），这是硬要求。
5. 输出严格 JSON，不要 markdown 代码块。"""


def _prompt(thesis: Dict, day: str, ev: Dict) -> str:
    sc = "\n".join("- %s（%s%%）：%s" % (s.get("name"), s.get("prob"), s.get("path"))
                   for s in thesis.get("scenarios") or [])
    ck = "\n".join(
        "%s %s %s → 实际 %s（%s）%s"
        % (x["label"], x["op"], x["target"],
           "无数据" if x["actual"] is None else x["actual"],
           x["verdict"],
           ("；" + x["note"]) if x.get("note") else "")
        for x in ev.get("checked") or [])
    inv = "\n".join("- " + s for s in thesis.get("invalidators") or []) or "（未登记）"
    return f"""【推演】{thesis.get('title')}（{thesis.get('horizon_start')} ~ {thesis.get('horizon_end')}）

情景：
{sc}

用户登记的证伪条件：
{inv}

【今日实际（{day}）】
{ck}

统计：pass {ev.get('n_pass')} / fail {ev.get('n_fail')} / nodata {ev.get('n_nodata')}

请输出 JSON：
{{
  "scenario_now": "当前最像哪个情景名（必须来自上面情景之一）",
  "probs": {{"情景名": 修正后概率整数, ...}},
  "progress": "一两句话：兑现进度（引用上面的实际数字）",
  "invalidated": ["已被证伪的假设，没有就空数组"],
  "next_watch": ["接下来 1~3 个交易日最该盯的具体指标"],
  "falsifier": "什么情况说明这次修正也错了"
}}"""


def llm_review(thesis: Dict, day: str, ev: Dict) -> Optional[Dict]:
    """一次 LLM 调用；失败返回 None（**不抛** —— 确定性对照已落库，不能因 LLM 丢了）。"""
    if not LLM_ENABLED:
        return None
    try:
        from app.flash import llm
    except Exception as e:
        print(f"[thesis] llm import failed: {str(e)[:80]}")
        return None
    try:
        txt = llm.call_llm(_SYSTEM, _prompt(thesis, day, ev),
                           temperature=0.2, json_mode=True, tier="slow")
    except Exception as e:
        print(f"[thesis] llm call failed: {str(e)[:90]}")
        return None
    if not txt:
        print("[thesis] llm 返回空（provider 未配置/熔断）")
        return None
    s = txt.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    try:
        obj = json.loads(s)
    except ValueError:
        # 容错：截取第一个 { 到最后一个 }
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            print(f"[thesis] llm 非法 JSON: {txt[:80]}")
            return None
        try:
            obj = json.loads(s[i:j + 1])
        except ValueError:
            print(f"[thesis] llm 非法 JSON(2): {txt[:80]}")
            return None
    return obj if isinstance(obj, dict) else None


# ══════════════════════════════════════════════════════════════════════════
#  文本导入（粘贴 → LLM 结构化 → 回填表单，**不落库**）
# ══════════════════════════════════════════════════════════════════════════
# ★★ 2026-11-22（用户："加入一个导入功能，并且有格式举例，内容可以由 llm 生成，
#   自动写入，我到时审阅，有问题我会自行修改并登记"）
#   定位：**只解析、不写库** —— 解析结果回填表单，用户审阅/改数字后自己点登记。
#   这样 LLM 认错指标或编造阈值时，人工一定看得见（直接写库就绕过了审阅）。
MAX_PARSE_CHARS = 8000

_PARSE_SYSTEM = """你是"推演文本结构化解析器"。把用户粘贴的自然语言推演解析成 JSON。

【唯一允许的指标 key】（不在列表里的一律不要硬塞，放进 unparsed）
- sh_close       上证收盘（点数）
- sh_chg_pct     上证涨跌%（百分数，跌为负，如 -1.2）
- amount_yi      两市成交额（亿元）
- limit_down     跌停家数
- limit_up       涨停家数
- down_ratio     下跌占比（0~1 的小数，如 0.95 表示 95% 在跌）
- breadth_gap    广度背离（百分点 pp）

【规则】
1. 只输出 JSON，不要 markdown 代码块、不要解释。
2. scenarios 的 prob 合计必须等于 100；原文没写概率就按你判断给一个合计 100 的分配，
   并在 unparsed 里注明"概率为自动分配"。
3. checks 的 op 只能是 <= >= < > == 之一；value 必须是数字。
4. 原文提到但无法映射到上面 7 个指标的（纳指、台股、汇率、北向、个股价格、政策等），
   **放进 unparsed 数组**，不要塞进 checks。
5. 日期缺失：horizon_start 用今天，horizon_end 用今天+6 天。
6. invalidators（证伪条件）是给复盘用的文字判据，**原样放进 invalidators 即可**，
   **不要**因为里面提到点位/成交额就把它塞进 unparsed —— 只有"验证项/判断依据"
   里出现不可映射内容时才用 unparsed。

【输出格式】
{
  "title": "简短标题",
  "horizon_start": "YYYY-MM-DD",
  "horizon_end": "YYYY-MM-DD",
  "scenarios": [{"name": "情景名", "prob": 40, "path": "路径描述"}],
  "checks": [{"key": "sh_close", "label": "上证收盘", "op": "<=", "value": 3500}],
  "invalidators": ["证伪条件原文"],
  "unparsed": ["无法映射的原文片段 + 原因"]
}"""


def _today() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


def parse_text(text: str) -> Dict:
    """把自由文本解析成可登记草案。返回 {ok, draft, warnings} 或 {ok:False, error}。

    ⚠️ 绝不落库：即使解析成功也只返回草案，由用户在页面上审阅后自己点「登记」。
    """
    t = (text or "").strip()
    if not t:
        return {"ok": False, "error": "文本为空"}
    if len(t) > MAX_PARSE_CHARS:
        return {"ok": False,
                "error": f"文本过长（{len(t)} 字），上限 {MAX_PARSE_CHARS}"}
    if not LLM_ENABLED:
        return {"ok": False, "error": "LLM 已关闭（THESIS_LLM=0），无法解析"}

    try:
        from app.flash import llm
    except Exception as e:
        return {"ok": False, "error": f"LLM 模块不可用: {str(e)[:60]}"}

    try:
        txt = llm.call_llm(_PARSE_SYSTEM, t, temperature=0.1,
                           json_mode=True, tier="slow")
    except Exception as e:
        return {"ok": False, "error": f"LLM 调用失败: {str(e)[:80]}"}
    if not txt:
        return {"ok": False, "error": "LLM 返回空（provider 未配置/熔断）"}

    s = txt.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    try:
        obj = json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return {"ok": False, "error": "LLM 返回非法 JSON"}
        try:
            obj = json.loads(s[i:j + 1])
        except ValueError:
            return {"ok": False, "error": "LLM 返回非法 JSON"}
    if not isinstance(obj, dict):
        return {"ok": False, "error": "LLM 返回不是对象"}

    warnings = []
    for u in (obj.get("unparsed") or []):
        u = str(u).strip()
        if u:
            warnings.append(u)

    # ── 逐项校验/规整（口径与 create 一致，避免"能导入但登记被拒"）──
    title = str(obj.get("title") or "").strip() or "未命名推演"
    hs = str(obj.get("horizon_start") or "").strip()[:10] or _today()
    he = str(obj.get("horizon_end") or "").strip()[:10]
    if not he:
        try:
            he = (datetime.strptime(hs, "%Y-%m-%d")
                  + timedelta(days=6)).strftime("%Y-%m-%d")
        except ValueError:
            he = hs
    for nm, v in (("horizon_start", hs), ("horizon_end", he)):
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": f"解析出的 {nm} 不是 YYYY-MM-DD：{v}"}
    if hs > he:
        hs, he = he, hs
        warnings.append("开始/结束日期被自动对调")

    sc_out = []
    for s_ in (obj.get("scenarios") or []):
        if not isinstance(s_, dict):
            continue
        name = str(s_.get("name") or "").strip()
        prob = _f(s_.get("prob"))
        if not name or prob is None:
            continue
        sc_out.append({"name": name, "prob": round(prob, 1),
                       "path": str(s_.get("path") or "").strip()})
    if not sc_out:
        return {"ok": False, "error": "解析不出现景，请检查文本格式"}
    total = sum(x["prob"] for x in sc_out)
    if abs(total - 100) > 1:
        warnings.append(f"情景概率合计 {round(total, 1)}%，登记前请改成 100%")

    ck_out = []
    dropped = []
    for c_ in (obj.get("checks") or []):
        if not isinstance(c_, dict):
            continue
        key = str(c_.get("key") or "").strip()
        op = str(c_.get("op") or "<=").strip()
        val = _f(c_.get("value"))
        if key not in METRIC_LABELS:
            dropped.append(f"{key or '?'}（不在可用指标）")
            continue
        if op not in _OPS or val is None:
            dropped.append(f"{METRIC_LABELS[key]}（op 或阈值非法）")
            continue
        ck_out.append({"key": key, "label": METRIC_LABELS[key],
                       "op": op, "value": val})
    if dropped:
        warnings.append("已丢弃无法登记的验证项：" + "、".join(dropped))
    if not ck_out:
        return {"ok": False, "error": "解析不出可用验证项（指标需在 7 个内置项内）"}

    inv = [str(x).strip() for x in (obj.get("invalidators") or [])
           if str(x).strip()]

    return {"ok": True,
            "draft": {"title": title, "horizon_start": hs, "horizon_end": he,
                      "scenarios": sc_out, "checks": ck_out,
                      "invalidators": inv},
            "warnings": warnings}


# ══════════════════════════════════════════════════════════════════════════
#  落库 + 推送
# ══════════════════════════════════════════════════════════════════════════
def save_daily(tid: str, day: str, ev: Dict,
               review: Optional[Dict]) -> bool:
    ensure_tables()
    try:
        db.upsert(_T_DAILY, {
            "thesis_id": tid, "date": day,
            "checked_json": _dumps(ev.get("checked")),
            "scenario_now": (review or {}).get("scenario_now"),
            "probs_json": _dumps((review or {}).get("probs") or []),
            "llm_json": _dumps(review) if review else None,
            "pushed": 0,
            "created_at": _now(),
        }, conflict_columns=["thesis_id", "date"])
        return True
    except Exception as e:
        print(f"[thesis] save_daily failed: {str(e)[:90]}")
        return False


def _markdown(thesis: Dict, day: str, ev: Dict,
              review: Optional[Dict]) -> str:
    lines = ["**%s**（%s ~ %s）"
             % (thesis.get("title"), thesis.get("horizon_start"),
                thesis.get("horizon_end")), ""]
    for x in ev.get("checked") or []:
        mark = {"pass": "✓", "fail": "✗", "nodata": "—"}[x["verdict"]]
        act = "无数据" if x["actual"] is None else x["actual"]
        lines.append("%s %s %s %s → 实际 %s"
                     % (mark, x["label"], x["op"], x["target"], act))
    lines.append("")
    lines.append("统计：命中 %d / 偏离 %d / 无数据 %d"
                 % (ev.get("n_pass", 0), ev.get("n_fail", 0), ev.get("n_nodata", 0)))
    if review:
        lines.append("")
        lines.append("**情景：%s**" % review.get("scenario_now", "—"))
        probs = review.get("probs") or {}
        if isinstance(probs, dict) and probs:
            lines.append("概率修正：" + " / ".join(
                "%s %s%%" % (k, v) for k, v in probs.items()))
        if review.get("progress"):
            lines.append("兑现进度：" + str(review["progress"]))
        inv = review.get("invalidated") or []
        if inv:
            lines.append("已证伪：" + "；".join(str(x) for x in inv))
        nw = review.get("next_watch") or []
        if nw:
            lines.append("明日观察：" + "；".join(str(x) for x in nw))
        if review.get("falsifier"):
            lines.append("⚠️ 这次修正的失效条件：" + str(review["falsifier"]))
    else:
        lines.append("")
        lines.append("（LLM 修正不可用：仅确定性对照）")
    lines.append("")
    lines.append("口径：只对照已登记指标，无数据项标「—」，不推测。纯复盘，不进决策链。")
    return "\n".join(lines)


def push(thesis: Dict, day: str, ev: Dict, review: Optional[Dict]) -> bool:
    if not PUSH_ENABLED:
        return False
    try:
        from app.flash import wechat
        return bool(wechat.notify("brief", f"推演复盘 {day}",
                                  _markdown(thesis, day, ev, review)))
    except Exception as e:
        print(f"[thesis] push failed: {str(e)[:90]}")
        return False


# ══════════════════════════════════════════════════════════════════════════
#  日批入口
# ══════════════════════════════════════════════════════════════════════════
def run_daily(day: str = None) -> Dict:
    """取有效推演 → 确定性对照 → LLM 修正 → 落库 → 推送。异常不外抛。"""
    try:
        ensure_tables()
        d = str(day or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
        theses = list_theses(active_only=True, day=d)
        if not theses:
            return {"ok": True, "date": d, "skipped": "无有效推演", "items": []}

        items = []
        for t in theses:
            ev = evaluate_day(t, d)
            rv = llm_review(t, d, ev)
            ok_db = save_daily(t["id"], d, ev, rv)
            pushed = push(t, d, ev, rv) if ok_db else False
            if pushed:
                try:
                    db.execute(f"UPDATE {_T_DAILY} SET pushed=1 "
                               f"WHERE thesis_id=%s AND date=%s", (t["id"], d))
                except Exception as e:
                    print(f"[thesis] mark pushed failed: {str(e)[:70]}")
            items.append({
                "id": t["id"], "title": t.get("title"),
                "n_pass": ev["n_pass"], "n_fail": ev["n_fail"],
                "n_nodata": ev["n_nodata"],
                "scenario_now": (rv or {}).get("scenario_now"),
                "llm": bool(rv), "pushed": pushed,
            })
        return {"ok": True, "date": d, "items": items}
    except Exception as e:
        print(f"[thesis] run_daily failed: {str(e)[:100]}")
        return {"ok": False, "error": str(e)[:120]}


def daily_rows(tid: str, limit: int = 30) -> List[Dict]:
    """某份推演的逐日对照历史（前端时间线用）。"""
    ensure_tables()
    try:
        rows = db.fetch(f"SELECT * FROM {_T_DAILY} WHERE thesis_id=%s "
                        f"ORDER BY date DESC LIMIT %s", (tid, max(1, min(limit, 200)))) or []
    except Exception as e:
        print(f"[thesis] daily_rows failed: {str(e)[:80]}")
        return []
    out = []
    for r in rows:
        out.append({
            "date": r.get("date"),
            "checked": _loads(r.get("checked_json")),
            "scenario_now": r.get("scenario_now"),
            "llm": json.loads(r["llm_json"]) if r.get("llm_json") else None,
            "pushed": bool(r.get("pushed")),
        })
    return out
