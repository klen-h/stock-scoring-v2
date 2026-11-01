# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】金十「每日汇总」抓取（tidy-news）—— 补国内消息面缺口
================================================================================
背景（2026-10-30，用户："现在缺国内的消息"）：
  系统里已有金十**快讯**（`flash/source.py`，单条实时），但缺"每天一次的结构化汇总"。
  这类汇总恰好能回答快讯回答不了的问题：**为什么涨/跌**（资金面、业绩、政策三线）。

★ 预登记声明（红线）：**纯素材采集，不产生买卖信号**。
  不改分数、不进闸门、不碰 regime/模拟盘；删掉本模块系统其余部分照常运行。

--------------------------------------------------------------------------------
一、接口（实测 2026-10-30 / **修正 2026-11-01**）
--------------------------------------------------------------------------------
    GET https://mp-api.jin10.com/api/tidy-news/details?category_id={cat}&date={YYYY-MM-DD}
      x-app-id : fiXF2nOnDycGutVA   （与 flash/margin_sentiment.py 同一个）
      x-version: 1.0
      x-token  : <登录后拿到的 UUID>  ← **必须放 header**，放 cookie 也行但 header 更干净
    Bearer 方式无效（实测 401）。

★★ 2026-11-01 重要修正（代价：10-30 起漏抓了整批数据）
  原方案是"按 id 递增扫描"——当时实测 `/tidy-news/list|articles|items` 全 502，
  于是断定"没有列表 API"。**但真正的取数键是 `category_id + date`，不是 `id`**：
    · `/details?id=60070`                          → `status:200` 但 `data:null`
    · `/details?category_id=50029&date=2026-10-30` → 正常返回 27 条
  ⇒ 旧扫描法对新日期一律返回 null，被误判成"今天还没发布"，游标一路往前推，
    **越过之后永久补不回来**（实测 last_id=60169，而 10-30 四个栏目一条都没入库）。
  ⇒ 现在：`compute(days=3)` 对最近 3 天 × 4 栏目发 **12 个请求**（旧法最多 120 个），
    且天然支持补历史（`day=` 指定任意日期）。
    实测补 10-29 + 10-30 两天：**8 个请求 → 121 条**。

--------------------------------------------------------------------------------
二、⚠️ x-token 会过期（本方案唯一的长期运维点）
--------------------------------------------------------------------------------
  token 是登录态，过期后所有请求返回 `{"status":401,"message":"请登录"}`。
  **没有账号密码，无法自动刷新** ⇒ 过期后需用户重新登录金十换取。
  ⇒ 抓到 401 必须**显式告警**（写 meta.last_error + 日志），
    否则会静默变成"每天都没数据"，用户几天后才发现。

--------------------------------------------------------------------------------
三、为什么**不逐条**喂 LLM（2026-10-30 定）
--------------------------------------------------------------------------------
  这些汇总是**被人提炼过的结论**（标题本身就是结果），逐条让 LLM 再总结一遍
  属于**信息损耗**：`四部门：提高自贸港便利化` 会被改写成"利好海南板块"，
  丢掉"两类库容/LNG 期货交割"这些真正可用的细节。
  而且 18 栏目 × 10 条/天 ≈ 9 万 token，免费额度撑不住（已被 429 过）。
  ⇒ **入库/展示一律用原文**；LLM 只在 `summarize()` 里**整批归纳一次**
    （只送标题，约 1 千 token），输出 3~5 条跨栏目主线。
