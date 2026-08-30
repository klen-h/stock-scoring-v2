"""
================================================================================
【文件作用】A股财务数据（东财 F10 主要财务指标）
================================================================================

给评分引擎的「成长」和「质量」因子提供数据源。这两个因子此前完全缺失——
routers/stock.py 里明说"没有独立的财务数据源，只用了实时行情里的 PE/PB/市值"。

数据源：datacenter-web.eastmoney.com
  ★ 与 push2.eastmoney.com（实时行情）是不同域名。实测后者被反爬封禁时，
    本接口依然可用 —— 财务数据这块不受行情域名封禁影响。

为什么不用 AKShare：只要营收增速/利润增速/ROE/负债率等几个字段，
AKShare 会引入 pandas + 几十个间接依赖，对这点需求性价比太低。

★ 三个必须处理的坑（都是实测踩出来的）：

  1. 结果混有非 A 股：不过滤是 13085 条，加 SECURITY_TYPE_CODE="058001001"
     后 6236 条。不做过滤会把港股/B股数据写进 A 股因子。

  2. 一季报/三季报字段缺失明显（实测 ROE 缺失约 17%）：这些列允许 NULL，
     ★ 且绝不能把 NULL 当 0 —— "未披露"和"ROE 为 0"是天差地别的两回事，
     当 0 算进因子会把一堆正常公司打成垃圾股。

  3. 未来函数（最危险）：必须用 notice_date（公告日）而非 report_date（报告期）
     判断数据可得性。2026 中报报告期是 6-30，但 8-29 才公告 —— 若 7 月就拿它
     回测，等于偷看未来，回测收益虚高。回测/评分一律用 get_finance_asof()。
================================================================================
"""

import time

import requests

from app.database import db

_EASTMONEY_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REPORT = "RPT_F10_FINANCE_MAINFINADATA"

# ★ A股类型过滤码：不加这个条件会返回 13085 条（含港股/B股等），加上后约 6236 条
_A_SHARE_TYPE = "058001001"

_PAGE_SIZE = 500
_PAGE_GAP = 0.2          # 翻页间隔（该域名反爬比 push2 宽松，但仍留间隔）
_TIMEOUT = 25

_FIELDS = ("SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,REPORT_TYPE,NOTICE_DATE,"
           "TOTALOPERATEREVE,PARENTNETPROFIT,TOTALOPERATEREVETZ,PARENTNETPROFITTZ,"
           "ROEJQ,ROEKCJQ,ZCFZL,XSMLL,XSJLL,EPSJB,BPS")

_session = requests.Session()
_session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
})


def _now_iso() -> str:
    from app.flash.rules import beijing_now
    return beijing_now().isoformat()


def _num(v):
    """
    → float 或 None。
    ★ None 必须保持 None：一季报/三季报的部分指标未披露（实测 ROE 缺失约 17%），
      转成 0 会让"未披露"被当成"该指标为 0"，因子结果严重失真。
    """
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v):
    """'2026-06-30 00:00:00' → '2026-06-30'"""
    if not v:
        return None
    return str(v)[:10] or None


# ================================================================
#  一、拉取
# ================================================================

def _fetch_page(report_date: str, page: int = 1):
    """单页请求。返回 (总条数, 本页数据)。"""
    params = {
        "reportName": _REPORT, "columns": _FIELDS,
        "pageNumber": str(page), "pageSize": str(_PAGE_SIZE),
        "filter": f"(REPORT_DATE='{report_date}')"
                  f"(SECURITY_TYPE_CODE=\"{_A_SHARE_TYPE}\")",
        "source": "WEB", "client": "WEB",
    }
    r = _session.get(_EASTMONEY_API, params=params, timeout=_TIMEOUT)
    res = (r.json() or {}).get("result") or {}
    return res.get("count") or 0, res.get("data") or []


def fetch_report(report_date: str, max_pages: int = 60) -> list:
    """拉取某报告期的全市场财务数据（自动翻页）。约 27 页 / 11 秒。"""
    out, page = [], 1
    while page <= max_pages:
        if page > 1:
            time.sleep(_PAGE_GAP)
        try:
            cnt, rows = _fetch_page(report_date, page)
        except Exception as e:
            print(f"[finance] 第{page}页失败 {report_date}: {e}")
            break
        if not rows:
            break
        out.extend(rows)
        if len(out) >= cnt or len(rows) < _PAGE_SIZE:
            break
        page += 1
    return out


def latest_report_date() -> str:
    """
    探测"当前已披露的最新报告期"：按公告日期倒序取第一条。

    ★ 查接口而不是按日历推算 —— 财报披露是渐进的（且个股会延期），
      用日期硬编码"4月底一定有一季报"会踩空。
    """
    params = {
        "reportName": _REPORT, "columns": "REPORT_DATE,REPORT_TYPE,NOTICE_DATE",
        "pageNumber": "1", "pageSize": "1",
        "sortColumns": "NOTICE_DATE", "sortTypes": "-1",
        "source": "WEB", "client": "WEB",
    }
    try:
        r = _session.get(_EASTMONEY_API, params=params, timeout=_TIMEOUT)
        rows = (r.json() or {}).get("result", {}).get("data") or []
        if rows:
            return _date(rows[0].get("REPORT_DATE")) or ""
    except Exception as e:
        print(f"[finance] 探测最新报告期失败: {e}")
    return ""


