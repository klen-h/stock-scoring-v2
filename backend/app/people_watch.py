# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】人物立场时间线（People Watch）—— 同一人物跨天讲话的原文序列
================================================================================
背景（2026-11-05，用户给了真实接口）：
  ```
  GET https://mp-api.jin10.com/api/free-flash/list
      ?people_org_id=83&limit=20&page=1&date=2026-08-05&date_end=2026-11-05
      x-app-id / x-version 必带；**x-token 必带**（不带返回 {"status":401,"message":"请登录"}）
  ```
  用户同时指出：**这个接口不是实时的**，实时只能在快讯里过滤。

★ 为什么值得做（不在"再加一个新闻源"）：
  单个快讯看不出"同一人在短时间里说了相反的话"。实测样例（Trump）：
    11-03 19:35  "我们昨晚对伊朗设施进行了非常出色的打击"
    11-04 11:39  "如果伊朗表现良好，我们可以签署一些协议"
    11-05 01:29  "选举失利在很大程度上是伊朗战争的结果，基本属实"
  48 小时 = 打击 → 签协议 → 承认是战争害了选票。
  `flash_brief` 每次只看"当下这一条"，**结构性看不到这个序列**。

★★ 核心发现：**金十自己把关键句标红了** ——
  正文里 `<span style="color: rgb(235, 69, 57);">...</span>` 是编辑认为最重要的句子，
  另有 `<b>` 粗体强调。⇒ **不需要 LLM 判断哪句重要，直接取编辑的高亮**。
  这既省钱也避免"模型总结损耗原文"（与 `jin10_tidy` 的"原文直出"同一立场）。

--------------------------------------------------------------------------------
★ 实时性：两个数据源，必须分工（用户 2026-11-05 实测指出）
--------------------------------------------------------------------------------
  · 本接口 = 编辑**整理后**发布，有延迟（实测：11-05 22:00 时最新一条是 11-05 01:29）。
  · `flash_news` = 原始快讯流，实时，但只覆盖高频人物
    （实测近 19 天：特朗普 62 条直接引语，而苏姿丰 0 条、鲍威尔 0 条）。
  ⇒ 本模块**只负责历史（T-1 及更早）的结构化时间线**；
    当日实时看快讯（`flash_brief` 已经在做），**不在这里重复造一个实时流**。
    这是刻意的：为一个必然延迟的数据源做"伪实时"，只会制造两个口径。

★ 红线：只呈现原文与高亮句，**不做立场判断、不做多空解读**。
  "他改口了"必须由人自己看出来 —— 任何自动"立场反转检测"都需要语义模型，
  而模型的判断无法证伪（同 `thesis_tracker` 的顾虑）。
================================================================================
"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from app.database import db

_API = "https://mp-api.jin10.com/api/free-flash/list"
_TABLE = "people_flash"
_READY = False
_BJ = timezone(timedelta(hours=8))

# 关注名单（people_org_id 与快讯 `type_id` 是**同一个 ID 空间**，已实测 83=特朗普）
# 只放"说话会影响大类资产"的人：央行行长 + 财政 + 白宫 + 两个产业旗手。
# ⚠️ 加人前先确认 id：金十 `people_org` 里的 id 就是接口的 people_org_id。
PEOPLE: List[Dict] = [
    {"id": 83,  "name": "特朗普",   "title": "美国总统"},
    {"id": 153, "name": "沃什",     "title": "美联储主席"},
    {"id": 2,   "name": "拉加德",   "title": "欧洲央行行长"},
    {"id": 3,   "name": "贝利",     "title": "英国央行行长"},
    {"id": 96,  "name": "麦克勒姆", "title": "加拿大央行行长"},
    {"id": 95,  "name": "布洛克",   "title": "澳洲联储主席"},
    {"id": 134, "name": "贝森特",   "title": "美国财长"},
    {"id": 527, "name": "马斯克",   "title": "特斯拉/SpaceX"},
]