================================================================================
"""
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from app.database import db

_TABLE = "jin10_tidy_news"
_META = "jin10_tidy_meta"
_READY = False

_API = "https://mp-api.jin10.com/api/tidy-news/details"
_APP_ID = os.environ.get("JIN10_MP_APP_ID") or "fiXF2nOnDycGutVA"

# 目标栏目（先 4 个，跑顺了再扩）
#   50029 A股盘前 / 50030 A股回顾 / 50033 存储 / 50034 热门ETF
#   其余候选：50007 AI、50009 电动车、50010 房地产、50011 投行、50012 债券、
#            50014 隔日股市、50015 外汇、50016 商品、50017 港股、50020 期货、
#            50022 昨日今晨、50023 欧盘美盘、50024 美股
_TARGETS: Dict[int, str] = {
    50029: "A股盘前",
    50030: "A股回顾",
    50033: "存储",
    50034: "热门ETF",
}

# 正文只存前 N 字：数字与结论几乎都在前两句，再往后多为"公司表示/符合预期"这类客套
_CONTENT_MAX = 120
# 单次扫描上限：每天约 18~24 个 id，120 足够覆盖一周的空窗
_MAX_SCAN = int(os.environ.get("JIN10_TIDY_MAX_SCAN") or 120)
# 连续这么多个空 id 就认为"今天还没发布"，提前结束（省请求）
_EMPTY_BREAK = 20

_last_error = ""


def ensure_tables() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT, category_id INTEGER, group_name TEXT,
            title TEXT, content TEXT, tags TEXT, seq INTEGER,
            created_at TEXT,
            PRIMARY KEY (date, category_id, seq)
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_META} (
            key TEXT PRIMARY KEY, value TEXT
        )
    """)
    _READY = True


def _meta_get(key: str, default: str = "") -> str:
    try:
        r = db.fetch_one(f"SELECT value FROM {_META} WHERE key=%s", (key,))
        return str((r or {}).get("value") or default)
    except Exception:
        return default


def _meta_set(key: str, value: str) -> None:
    try:
        db.execute(
            f"INSERT INTO {_META} (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, str(value)),
        )
    except Exception as e:
        print(f"[jin10_tidy] meta 写入失败 {key}: {str(e)[:80]}")


def _headers() -> Dict[str, str]:
    token = (os.environ.get("JIN10_X_TOKEN") or "").strip()
    h = {
        "x-app-id": _APP_ID,
        "x-version": "1.0",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
        "Referer": "https://www.jin10.com/",
        "Origin": "https://www.jin10.com",
        "accept": "application/json, text/plain, */*",
    }
    if token:
        h["x-token"] = token          # ★ 放 header；放 cookie 也行，但 header 更干净
    return h


def _fetch_one(news_id: int) -> Optional[Dict]:
    """抓一期详情。

    ★ 返回 None 的三种情况必须区分开（否则会把"没发布"误判成"故障"）：
      ① 未使用的 id：`status==200` 但 `data.category_id` 为 None  ⇒ 正常，没内容
      ② 未登录：`status==401`                                     ⇒ **token 过期，必须告警**
      ③ 网络/其它：抛异常                                          ⇒ 记 error，不中断整批
    """
    global _last_error
    # 先把请求头提到变量：若直接在调用参数里写 `_headers()` 调用，
    # 嵌套括号会让卫生门 req_no_timeout 的纯正则匹配不到 timeout（误报"缺 timeout"）。
    # ⚠️ 这段注释里也**不能**写出完整的 requests 调用示例，否则同样被当作真违规。
    hdrs = _headers()
    try:
        r = requests.get(_API, params={"id": news_id}, headers=hdrs, timeout=15)
        j = r.json()
    except Exception as e:
        _last_error = f"网络: {str(e)[:80]}"
        return None
    status = j.get("status")
    if status == 401:
        # ★ token 过期：这是唯一需要用户动手的故障，必须留下明确痕迹
        _last_error = "x-token 失效（请重新登录金十更新 JIN10_X_TOKEN）"
        print(f"[jin10_tidy] ⚠️ {_last_error}")
        return None
    if status != 200:
        _last_error = f"status={status} {str(j.get('message'))[:60]}"
        return None
    d = j.get("data")
    if not isinstance(d, dict) or not d.get("category_id"):
        # ① 未使用的 id —— **不是故障**，必须把 `_last_error` 清空，
        #   否则调用方会拿上一次的旧错误去判断（从而误以为这次也失败、不推进游标）
        _last_error = ""
        return None
    _last_error = ""
    return d


