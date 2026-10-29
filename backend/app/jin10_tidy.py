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
一、接口（实测 2026-10-30）
--------------------------------------------------------------------------------
    GET https://mp-api.jin10.com/api/tidy-news/details?id={id}
      x-app-id : fiXF2nOnDycGutVA   （与 flash/margin_sentiment.py 同一个）
      x-version: 1.0
      x-token  : <登录后拿到的 UUID>  ← **必须放 header**，放 cookie 也行但 header 更干净
    Bearer 方式无效（实测 401）。

⚠️ **没有"文章列表"接口**：`/tidy-news/list|articles|items|news` 全部 502；
   `/tidy-news/categories` 只给栏目元信息（无 data_id）；`/api/home` 只覆盖首页栏目。
⇒ **靠 id 递增扫描**：id 全局连续，每天一批（约 18~24 个），
   未使用的 id 返回 `status:200` 但 `data.category_id` 为 null（**不是 404**）⇒ 判空即可。

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
from datetime import datetime, timezone
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


def compute(max_scan: int = None) -> Dict:
    """从上次的 id 往后扫，把目标栏目入库。

    返回 `{ok, scanned, saved, last_id, error}`。
    """
    ensure_tables()
    limit = int(max_scan or _MAX_SCAN)
    last_id = int(_meta_get("last_id", "60065") or 60065)   # 60065 = 2026-10-29 最后一条
    scanned = saved = 0
    empty_run = 0
    new_last = last_id
    errors: List[str] = []

    for nid in range(last_id + 1, last_id + limit + 1):
        d = _fetch_one(nid)
        scanned += 1
        if d is None:
            # ★ 只有**确认是空 id**（`_fetch_one` 已清空 `_last_error`）才推进游标；
            #   网络错误/401 一律不推进 —— 否则会把"没取到"当成"没有"，把那一段永久跳过。
            if _last_error.startswith("x-token") or _last_error.startswith("网络"):
                break
            empty_run += 1
            new_last = max(new_last, nid)   # 空 id 也推进，避免明天重复扫同一段
            if empty_run >= _EMPTY_BREAK:
                break                       # 连续空 ⇒ 今天这批还没发布，提前收
            continue
        empty_run = 0
        new_last = max(new_last, nid)

        cat = d.get("category_id")
        if cat not in _TARGETS:
            continue
        day = str(d.get("date") or "")[:10]
        if not day:
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
                    (day, int(cat), it["group_name"], it["title"], it["content"],
                     _classify(it["title"], it["content"]), it["seq"],
                     # ★ 显式带时区：卫生门 naive_now 会拦"无时区的 now 调用"
                     #   （生产容器是 UTC，naive 会与本地差 8 小时）。
                     #   ⚠️ 注释里同样不能写出那个无时区写法，否则被正则当成真违规。
                     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
                )
            saved += len(items)
            print(f"[jin10_tidy] {nid} -> {_TARGETS[cat]} {day} {len(items)} 条")
        except Exception as e:
            errors.append(f"{nid}: {str(e)[:60]}")
            print(f"[jin10_tidy] 入库失败 {nid}: {str(e)[:80]}")

    if new_last > last_id:
        _meta_set("last_id", new_last)
    if _last_error:
        _meta_set("last_error", _last_error)
    elif saved:
        _meta_set("last_error", "")

    return {"ok": not errors, "scanned": scanned, "saved": saved,
            "last_id": new_last, "error": _last_error or ";".join(errors[:3])}


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
            "sections": list(sections.values()), "note": note}


def summarize(day: str = None) -> Dict:
    """把当日**全部标题**拼成一次 LLM 调用，输出 3~5 条跨栏目主线。

    ★ 只送标题、不送正文：18 条标题约 600 字 ≈ 1 千 token，
      而逐条送正文要 9 万 token（免费额度已被 429 过，见 memory 2026-10-30）。
    ★ 这是 LLM 唯一该介入的环节 —— 它的强项是**跨条目归纳**，
      而不是逐条复述（原文已是提炼过的结论，复述只会丢细节）。
    """
    data = load(day)
    sections = data.get("sections") or []
    if not sections:
        return {"ok": False, "summary": "", "error": "无数据可归纳"}

    lines: List[str] = []
    for s in sections:
        lines.append(f"[{s['name']}]")
        for it in s["items"][:12]:
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
    return {"ok": True, "date": data.get("date"), "summary": text.strip(),
            "sections": len(sections), "titles": sum(len(s["items"]) for s in sections)}