# 金十的高亮色（编辑标红 = 关键句）。写死颜色值而不是"任意 span"，
# 因为正文里还有黑色/灰色 span 用于排版，全取会退化成"整段照抄"。
_KEY_COLOR = re.compile(r'<span[^>]*rgb\(\s*235\s*,\s*69\s*,\s*57\s*\)[^>]*>(.*?)</span>', re.S)
_BOLD = re.compile(r'<b[^>]*>(.*?)</b>', re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

_PAGE_SIZE = 20
_MAX_PAGES = int(8)          # 单次最多翻 8 页（160 条）；再多的历史靠下次增量
_KEY_MAX = 12                # 单条讲话最多留 12 句高亮，避免刷屏
_KEY_MIN_LEN = 6             # 少于 6 字的"关键句"多半是"是的。""不。"这类，没信息量


def _clean(html: str) -> str:
    t = _TAG.sub(" ", html or "")
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return _WS.sub(" ", t).strip()


def _keys(html: str) -> List[str]:
    """提取关键句：金十标红的 + `<b>` 粗体的，按**原文位置**排序后去重。

    ⚠️ 按位置排序是必要的：先红后 b 会把句中、句首的顺序打乱，
       而"顺序"恰恰是判断立场演进的关键信息。
    """
    hits = []
    for rx in (_KEY_COLOR, _BOLD):
        for m in rx.finditer(html or ""):
            hits.append((m.start(), _clean(m.group(1))))
    hits.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _pos, t in hits:
        t = t.strip(" ：:，,。;；")
        if len(t) < _KEY_MIN_LEN or t in seen:
            continue
        # 红 span 里嵌 <b> 时，_clean 结果会包含同一句 ⇒ 子串级去重
        if any(t in s for s in seen):
            continue
        seen.add(t)
        out.append(t)
    return out[:_KEY_MAX]


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            item_id INTEGER PRIMARY KEY,
            people_id INTEGER,
            name TEXT,
            people_title TEXT,
            title TEXT,
            publish_time TEXT,
            publish_date TEXT,
            text TEXT,
            keys_json TEXT,
            fetched_at TEXT
        )
    """)
    _READY = True


def _headers() -> Dict[str, str]:
    """复用 `jin10_tidy` 的 header（含 x-token 读取逻辑），避免两套 token 口径。

    ⚠️ 实测（2026-11-05）：
       裸 header            → HTTP 502（空响应）
       +x-app-id/x-version  → HTTP 200 但 status=401 请登录
       +x-token             → 正常
    """
    from app.jin10_tidy import _headers as _h
    return _h()


def _bj_date(iso: str) -> str:
    """UTC ISO → 北京日期（YYYY-MM-DD）。

    ⚠️ 必须转时区：`2026-11-04T17:29:27Z` 在北京是 **11-05 01:29**，
       直接截前 10 位会把它算到 11-04，整条时间线错位一天。
    """
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone(_BJ).strftime("%Y-%m-%d")
    except Exception:
        return str(iso or "")[:10]


def _bj_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone(_BJ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(iso or "")


_last_error = ""       # ⚠️ 必须在 `_get` 之前定义：`global` 声明只影响赋值，
                       #    但 sync_one 会在失败分支里直接读它，放到函数下面容易漏。


def _get(pid: int, d0: str, d1: str, page: int) -> Optional[List[Dict]]:
    """取一页。返回 None 表示失败（原因写 `_last_error`）。"""
    global _last_error
    try:
        r = requests.get(_API, params={
            "people_org_id": int(pid), "limit": _PAGE_SIZE,
            "page": int(page), "date": d0, "date_end": d1,
        }, headers=_headers(), timeout=20)
        j = r.json()
    except Exception as e:
        _last_error = f"网络: {str(e)[:80]}"
        return None
    st = j.get("status")
    if st == 401:
        _last_error = "x-token 失效（请更新服务器 .env 的 JIN10_X_TOKEN）"
        return None
    if st != 200:
        _last_error = f"status={st} {str(j.get('message'))[:60]}"
        return None
    _last_error = ""
    data = j.get("data")
    return data if isinstance(data, list) else []


def sync_one(pid: int, days: int = 30, max_pages: int = None) -> Dict:
    """拉取并落库单个人物的讲话。幂等（item_id 为主键）。

    只增不改：`DO NOTHING` 而非 `DO UPDATE` —— 金十可能修订旧稿，
    但"事后改写历史讲话"正是本模块最该防的事，保留首次落库版本更可信。
    """
    ensure_table()
    name, title = "", ""
    for p in PEOPLE:
        if int(p["id"]) == int(pid):
            name, title = p["name"], p["title"]
            break
    if not name:
        return {"ok": False, "people_id": pid, "error": "不在关注名单（见 PEOPLE）"}

    now = datetime.now(_BJ)
    d1 = now.strftime("%Y-%m-%d")
    d0 = (now - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    pages = int(max_pages or _MAX_PAGES)
    saved, scanned = 0, 0
    for page in range(1, pages + 1):
        rows = _get(pid, d0, d1, page)
        if rows is None:
            return {"ok": False, "people_id": pid, "name": name,
                    "error": _last_error, "scanned": scanned, "saved": saved}
        if not rows:
            break
        scanned += len(rows)
        # ★ 先查一遍本页哪些 id 已在库：`DO NOTHING` 时 `execute` 不告诉我们是插入还是跳过，
        #   不查就会把"每天重复拉到的 20 条"报成"新增 20 条"，日志彻底失去意义。
        existing = set()
        _ids = [int(x["id"]) for x in rows if x.get("id")]
        if _ids:
            _ph = ",".join(["%s"] * len(_ids))
            try:
                for _r in db.fetch(
                        f"SELECT item_id FROM {_TABLE} WHERE item_id IN ({_ph})",
                        tuple(_ids)) or []:
                    existing.add(int(_r["item_id"]))
            except Exception:
                pass
        for x in rows:
            iid = x.get("id")
            if not iid:
                continue
            html = x.get("content") or ""
            keys = _keys(html)
            try:
                db.execute(
                    f"INSERT INTO {_TABLE} (item_id, people_id, name, people_title, title, "
                    f"publish_time, publish_date, text, keys_json, fetched_at) "
                    f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (item_id) DO NOTHING",
                    (int(iid), int(pid), name, title,
                     (x.get("title") or "")[:120],
                     str(x.get("publish_time") or ""),
                     _bj_date(x.get("publish_time")),
                     _clean(html)[:2000],
                     json.dumps(keys, ensure_ascii=False),
                     now.strftime("%Y-%m-%d %H:%M:%S")))
                if int(iid) not in existing:
                    saved += 1
            except Exception as e:
                print(f"[people] insert failed id={iid}: {str(e)[:80]}")
        if len(rows) < _PAGE_SIZE:
            break
    return {"ok": True, "people_id": pid, "name": name,
            "range": [d0, d1], "scanned": scanned, "saved": saved}


def sync_all(days: int = 30) -> Dict:
    """日批调用：逐个拉取关注名单。任何一个人失败都不影响其他人。"""
    out = []
    for p in PEOPLE:
        r = sync_one(p["id"], days=days)
        out.append(r)
        if not r.get("ok"):
            print(f"[people] {p['name']} 失败: {str(r.get('error'))[:80]}")
    return {"ok": True, "items": out,
            "saved": sum(x.get("saved") or 0 for x in out)}


def timeline(pid: int, days: int = 30, limit: int = 20) -> Dict:
    """读库：某人近 N 天的讲话（按时间倒序）。**不出网、零外部请求**。"""
    ensure_table()
    since = (datetime.now(_BJ) - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    name, title = "", ""
    for p in PEOPLE:
        if int(p["id"]) == int(pid):
            name, title = p["name"], p["title"]
            break
    try:
        rows = db.fetch(
            f"SELECT item_id, title, publish_time, publish_date, text, keys_json "
            f"FROM {_TABLE} WHERE people_id=%s AND publish_date >= %s "
            f"ORDER BY publish_time DESC LIMIT %s", (int(pid), since, int(limit)))
    except Exception as e:
        return {"ok": False, "people_id": pid, "name": name,
                "error": f"读取失败: {str(e)[:80]}", "items": []}

    items = []
    for r in rows or []:
        try:
            keys = json.loads(r.get("keys_json") or "[]")
        except Exception:
            keys = []
        items.append({
            "id": r.get("item_id"),
            "date": str(r.get("publish_date") or ""),
            "time": _bj_time(r.get("publish_time")),
            "title": r.get("title") or "",
            # ★ 有高亮句就只用高亮句：金十编辑已经筛过，比"全文前 100 字"准得多
            "keys": keys,
            "excerpt": "" if keys else (r.get("text") or "")[:160],
        })
    return {"ok": True, "people_id": pid, "name": name, "people_title": title,
            "days": days, "n": len(items), "items": items}


def list_people() -> List[Dict]:
    """关注名单 + 库里条数/最新日期（前端选择器用）。"""
    ensure_table()
    out = []
    for p in PEOPLE:
        n, latest = 0, ""
        try:
            r = db.fetch_one(
                f"SELECT COUNT(*) AS c, MAX(publish_date) AS d FROM {_TABLE} "
                f"WHERE people_id=%s", (int(p["id"]),))
            n = int((r or {}).get("c") or 0)
            latest = str((r or {}).get("d") or "")
        except Exception:
            pass
        out.append({**p, "n": n, "latest": latest})
    return out