# 板块标签（规则，不用 LLM）：命中即打，可多标签
_TAGS: Dict[str, List[str]] = {
    "光模块": ["光模块", "光芯片", "EML", "1.6T"],
    # ★ 2026-10-30：**不能只写"存储"** —— 实测「海南自贸港…保税仓库**存储**汽油」
    #   被误判成半导体存储。必须用带限定词的组合。
    "存储": ["DRAM", "NAND", "闪存", "HBM", "存储芯片", "存储价格", "存储市场",
             "存储原厂"],
    "算力": ["算力", "AI服务器", "PCB", "HDI", "数据中心"],
    "汽车": ["汽车", "乘用车", "新能源车", "自动驾驶"],
    "地产": ["房地产", "楼市", "宅地", "房贷", "土储"],
    "资金面": ["主力资金", "净流入", "净流出", "ETF", "募集", "建仓"],
    # ★ 2026-10-30：补"政策性金融工具/投放"——实测 50029 的
    #   「8000亿元新型政策性金融工具投放完成」原来一个标签都没打上
    "政策": ["四部门", "国务院", "发改委", "商务部", "央行", "财政部", "证监会",
             "政策性金融工具", "专项债", "以旧换新"],
    "半导体": ["半导体", "晶圆", "设备", "刻蚀", "光刻"],
    "机器人": ["机器人", "具身智能", "人形"],
    "业绩": ["净利润", "营收", "同比增长", "同比下降", "业绩"],
    "订单": ["订单", "排单", "中标", "在手订单"],
}


def _classify(title: str, content: str) -> str:
    text = f"{title} {content}"
    hits = [tag for tag, kws in _TAGS.items() if any(k in text for k in kws)]
    return ",".join(hits)


# ================================================================
#  送 LLM 前的「结构性重复」折叠（★ 2026-11-01）
# ================================================================
# 典型：`热门ETF` 栏目 17 条里 12 条是「XX溢价约Y%，开市起停牌至10:30」——
#   同一现象的不同标的。逐条送进 LLM 既费 token，又会诱导它把"ETF 溢价"
#   当成一条独立主线（它其实只是一个资金面现象，不是产业逻辑）。
#
# ⚠️ 只折叠**明确枚举**的模式，**不做通用文本相似度**：
#   业绩类条目结构也很像（"XX：三季度净利润Y亿，同比+Z%"），但那是不同公司
#   的不同事实，折叠会丢信息。ETF 溢价则是"同一件事的 N 个标的"，性质不同。
_FOLD_PATTERNS = [
    # ⚠️ 窗口要放宽：实测「溢价约36.41%，10月30日开市起至10:30停牌」中间有 21 个字符，
    #   `{0,16}` 会漏掉大部分条目（首版就是栽在这里，17 条只折叠了 1 条）。
    (re.compile(r"溢价[^。]{0,40}(停牌|风险)"), 2),   # ETF/LOF 溢价 → 停牌/风险提示
]
_FOLD_MIN = 3        # 少于 3 条不折叠（只有 2 条重复时没必要汇总）
_FOLD_KEEP = 2       # 保留几条样例给 LLM 看具体幅度


def _fold_for_llm(items: List[Dict]) -> List[Dict]:
    """把同栏目内**结构化重复**的标题折叠为「样例外 2 条 + 一条汇总」。

    仅用于 `summarize()` 的 payload —— **不影响入库与前端展示**。
    """
    consumed = set()
    out: List[Dict] = []
    for i, it in enumerate(items):
        if i in consumed:
            continue
        hit = False
        title = it.get("title") or ""
        for pat, keep in _FOLD_PATTERNS:
            if not pat.search(title):
                continue
            group = [j for j, x in enumerate(items)
                     if j not in consumed and pat.search(x.get("title") or "")]
            if len(group) < _FOLD_MIN:
                continue
            for j in group[:keep]:
                out.append(items[j])
                consumed.add(j)
            rest = len(group) - keep
            if rest > 0:
                out.append({"title": f"（同类另有 {rest} 条，属同一现象的重复披露）"})
            consumed.update(group[keep:])
            hit = True
            break
        if not hit:
            out.append(it)
            consumed.add(i)
    return out