def _prev_report(rdate: str) -> str:
    """上一个报告期（按季度回退）。报告期固定为 03-31/06-30/09-30/12-31。"""
    y, m, _ = (int(x) for x in rdate.split("-"))
    if m == 3:
        return f"{y - 1}-12-31"
    if m == 6:
        return f"{y}-03-31"
    if m == 9:
        return f"{y}-06-30"
    return f"{y}-09-30"


# ================================================================
#  二、落库
# ================================================================

def _save_rows(rows: list) -> int:
    """
    写入 stock_finance（批量事务）。返回条数。

    ★ 必须用 upsert_many：远程云库（Supabase 东京）单条 upsert 实测 0.5s，
      逐条写 6000 条要 50 分钟；批量 ≈ 30 秒内。
    """
    now = _now_iso()
    out = []
    for r in rows:
        code = r.get("SECURITY_CODE")
        if not code:
            continue
        out.append({
            "code": code,
            "name": r.get("SECURITY_NAME_ABBR") or "",
            "report_date": _date(r.get("REPORT_DATE")),
            "report_type": r.get("REPORT_TYPE") or "",
            "notice_date": _date(r.get("NOTICE_DATE")),
            "revenue": _num(r.get("TOTALOPERATEREVE")),
            "profit": _num(r.get("PARENTNETPROFIT")),
            "revenue_yoy": _num(r.get("TOTALOPERATEREVETZ")),
            "profit_yoy": _num(r.get("PARENTNETPROFITTZ")),
            "roe": _num(r.get("ROEJQ")),
            "roe_deduct": _num(r.get("ROEKCJQ")),
            "debt_ratio": _num(r.get("ZCFZL")),
            "gross_margin": _num(r.get("XSMLL")),
            "net_margin": _num(r.get("XSJLL")),
            "eps": _num(r.get("EPSJB")),
            "bps": _num(r.get("BPS")),
            "updated_at": now,
        })
    if not out:
        return 0
    try:
        # page_size=1000：17 列 × 1000 行 ≈ 1.7 万参数，远低于 PG 65535 上限；
        # 6000 条只需 7 次 SQL 往返（默认 100 要 60 次，远程库每次 0.5s 差 30 秒）
        return db.upsert_many("stock_finance", out,
                              conflict_columns=["code", "report_date"], page_size=1000)
    except Exception as e:
        print(f"[finance] 批量写入失败: {e}")
        return 0


def refresh(reports: int = 4, verbose: bool = True) -> dict:
    """
    刷新财务数据：从最新报告期往前拉 N 期（默认 4 期 ≈ 覆盖一年）。

    成本：每期约 27 页 / 11 秒，4 期约 45 秒 —— 季度跑一次完全可接受。
    返回 {ok, latest_report, reports:{报告期:条数}, written, cost_sec}
    """
    t0 = time.time()
    latest = latest_report_date()
    if not latest:
        return {"ok": False, "error": "无法探测最新报告期（接口异常？）"}

    out = {"ok": True, "latest_report": latest, "reports": {}, "written": 0}
    rd = latest
    for _ in range(max(1, reports)):
        rows = fetch_report(rd)
        n = _save_rows(rows) if rows else 0
        out["reports"][rd] = n
        out["written"] += n
        if verbose:
            print(f"[finance] {rd}: 拉取 {len(rows)} 条，写入 {n} 条")
        rd = _prev_report(rd)
    out["cost_sec"] = round(time.time() - t0, 1)
    if out["written"]:
        clear_finance_cache()   # 新财报入库后立即生效，不用等 30 分钟缓存过期
    return out


# ================================================================
#  三、查询
# ================================================================

def has_report(report_date: str) -> bool:
    """库里是否已有某报告期的数据（调度器判断"要不要拉"用）。"""
    row = db.fetch_one("SELECT COUNT(*) AS n FROM stock_finance WHERE report_date = %s",
                       (report_date,))
    return int((row or {}).get("n") or 0) > 0


def get_finance(code: str, report_date: str = None) -> dict:
    """查单只股票的财报；不传 report_date 则取最新一期。"""
    if report_date:
        row = db.fetch_one("SELECT * FROM stock_finance WHERE code = %s "
                           "AND report_date = %s", (code, report_date))
    else:
        row = db.fetch_one("SELECT * FROM stock_finance WHERE code = %s "
                           "ORDER BY report_date DESC LIMIT 1", (code,))
    return dict(row) if row else {}


