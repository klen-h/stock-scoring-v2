# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】国家队增持公告 **自动提示器**（只提示，绝不自动打勾）
================================================================================
背景（2026-10-19，用户："把①做成自动提示"）：
  `policy_bottom_watch` 的第①条"汇金/国新等国家队新公告"原本**纯人工**，因为
  "没有结构化数据源"。但巨潮资讯网（法定披露渠道）的公告检索接口是可用的
  ⇒ 可以做到"每天自动扫一遍，**有公告就把链接和标题摆在人眼前**"，
  打勾权仍然在人（这是本条判据的设计，不因自动化而改变）。

★★ 红线（改之前先读）
  1. **只提示，不打勾**：本模块不写 `manual_nation_team`，也不改 `checked`。
     自动打勾会把"有人发过增持公告"混成"清单已确认"，那是两回事。
  2. **不产生任何交易信号**：不改分数、不改排序、不进闸门、不碰模拟盘准入。
     删掉本模块，其余部分照常运行。
  3. **失败必须静默降级**：抓不到就记 `ok=0` + error，让清单显示"自动检查不可用"，
     **绝不能把"抓取失败"当成"没有公告"**（缺失 ≠ 不满足，与 ④ 同一条纪律）。

★★ 实测坑（2026-10-19，务必别踩回去）
  · `searchkey` 是 **分词全文匹配**，不是短语匹配：
      搜"汇金" → 命中「使用银行承兑**汇**票…募集资**金**」
      搜"国新投资" → 命中「中国国际金融…**新**材料…**投资**」
      搜"中国诚通" → 命中「中国海诚」「容诚会计师事务所」
    ⇒ **不能**用 searchkey 的命中数当判据；必须"粗召回 + 本地连续字串过滤"。
    另：`中央汇金` 整词 total=0（近 90 天确无），不要据此认为接口坏了。
  · `column=sse|szse` 对全文检索**无效**（两者返回完全相同），别再按板块分两次拉。
  · `announcementTime` 是毫秒时间戳 ⇒ **必须 +8h** 再取日期。Actions 容器是 UTC，
    直接 `fromtimestamp` 会把 00:0x（北京）的公告算到前一天。

★★ 判据（两条任一命中即提示）
  A. 标题同时含【央企短语】+【动作词】—— 如「中央汇金…增持…」
  B. 标题含【动作词】且代码在**白名单**（四大行/三桶油等）—— 覆盖
     「关于控股股东增持本行股份的公告」这种**标题不写汇金**的写法（真实路径就是它）
  另设**噪声词**兜底（募集资金/承兑汇票/现金管理…），防止分词误命中。