def retag(day: str = None) -> int:
    """按当前 `_TAGS` 重算标签，**不重新抓取**。

    ★ 为什么需要：标签是在**入库那一刻**算好存下来的，改了 `_TAGS` 只影响新数据。
      而"补关键词"这类调整很常见（实测 `8000亿政策性金融工具` 一开始没打上标签）。
      重抓要几十个请求，而重算只读库，几百毫秒。
    """
    ensure_tables()
    if day:
        rows = db.fetch(f"SELECT date, category_id, seq, title, content "
                        f"FROM {_TABLE} WHERE date=%s", (str(day)[:10],))
    else:
        rows = db.fetch(f"SELECT date, category_id, seq, title, content FROM {_TABLE}")
    n = 0
    for r in rows or []:
        t = _classify(r.get("title") or "", r.get("content") or "")
        db.execute(f"UPDATE {_TABLE} SET tags=%s "
                   f"WHERE date=%s AND category_id=%s AND seq=%s",
                   (t, r["date"], r["category_id"], r["seq"]))
        n += 1
    print(f"[jin10_tidy] retag {n} 条")
    return n


def _split_contents(contents: List[Dict]) -> List[Dict]:
    """把 contents 拆成条目，并给每条挂上所属分组名。

    结构：`{"group":"0","title":"重要新闻","content":""}` 是**分组标记**（group 非空）；
          其余是条目（`{"title":"1. xxx","content":"正文"}`）。
    ⚠️ 部分条目 `content` 为空（如"主力资金流向数据显示…"）⇒ 这里原样保留，不丢。
    """
    out: List[Dict] = []
    cur_group = ""
    seq = 0
    for c in contents or []:
        if c.get("group"):
            cur_group = str(c.get("title") or "").strip()
            continue
        title = str(c.get("title") or "").strip()
        if not title:
            continue
        body = re.sub(r"\s+", " ", str(c.get("content") or "")).strip()
        seq += 1
        out.append({
            "group_name": cur_group,
            "title": title,
            "content": body[:_CONTENT_MAX],
            "seq": seq,
        })
    return out


def _fetch_by_date(category_id: int, day: str) -> Optional[Dict]:
    """按「栏目 + 日期」取一天的汇总（★ 2026-11-01 新增，取代 id 扫描）。

    `GET /api/tidy-news/details?category_id=50029&date=2026-10-30`
    → `{id, title, category_id, date, contents, publish_time, ...}`

    返回 None 表示"该日该栏目没有内容"或调用失败（失败原因见 `_last_error`）。
    """
    global _last_error
    try:
        r = requests.get(_API,
                         params={"category_id": int(category_id), "date": str(day)[:10]},
                         headers=_headers(), timeout=15)
        j = r.json()
    except Exception as e:
        _last_error = f"网络: {str(e)[:80]}"
        return None
    status = j.get("status")
    if status == 401:
        _last_error = "x-token 失效（请重新登录金十更新 JIN10_X_TOKEN）"
        return None
    if status != 200:
        _last_error = f"status={status} {str(j.get('message'))[:60]}"
        return None
    _last_error = ""
    d = j.get("data")
    return d if isinstance(d, dict) else None