def get_finance_asof(code: str, date_str: str) -> dict:
    """
    ★ 防未来函数：取在 date_str 这天【已经公告】的最新一期财报。

    用 notice_date（公告日）过滤，而不是 report_date（报告期）。
    回测和"当时视角"的评分都必须走这个接口，否则会用尚未披露的数据，
    得出虚高的回测收益。
    """
    row = db.fetch_one(
        "SELECT * FROM stock_finance WHERE code = %s "
        "AND notice_date IS NOT NULL AND notice_date <= %s "
        "ORDER BY report_date DESC LIMIT 1", (code, date_str))
    return dict(row) if row else {}


# ── 进程级缓存 ──
# 财报是季度更新的低频数据，而评分排行每 3 分钟就要重算一次（_RANK_CACHE_TTL=180）。
# 每次都去远程库（Supabase 往返 0.5s 起）拉同样的 1550 行纯属浪费，
# 所以缓存起来：财报刷新后最多延迟 _FIN_CACHE_TTL 秒生效，完全可接受。
_fin_cache = {"data": {}, "ts": 0.0}
_FIN_CACHE_TTL = 1800        # 30 分钟
_SQL_BATCH = 500             # 单条 SQL 的代码数上限（SQLite 默认参数上限 999，留足余量）


def clear_finance_cache() -> None:
    """清空财务缓存（手动刷新财务数据后调用，让新数据立即生效）。"""
    _fin_cache["data"] = {}
    _fin_cache["ts"] = 0.0


def get_finance_batch(codes: list, force: bool = False) -> dict:
    """
    批量查多只股票的【最新一期】财报，返回 {code: row}。

    ★ 是按 codes 过滤，不是拉全表：
      评分池已剔除创业板/科创板/ST（见 tencent.py 的 DISABLED_PREFIXES 与
      EXCLUDE_ST），实际约 1550 只；而 stock_finance 全表有 6236 只
      （含港股/创业板/科创板等）。只查需要的代码能少传约 3/4 的数据量。

    ★ 两层优化：
      1. SQL 只取每只股票的最新一期（相关子查询），而不是拉回全部报告期再筛
      2. 进程级缓存 30 分钟，命中则零 DB 往返

    兼容性说明：
      - 没用 PostgreSQL 的 DISTINCT ON（SQLite 不支持），改用相关子查询
      - 分批发送：SQLite 默认参数上限是 999，一次性传 1550 个 code 会报错
    """
    if not codes:
        return {}
    codes = list(codes)
    now = time.time()

    # 缓存命中：请求的代码全部在缓存里且未过期
    if not force and _fin_cache["data"] and now - _fin_cache["ts"] < _FIN_CACHE_TTL:
        cached = _fin_cache["data"]
        if all(c in cached for c in codes):
            return {c: cached[c] for c in codes if c in cached}

    out = {}
    for i in range(0, len(codes), _SQL_BATCH):
        chunk = codes[i:i + _SQL_BATCH]
        ph = ",".join(["%s"] * len(chunk))
        rows = db.fetch(
            "SELECT code, name, report_date, report_type, notice_date, revenue, "
            "profit, revenue_yoy, profit_yoy, roe, roe_deduct, debt_ratio, "
            "gross_margin, net_margin FROM stock_finance f "
            f"WHERE f.code IN ({ph}) AND f.report_date = ("
            "SELECT MAX(f2.report_date) FROM stock_finance f2 WHERE f2.code = f.code)",
            tuple(chunk))
        for r in (rows or []):
            if r.get("code"):
                out[r["code"]] = dict(r)

    # 增量并入缓存（不整体丢弃，避免不同请求的代码集互相把缓存清空）
    _fin_cache["data"].update(out)
    _fin_cache["ts"] = now
    return out


def get_history(code: str, limit: int = 12) -> list:
    """某只股票的财报历史（最新在前）。"""
    rows = db.fetch("SELECT * FROM stock_finance WHERE code = %s "
                    "ORDER BY report_date DESC LIMIT %s", (code, limit))
    return [dict(r) for r in (rows or [])]


def stats() -> dict:
    """财务表概况：覆盖多少只、最新报告期、各期条数。"""
    try:
        total = db.fetch_one("SELECT COUNT(*) AS n FROM stock_finance")
        by_report = db.fetch(
            "SELECT report_date, report_type, COUNT(*) AS n, "
            "MAX(notice_date) AS last_notice FROM stock_finance "
            "GROUP BY report_date, report_type ORDER BY report_date DESC LIMIT 8")
        missing = db.fetch_one(
            "SELECT COUNT(*) FILTER (WHERE roe IS NULL) AS roe_null, "
            "COUNT(*) FILTER (WHERE revenue_yoy IS NULL) AS rev_null, "
            "COUNT(*) AS n FROM stock_finance")
        return {
            "rows": int((total or {}).get("n") or 0),
            "reports": [{"report_date": r["report_date"],
                         "report_type": r["report_type"],
                         "count": r["n"],
                         "last_notice": r["last_notice"]} for r in (by_report or [])],
            "missing": {"roe_null": int((missing or {}).get("roe_null") or 0),
                        "revenue_yoy_null": int((missing or {}).get("rev_null") or 0),
                        "total": int((missing or {}).get("n") or 0)},
        }
    except Exception as e:
        return {"error": str(e)}