================================================================================
"""

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

CNINFO_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# ── 判据常量（改这里等于改判据，请在文档留痕） ──────────────────────────────
SOVEREIGN_PHRASES = (
    "中央汇金", "汇金公司", "汇金资产", "汇金投资",
    "中国国新", "国新投资", "国新控股",
    "中国诚通", "诚通金控", "诚通资本",
)
ACTION_WORDS = ("增持", "权益变动", "增持计划", "增持股份", "购买")
NOISE_WORDS = ("募集资金", "承兑汇票", "现金管理", "回购注销", "减持")

# 白名单：标题常写"控股股东"而不写"汇金"，但代码是确定的（国家队主战场）
WHITE_CODES = {
    "601398": "工商银行", "601939": "建设银行", "601288": "农业银行",
    "601658": "邮储银行", "601988": "中国银行", "601328": "交通银行",
    "601857": "中国石油", "600028": "中国石化", "601088": "中国神华",
    "600019": "宝钢股份", "601668": "中国建筑", "601390": "中国中铁",
    "601186": "中国铁建", "601766": "中国中车", "600050": "中国联通",
}

# 粗召回关键词（只要"增持/权益变动"两类就够，覆盖标题写法）
RECALL_KEYS = ("增持", "权益变动")
LOOKBACK_DAYS = 3          # 回看窗口（天）——容忍日批失败/补跑，靠去重不会重复提示
MAX_PAGES = 5              # 每个关键词最多翻几页（30 条/页 ⇒ 上限 150 条/关键词）
PAGE_SIZE = 30
TIMEOUT = 20

_TABLE_READY = False


# ────────────────────────────────────────────────────────────────
#  抓取
# ────────────────────────────────────────────────────────────────
def _clean(t) -> str:
    """去掉巨潮返回的高亮标签 `<em>`，否则短语过滤会被标签打断。"""
    return re.sub(r"</?em>", "", str(t or "")).strip()


def _bj_date(ms) -> str:
    """毫秒时间戳 → 北京时间日期（★ 必须 +8h，见模块头）。"""
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc) + timedelta(hours=8)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _query(key: str, start: str, end: str, page: int = 1) -> Dict:
    body = urllib.parse.urlencode({
        "pageNum": page, "pageSize": PAGE_SIZE, "column": "sse", "tabName": "fulltext",
        "plate": "", "stock": "", "searchkey": key, "secid": "",
        "category": "", "trade": "", "seDate": f"{start}~{end}",
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }).encode()
    req = urllib.request.Request(CNINFO_URL, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _classify(code: str, title: str) -> Optional[Dict]:
    """判定一条公告是否命中。返回 {rule, reason} 或 None。

    ★ 只按**标题**判定：正文要下 PDF 才看得到（成本高），而两类真实路径的标题都带
      "增持/权益变动"；正文关键词留作以后增强，不在本轮范围。
    """
    if not title:
        return None
    if any(n in title for n in NOISE_WORDS):
        return None
    phrase = next((p for p in SOVEREIGN_PHRASES if p in title), None)
    action = next((a for a in ACTION_WORDS if a in title), None)
    if not action:
        return None
    if phrase:
        return {"rule": "phrase", "reason": f"标题含「{phrase}」+「{action}」"}
    if code in WHITE_CODES:
        return {"rule": "white_code", "reason": f"白名单个股（{WHITE_CODES[code]}）+「{action}」"}
    return None


def scan(day: str, lookback_days: int = LOOKBACK_DAYS) -> Dict:
    """扫描 [day-lookback, day] 的公告，返回命中清单。

    失败**不外抛**：返回 ok=False + error，由调用方决定怎么展示（缺失 ≠ 不满足）。
    """
    day = str(day or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    try:
        start = (datetime.strptime(day, "%Y-%m-%d")
                 - timedelta(days=int(lookback_days))).strftime("%Y-%m-%d")
    except ValueError:
        start = day

    hits: List[Dict] = []
    seen = set()
    scanned = 0
    truncated = False
    errors: List[str] = []

    for key in RECALL_KEYS:
        for page in range(1, MAX_PAGES + 1):
            try:
                d = _query(key, start, day, page)
            except Exception as e:
                errors.append(f"{key} p{page}: {type(e).__name__}: {str(e)[:60]}")
                break
            items = d.get("announcements") or []
            if not items:
                break
            for a in items:
                scanned += 1
                code = str(a.get("secCode") or "").strip()
                name = _clean(a.get("secName"))
                title = _clean(a.get("announcementTitle"))
                dstr = _bj_date(a.get("announcementTime"))
                ann_id = str(a.get("announcementId") or "")
                uid = ann_id or f"{code}|{dstr}|{title}"
                if uid in seen:
                    continue
                seen.add(uid)
                if dstr and not (start <= dstr <= day):
                    continue                      # seDate 理论上已过滤，双保险
                m = _classify(code, title)
                if not m:
                    continue
                url = a.get("adjunctUrl") or ""
                hits.append({
                    "id": uid, "date": dstr, "code": code, "name": name,
                    "title": title, "rule": m["rule"], "reason": m["reason"],
                    "url": (f"http://static.cninfo.com.cn/{url}" if url else ""),
                })
            total = int(d.get("totalAnnouncement") or 0)
            if page * PAGE_SIZE >= total:
                break
            if page == MAX_PAGES:
                truncated = True

    hits.sort(key=lambda h: (h.get("date") or "", h.get("code") or ""), reverse=True)
    return {
        "ok": not errors, "date": day, "window": [start, day],
        "scanned": scanned, "hits": hits, "hit_count": len(hits),
        "truncated": truncated, "error": ("；".join(errors[:3]) if errors else ""),
    }


# ────────────────────────────────────────────────────────────────
#  落库 / 读取
# ────────────────────────────────────────────────────────────────
def ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS policy_announcement_watch (
            date TEXT PRIMARY KEY,
            hits_json TEXT NOT NULL,
            hit_count INTEGER,
            scanned INTEGER,
            ok INTEGER DEFAULT 1,
            error TEXT,
            created_at TEXT
        )
    """)
    _TABLE_READY = True