def compute(days: int = 3, day: str = None) -> Dict:
    """按「栏目 × 日期」抓取目标栏目并入库。

    ★★ 2026-11-01 重构（推翻原来的 id 递增扫描）：
      原实现假定"该接口没有列表 API，只能按 id 递增扫描"。**这个假定是错的**：
        · `/details?id=xxx`   对新日期返回 `status 200 + data=null`（看着像"没发布"）
        · `/details?category_id=50029&date=2026-10-30` **正常返回 27 条**
      ⇒ `id` 不是取数的正确键。扫描法从 2026-10-30 起一直在"扫到空就推进游标"，
        游标越过那段后**永远补不回来**（实测 last_id 60169，而 10-30 数据完全没入库）。

      现在的做法：对最近 `days` 天 × 4 个目标栏目各发 1 个请求
      （默认 3 天 = **12 个请求**，比原来最多 120 个少 90%），
      且天然支持补历史（`day=` 指定任意一天即可回补）。

    参数：
      days: 往回抓几天（含 `day` 当天），默认 3 —— 覆盖"当天还没发布、昨天已发布"的情况
      day : 结束日期 YYYY-MM-DD，默认今天（北京时间）

    返回 `{ok, scanned, saved, dates, per_day, error}`。
    """
    ensure_tables()
    end = (str(day)[:10] if day else
           datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"))
    try:
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "scanned": 0, "saved": 0, "dates": [], "per_day": {},
                "error": f"日期格式错误: {end}"}
    date_list = [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d")
                 for i in range(max(1, int(days or 1)))]

    scanned = saved = 0
    errors: List[str] = []
    per_day: Dict[str, int] = {}

    for dstr in date_list:
        for cat in _TARGETS:
            d = _fetch_by_date(cat, dstr)
            scanned += 1
            if d is None:
                # token 失效是唯一需要人介入的故障 ⇒ 立即中止，不空转剩余请求
                if _last_error.startswith("x-token"):
                    _meta_set("last_error", _last_error)
                    return {"ok": False, "scanned": scanned, "saved": saved,
                            "dates": date_list, "per_day": per_day, "error": _last_error}
                continue
            items = _split_contents(d.get("contents") or [])
            if not items:
                continue
            try:
                for it in items:
                    db.execute(
                        f"INSERT INTO {_TABLE} (date, category_id, group_name, title, "
                        f"content, tags, seq, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        f"ON CONFLICT (date, category_id, seq) DO UPDATE SET "
                        f"group_name=EXCLUDED.group_name, title=EXCLUDED.title, "
                        f"content=EXCLUDED.content, tags=EXCLUDED.tags",
                        (dstr, int(cat), it["group_name"], it["title"], it["content"],
                         _classify(it["title"], it["content"]), it["seq"],
                         # ★ 显式带时区：卫生门 naive_now 会拦"无时区的 now 调用"
                         #   （生产容器是 UTC，naive 会与本地差 8 小时）。
                         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
                    )
                saved += len(items)
                per_day[dstr] = per_day.get(dstr, 0) + len(items)
                print(f"[jin10_tidy] {dstr} {_TARGETS[cat]} {len(items)} 条")
            except Exception as e:
                errors.append(f"{dstr}/{cat}: {str(e)[:60]}")
                print(f"[jin10_tidy] 入库失败 {dstr}/{cat}: {str(e)[:80]}")

    if _last_error:
        _meta_set("last_error", _last_error)
    elif saved:
        _meta_set("last_error", "")

    return {"ok": not errors and bool(saved), "scanned": scanned, "saved": saved,
            "dates": date_list, "per_day": per_day,
            "error": _last_error or ";".join(errors[:3])}


def load(day: str = None) -> Dict:
    """读取某日的汇总（按栏目分组）。

    与 `crowding_watch.load` 同一套回退语义：指定日无数据时回退到
    `date <= 请求日` 的最近一天，并标注 `fallback`（避免"回到未来"）。
    """
    ensure_tables()
    requested = str(day)[:10] if day else ""
    fallback = False
    d = requested
    rows: List[Dict] = []
    try:
        if d:
            rows = db.fetch(f"SELECT * FROM {_TABLE} WHERE date=%s "
                            f"ORDER BY category_id, seq", (d,))
            if not rows:
                r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE} WHERE date <= %s",
                                 (d,))
                latest = str((r or {}).get("d") or "")[:10]
                if latest and latest != d:
                    rows = db.fetch(f"SELECT * FROM {_TABLE} WHERE date=%s "
                                    f"ORDER BY category_id, seq", (latest,))
                    if rows:
                        fallback, d = True, latest
        else:
            r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE}")
            d = str((r or {}).get("d") or "")[:10]
            if d:
                rows = db.fetch(f"SELECT * FROM {_TABLE} WHERE date=%s "
                                f"ORDER BY category_id, seq", (d,))
    except Exception as e:
        return {"date": d, "sections": [], "note": f"读取失败: {str(e)[:80]}"}

    sections: Dict[int, Dict] = {}
    for x in rows:
        cat = int(x.get("category_id") or 0)
        s = sections.setdefault(cat, {"category_id": cat, "name": _TARGETS.get(cat, str(cat)),
                                      "items": []})
        s["items"].append({
            "group": x.get("group_name") or "",
            "title": x.get("title") or "",
            "content": x.get("content") or "",
            "tags": [t for t in str(x.get("tags") or "").split(",") if t],
        })
    note = "金十每日汇总（原文，未经 LLM 改写）；仅作消息面素材，不构成交易建议。"
    if fallback:
        note = f"该日（{requested}）无数据，此处显示 {d} 的结果。" + note
    return {"date": d, "requested_date": requested, "fallback": fallback,
            "sections": list(sections.values()), "note": note,
            # ★ 2026-11-01：已生成的 AI 主线归纳（按日缓存；没有就是空串）。
            #   前端据此把"原文未解读"换成实际内容，不用额外发请求。
            "summary": _meta_get(f"summary:{d}") if d else ""}


