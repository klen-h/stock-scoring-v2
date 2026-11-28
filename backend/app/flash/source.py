"""
================================================================================
【文件作用】金十快讯数据源（移植自 flash-monitor/scripts/fetch-flash.js 采集部分）
================================================================================

只负责"取数 + 去重游标"，不做过滤/聚类（那是 rules.py 的事）、不做编排（service.py）。

依赖环境变量：
  FLASH_COOKIE    金十会话 Cookie（必需，从浏览器登录后复制；失效时返回空列表）
  JIN10_FLASH_URL 可选，覆盖默认接口地址（金十会轮换哈希子域名）
================================================================================
"""

import os
import json
import re
import html as html_lib
import requests

from app.flash import store


_TAG_RE = re.compile(r"<br\s*/?>|</p>", re.I)
_HTML_RE = re.compile(r"<[^>]+>")


def _clean_html(text: str) -> str:
    """
    剥离快讯自带的 HTML 标签（<span class="section-news">、<b>、<br/> 等）。
    好处：事件流展示干净、送 LLM 的 prompt 不用为标签白付 token，
    关键词过滤/聚类也不受标签干扰。<br/> 转为换行保留分段语义。
    """
    if not text:
        return text
    text = _TAG_RE.sub("\n", text)            # 块级标签 → 换行
    text = _HTML_RE.sub("", text)             # 其余标签剥离
    text = html_lib.unescape(text)            # &amp; 等实体还原
    # 压缩连续空行/空格
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()

# 默认接口（子域名带哈希，金十可能轮换；失效时用环境变量覆盖）
_DEFAULT_URL = ("https://3318fc142ea545eab931e22a61ec6e5c.z3c.jin10.com/flash")
JIN10_URL = os.environ.get("JIN10_FLASH_URL", _DEFAULT_URL)
FLASH_COOKIE = os.environ.get("FLASH_COOKIE", "")

_session = requests.Session()
_session.headers.update({
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "handleerror": "true",
    "origin": "https://www.jin10.com",
    "referer": "https://www.jin10.com/",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0",
    "cookie": FLASH_COOKIE,
})


def fetch_jin10() -> list:
    """
    拉取金十快讯（频道 1+5，**不按热度过滤**）。
    返回 [{id, time, hot, content, source, source_link, important, channel, collectedAt}]。
    Cookie 缺失/失效/接口异常 → 返回 []（调用方静默降级，不影响其他模块）。

    ★★ 2026-11-28 调查结论（**结论：hot 过滤没有漏采，维持原样**）：
      起因：本地库 `flash_news` 11-27 最早只有 13:24，而金十页面显示 12:23:26
        有国常会通稿 ⇒ 一度判断"政策通稿因热度未起被过滤"，并改成了全量拉取。
      证伪：用户查**线上库**，12:23:26 那条的 `hot` 字段是 **"沸"** ——
        本来就在 `["爆","沸","热"]` 里，**从来没被过滤掉**。
        本地库只是残缺快照（`policy_tone_log` 表都不存在、`jin10_tidy_news` 只到 11-06），
        拿它推断"漏采"属于**用残缺样本冒充全量**。
      ⇒ 已回滚为原参数。附带实测：同一时刻带/不带 hot 返回的两批各 50 条、
        几乎完全不相交；不带 hot 虽能覆盖 3.7 小时连续流，但**金十本身也是聚合方**，
        返回的并非原始全量 ⇒ "全量"这个说法不成立，取消过滤只增成本。
      ⚠️ 已知结构性风险（**不改，但要记住**）：`get_new_items` 的游标只进不退
        （`lastId = 最大 id`），任何真正被漏掉的条目都不可追回。
    """
    from app import health
    if not FLASH_COOKIE:
        print("[flash] 未配置 FLASH_COOKIE，跳过金十抓取")
        health.record("jin10", False, "未配置 FLASH_COOKIE")
        return []
    params = json.dumps({"hot": ["爆", "沸", "热"], "channel": [1, 5]})
    out = []
    try:
        r = _session.get(JIN10_URL, params={"params": params}, timeout=30)
        data = r.json()
        rows = data.get("data")
        if not isinstance(rows, list):
            print(f"[flash] 金十返回格式异常: {type(rows)}")
        else:
            for item in rows:
                d = item.get("data") or {}
                out.append({
                    "id": item.get("id", ""),
                    "time": item.get("time", ""),
                    "hot": item.get("hot", ""),
                    "content": _clean_html(d.get("content") or ""),
                    "source": d.get("source") or "",
                    "source_link": d.get("source_link") or "",
                    "important": item.get("important", 0),
                    "channel": item.get("channel") or [],
                    "collectedAt": store._now_iso(),
                })
    except Exception as e:
        print(f"[flash] 金十抓取失败: {e}")
        health.record("jin10", False, str(e))
        return []
    # 空列表视为失败（正常时金十总有几十条热榜；持续为空 ≈ Cookie 失效）
    health.record("jin10", bool(out), "" if out else "返回空列表（Cookie可能过期）")
    return out


def get_new_items(items: list) -> list:
    """
    游标去重：只返回 id > lastId 的新快讯（时间升序）。
    首次运行（无游标）只取最近 5 条，避免历史洪流。
    注意：会推进 lastId 游标（写状态文件）。
    """
    state = store.load_state()
    last_id = state.get("lastId") or ""
    # id 是时间戳式字符串（如 20260811202604699800），字典序即时间序
    sorted_items = sorted(items, key=lambda i: i["id"], reverse=True)
    if not last_id:
        new_items = sorted_items[:5]
        print("[flash] 首次运行，取最近 5 条")
    else:
        new_items = [i for i in sorted_items if i["id"] > last_id]
    if sorted_items:
        state["lastId"] = sorted_items[0]["id"]
        store.save_state(state)
    return list(reversed(new_items))    # 时间升序返回