def save(data: Dict) -> bool:
    ensure_table()
    day = str(data.get("date") or "")[:10]
    try:
        db.upsert("policy_announcement_watch", {
            "date": day,
            "hits_json": json.dumps(data.get("hits") or [], ensure_ascii=False),
            "hit_count": int(data.get("hit_count") or 0),
            "scanned": int(data.get("scanned") or 0),
            "ok": 1 if data.get("ok") else 0,
            "error": str(data.get("error") or "")[:300],
            "created_at": datetime.now().isoformat(),
        }, conflict_columns=["date"])
        return True
    except Exception as e:
        print(f"[pa] save failed: {str(e)[:100]}")      # ASCII（铁律⑥）
        return False


def load(day: str) -> Optional[Dict]:
    ensure_table()
    day = str(day or "")[:10]
    if not day:
        return None
    try:
        r = db.fetch_one("SELECT * FROM policy_announcement_watch WHERE date = %s", (day,))
    except Exception:
        return None
    if not r:
        return None
    try:
        hits = json.loads(r.get("hits_json") or "[]")
    except (ValueError, TypeError):
        hits = []
    # ★ window 不落库（避免加列迁移），按默认回看天数**确定性还原** —— 保证 summarize 文案
    #   在"日批写入"与"接口读出"两条路径上一致（否则前端会看到"窗口  内"这种空档）。
    try:
        start = (datetime.strptime(day, "%Y-%m-%d")
                 - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    except ValueError:
        start = day
    return {"date": day, "window": [start, day], "hits": hits, "hit_count": len(hits),
            "scanned": r.get("scanned"), "ok": bool(r.get("ok")),
            "error": r.get("error") or "", "created_at": str(r.get("created_at") or "")}


def scan_and_store(day: str, push: bool = True) -> Dict:
    """日批入口：扫 → 落库 → **只把"新增命中"**推企微（同日重跑不重复推）。

    幂等口径：与上一次落库的 `hits_json` 里出现过的 `id` 比较，只推新出现的。
    """
    prev = load(day) or {}
    prev_ids = {h.get("id") for h in (prev.get("hits") or []) if h.get("id")}
    data = scan(day)
    data["new_hits"] = [h for h in data["hits"] if h.get("id") not in prev_ids]
    if not save(data):
        data["stored"] = False
    else:
        data["stored"] = True

    pushed = False
    if push and data.get("new_hits"):
        try:
            from app.flash import wechat
            lines = [f"**国家队增持公告提示（{data['date']}）**",
                     "（**只提示，不自动打勾**；请核对后到工作台清单第①条人工确认）"]
            for h in data["new_hits"][:5]:
                lines.append(f"- {h['date']} {h['code']} {h['name']}：{h['title']}")
                if h.get("url"):
                    lines.append(f"  {h['url']}")
            wechat.push_markdown_batched("国家队增持公告提示", "\n".join(lines),
                                         force=True, category="alert")
            pushed = True
        except Exception as e:
            print(f"[pa] push failed: {str(e)[:100]}")
    data["pushed"] = pushed
    return data


def summarize(data: Dict = None) -> str:
    """一行文案（日报/接口共用，避免两处漂移）。"""
    d = data or {}
    if not d:
        return "自动检查：尚无记录（日批未跑过）"
    if not d.get("ok"):
        return f"自动检查不可用：{d.get('error') or '抓取失败'}（**不等于没有公告**）"
    n = int(d.get("hit_count") or 0)
    win = d.get("window") or []
    span = f"{win[0]}~{win[1]}" if len(win) == 2 else ""
    if n:
        return f"🔔 自动提示：{n} 条增持公告（窗口 {span}，扫描 {d.get('scanned')} 条）"
    return f"自动检查：窗口 {span} 内无汇金/国新/诚通增持公告（扫描 {d.get('scanned')} 条）"