def summarize(day: str = None, force: bool = False) -> Dict:
    """把当日**全部标题**拼成一次 LLM 调用，输出 3~5 条跨栏目主线。

    ★ 只送标题、不送正文：18 条标题约 600 字 ≈ 1 千 token，
      而逐条送正文要 9 万 token（免费额度已被 429 过，见 memory 2026-10-30）。
    ★ 这是 LLM 唯一该介入的环节 —— 它的强项是**跨条目归纳**，
      而不是逐条复述（原文已是提炼过的结论，复述只会丢细节）。

    ★ 2026-11-01：加**按日缓存**（`jin10_tidy_meta` 的 `summary:<date>`）。
      该函数此前**从未被任何代码调用**（前端一直显示"原文未解读"），
      现在由手动按钮触发；缓存保证同一天重复点按钮不重复烧 token。
      `force=True` 强制重算。
    """
    data = load(day)
    day_key = str(data.get("date") or "")[:10]
    sections = data.get("sections") or []
    if not sections or not day_key:
        return {"ok": False, "summary": "", "error": "无数据可归纳"}

    cache_key = f"summary:{day_key}"
    if not force:
        cached = _meta_get(cache_key)
        if cached:
            return {"ok": True, "date": day_key, "summary": cached,
                    "cached": True, "sections": len(sections),
                    "titles": sum(len(s["items"]) for s in sections)}

    lines: List[str] = []
    for s in sections:
        lines.append(f"[{s['name']}]")
        # ★ 2026-11-01：先折叠结构性重复（如 12 条 ETF 溢价停牌），再截前 12 条
        for it in _fold_for_llm(s["items"])[:12]:
            lines.append(f"- {it['title']}")
    payload = "\n".join(lines)

    system = (
        "你是 A 股消息面分析助手。只做跨条目归纳，不复述单条新闻。"
        "要求：3~5 条主线，每条一句话，必须点明'对哪个板块/资金面意味着什么'；"
        "没有明确指向的条目直接忽略；不要预测点位，不给买卖建议。"
    )
    user = f"以下是 {data.get('date')} 的国内消息汇总标题，请归纳今日主线：\n\n{payload}"

    try:
        from app.flash import llm
        text = llm.call_llm(system, user, temperature=0.2, tier="slow")
    except Exception as e:
        return {"ok": False, "summary": "", "error": f"LLM 调用失败: {str(e)[:80]}"}
    if not text:
        return {"ok": False, "summary": "", "error": "LLM 返回空（可能限流）"}
    out = text.strip()
    try:
        _meta_set(cache_key, out)
    except Exception as e:
        print(f"[jin10_tidy] 归纳缓存写入失败（不影响本次返回）: {e}")
    return {"ok": True, "date": day_key, "summary": out, "cached": False,
            "sections": len(sections), "titles": sum(len(s["items"]) for s in sections)}
