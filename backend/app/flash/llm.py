"""
================================================================================
【文件作用】LLM 客户端 + 提示词 + 诊断/复盘（移植自 prompt.js / fetch-flash.js / review.js）
================================================================================

两条 LLM 流：
  1. 诊断流（快讯触发，JSON 模式）：油金相关性 2×2 / D状态 D1-D2 / 情景推演 /
     d_state_compliance 自报 + 代码层审查剥离
  2. 复盘流（盘前/午间/盘后，Markdown）：三段技能 + 趋势上下文 +
     【结构化信号输出】（修复原项目用正则从 Markdown 抓数字的缺陷）

相对原项目的增强（闭环意义所在）：LLM 输入额外注入 v2 已有的
  市场温度 / 宏观规则方向分 / 板块资金流 Top5 —— 原项目看不到这些内部状态。

环境变量：LLM_API_KEY / LLM_BASE_URL(默认 SiliconFlow) / LLM_MODEL
无 API_KEY 时：所有函数返回"未配置"占位结果，模块整体降级，不崩。
================================================================================
"""

import os
import re
import json
import time
import threading
import requests

from app.flash import rules, store
from app.database import db

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

# 可选：费用估算（元/百万 token）。不配则 status 只显示 token 数不算钱。
LLM_PRICE_IN = float(os.environ.get("LLM_PRICE_IN", "0") or 0)    # 输入价
LLM_PRICE_OUT = float(os.environ.get("LLM_PRICE_OUT", "0") or 0)  # 输出价

# 每日熔断：当日 LLM 调用达到上限后自动停用（事件流照常记录），次日自动恢复。
# 防事件风暴日（簇不停升爆重推）导致账单失控。默认 50 次/天，足够覆盖极端行情。
LLM_DAILY_MAX_CALLS = int(os.environ.get("LLM_DAILY_MAX_CALLS", "50") or 50)

_session = requests.Session()
_session.headers.update({"Authorization": f"Bearer {LLM_API_KEY}",
                         "Content-Type": "application/json"})


def llm_configured() -> bool:
    return bool(LLM_API_KEY and LLM_MODEL)


def _today_calls() -> int:
    """今日已成功调用的次数（读用量文件）。"""
    daily = store._load(_USAGE_PATH, {"daily": {}}).get("daily", {})
    return int(daily.get(store._bj_date(), {}).get("calls") or 0)


def llm_blocked_reason():
    """
    LLM 当前是否被熔断。返回 None（可用）或阻断原因文案。
    熔断条件：未配置 / 今日调用达到 LLM_DAILY_MAX_CALLS。
    """
    if not llm_configured():
        return "未配置 LLM_API_KEY / LLM_MODEL"
    if _today_calls() >= LLM_DAILY_MAX_CALLS:
        return (f"今日 LLM 调用已达上限（{LLM_DAILY_MAX_CALLS}次），"
                f"事件流照常记录但暂停分析，明日自动恢复")
    return None


# ================================================================
#  LLM 用量统计（每日调用次数 / token，落盘 data/llm_usage.json）
# ================================================================

_usage_lock = threading.Lock()
_USAGE_PATH = os.path.join(store.DATA_DIR, "llm_usage.json")
_USAGE_KEEP_DAYS = 30


def _record_usage(usage: dict) -> None:
    """记录一次调用的 token 用量到当日汇总（保留 30 天）。"""
    day = store._bj_date()
    with _usage_lock:
        data = store._load(_USAGE_PATH, {"daily": {}})
        d = data["daily"].setdefault(day, {"calls": 0, "prompt_tokens": 0,
                                           "completion_tokens": 0})
        d["calls"] += 1
        d["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        d["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        # 只保留最近 30 天
        if len(data["daily"]) > _USAGE_KEEP_DAYS:
            data["daily"] = dict(sorted(data["daily"].items())[-_USAGE_KEEP_DAYS:])
        store._save(_USAGE_PATH, data)


def get_llm_usage() -> dict:
    """用量概览：今日 + 最近 7 天。配置了单价则附费用估算。"""
    data = store._load(_USAGE_PATH, {"daily": {}})
    daily = data.get("daily", {})
    today = store._bj_date()

    def _cost(u):
        if not (LLM_PRICE_IN or LLM_PRICE_OUT):
            return None
        return round((u.get("prompt_tokens", 0) / 1e6 * LLM_PRICE_IN
                      + u.get("completion_tokens", 0) / 1e6 * LLM_PRICE_OUT), 3)

    recent = dict(sorted(daily.items())[-7:])
    today_usage = daily.get(today, {"calls": 0, "prompt_tokens": 0,
                                    "completion_tokens": 0})
    return {"today": today_usage,
            "daily_limit": LLM_DAILY_MAX_CALLS,
            "remaining_today": max(0, LLM_DAILY_MAX_CALLS - today_usage.get("calls", 0)),
            "blocked_reason": llm_blocked_reason(),
            "recent_days": recent,
            "estimated_cost_yuan": {d: _cost(u) for d, u in recent.items()},
            "prices_configured": bool(LLM_PRICE_IN or LLM_PRICE_OUT)}


# ================================================================
#  一、LLM 调用封装
# ================================================================

def call_llm(system: str, user: str, temperature: float = 0.3,
             json_mode: bool = False, retries: int = 3) -> str:
    """
    OpenAI 兼容 chat/completions 调用，带重试（3s/6s 退避）。
    返回文本内容；全部失败返回空字符串（调用方降级）。
    """
    if not llm_configured():
        return ""
    blocked = llm_blocked_reason()
    if blocked:
        print(f"[llm] 熔断: {blocked}")
        return ""
    body = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        # 推理模型（如 DeepSeek-R1）的思考过程也消耗输出 token，8192 才够
        # 「长推理 + 完整 JSON 答案」；4096 会出现答案被截断。
        "max_tokens": 8192,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(1, retries + 1):
        try:
            # 推理模型较慢（长 prompt 可能思考数分钟），超时给足（原 JS 项目 1200s）
            r = _session.post(f"{LLM_BASE_URL}/chat/completions", json=body, timeout=600)
            r.raise_for_status()
            data = r.json()
            _record_usage(data.get("usage") or {})      # 记录 token 用量
            content = data["choices"][0]["message"].get("content") or ""
            # 推理模型的思考在 reasoning_content 字段，content 即最终答案；
            # 若 max_tokens 被思考耗尽，content 可能为空 → 视为失败走重试/降级
            return content.strip()
        except Exception as e:
            print(f"[llm] 第{attempt}次调用失败: {str(e)[:200]}")
            if attempt < retries:
                time.sleep(attempt * 3)
    return ""


def _call_json(system: str, user: str, temperature: float = 0.1) -> dict:
    """JSON 模式调用并解析；失败返回空 dict。"""
    text = call_llm(system, user, temperature=temperature, json_mode=True)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 容错：模型有时在 JSON 外面包 ```json 围栏
        m = re.search(r"```json\s*(.+?)\s*```", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        print("[llm] JSON 解析失败")
        return {}


# ================================================================
#  二、提示词（移植 const/prompt.js，关键点位改为可配置）
# ================================================================

def get_core_skill() -> str:
    """核心约束：油金相关性 2×2 / D 状态 / 交叉验证 / 互斥情景 / 传导链。"""
    return """# 任务
**在完成以下所有任务时，必须遵守这些核心约束：**

### 1. 原油-黄金相关性诊断（优先级最高）

**状态诊断强制核对表**（必须逐项对应，禁止自创状态名称或叙事逻辑）：
- 原油涨 + 黄金涨 = 正相关 → 通胀/滞胀交易（需求过热或供给冲击）
- 原油涨 + 黄金跌 = 负相关 → 紧缩/实际利率飙升（通胀预期倒逼加息，压制金价）
- 原油跌 + 黄金跌 = 正相关 → 衰退或流动性危机
- 原油跌 + 黄金涨/平 = 负相关 → **D状态**

**D状态专属规则**（若诊断为D状态则强制执行）：
- ❌ 严禁推荐做空黄金
- ❌ 严禁推荐抄底油气ETF
- ✅ 允许推荐科技ETF（成本下降）、黄金ETF（独立支撑）
- ⚠️ 军工ETF注意：A股军工板块对海外冲突事件（中东/俄乌等）的历史联动很弱，
  其驱动主要来自国防预算、军贸订单、重大装备进展等本土因素。
  仅当事件直接涉及中国国防政策/军贸时才可推荐军工ETF，不要把海外冲突简单映射为军工利好。

**扩展诊断维度**（在基本状态判断后必须执行）：
- 负相关（原油涨+黄金跌）+ 铜跌 → 衰退预警（供给冲击正在扼杀需求）
- 负相关（原油涨+黄金跌）+ 铜涨 → 真紧缩/需求韧性（实际利率驱动为主）
- 负相关（原油涨+黄金跌）+ 铜涨+白银涨 → 供给冲击+工业韧性，滞胀情景强化
- 负相关时，必须交叉验证美元和纳指走势：
    - 若【美元走弱 + 纳指下跌】，可能是"供给冲击+避险消退"假象
    - 若【美元走强 + 纳指下跌】，紧缩逻辑成立

**利率验证**（若美债收益率数据可用）：
- 负相关 + 美债收益率↑ → 真紧缩成立；负相关 + 美债收益率↓ → 供给冲击假象
- 正相关 + 美债收益率↑ → 滞胀强化；正相关 + 美债收益率↓ → 通胀交易

**VIX情绪验证**（若VIX数据可用）：
- VIX < 15：极度乐观，警惕回调风险；15~20：正常；20~30：担忧升温；> 30：恐慌
- VIX与纳指同跌：定价"基本面走弱"；VIX飙升+纳指暴跌：恐慌性抛售，短期可能超卖

### 2. 白银交叉验证（辅助，若数据可用）
- 白银涨幅明显大于黄金（日内差距>2%）：负相关可能是假象（同时定价避险+工业需求）
- 白银与原油同向且涨幅接近：供给冲击叙事，强化滞胀情景

### 3. 铜与比价交叉验证（若数据可用）
- 铜价涨+油价涨 → 需求扩张+供给偏紧，滞胀叙事强化
- 铜价跌+油价涨 → 供给冲击正在消耗需求韧性，警惕衰退风险
- 铜油比趋势作为情景概率更新辅助锚定；金银比上升=纯避险，下降=工业需求占上风

### 4. 情景逻辑互斥提醒
滞胀（供给冲击）/紧缩（央行加息）/软着陆（通胀受控增长平稳）三者理论互斥。
若多事件分别强化互斥情景，必须指出市场主要在定价哪一种。

### 5. 策略传导链要求
给出任何方向建议时，必须附带完整的传导链（事件→宏观变量→行业/资产→ETF），不允许跳步。

### 6. 策略审慎性约束
- 必须考虑当前价格是否处于极端波动后的短期高位/低位（如单日涨跌幅超过4%）
- 禁止在日内暴涨暴跌后立即推荐追涨/杀跌，除非有明确反转信号且已说明

### 7. 策略与诊断一致性
策略建议必须与相关性状态诊断一致。若诊断为负相关（紧缩逻辑），不推荐以"通胀/避险"
为核心的配置，除非能明确指出负相关是特殊事件造成的假象（需在矛盾信号中说明）。"""


_SIGNAL_SCHEMA_HINT = """### 信号输出格式（必须严格遵守）
在报告的最后，输出一个 ```json 围栏代码块，内容形如：
```json
{"signals": [
  {"etfName": "黄金ETF", "direction": "long", "support": 7.10, "resistance": 7.55, "reasoning": "避险需求"}
]}
```
字段要求：
- etfName 必须严格从上面给出的 ETF 列表中选择
- direction 只能是 "long"（做多）或 "short"（做空）；没有合适信号就输出空数组
- support / resistance 必须是数字（基于当前价格给出，不是百分比）
- reasoning 一句话说明传导逻辑
没有把握时输出空数组，禁止编造点位。"""


def get_premarket_skill() -> str:
    return f"""## 盘前专属规则
**具体任务清单：**
1. **情景概率更新**：基于事件簇和资产收线，对昨日情景（软着陆/滞胀/衰退等）概率做倾向性调整。
2. **核心叙事修正**：当前主导叙事是否变化？如有，指出新叙事和脆弱点。
3. **开盘关键锚点**：今日开盘最需关注的 3 个价格/指标。
4. **今日策略基调**：整体仓位建议（进攻/防守/观望）、重点关注方向，附传导链。
5. **具体交易信号**：从给出的 ETF 中选 1-3 个给出具体建议（支撑/阻力/多空/理由）。
{_SIGNAL_SCHEMA_HINT}
6. **风险警示**：今日可能的黑天鹅或灰犀牛。"""


def get_midmarket_skill() -> str:
    return f"""## 午盘专属规则
**具体任务清单：**
1. **上午验证**：基于上午事件和 ETF 表现，验证早盘策略是否正确？哪些传导链成立？
2. **情景概率更新**：基于上午新信息调整情景概率。
3. **核心叙事修正**：上午表现是否改变主导叙事？
4. **下午关键锚点**：下午最需关注的 3 个价格/指标。
5. **下午策略基调**：仓位建议、重点关注方向，附传导链。
6. **具体交易信号**：从给出的 ETF 中选 1-3 个给出具体建议。
{_SIGNAL_SCHEMA_HINT}
7. **风险警示**：下午可能的黑天鹅或灰犀牛。"""


def get_postmarket_skill() -> str:
    return """## 盘后专属规则
**具体任务清单：**
1. **事件簇影响评估**：今日推送的事件簇中，哪些对盘面产生了实质性影响？传导链是否成立？
2. **逻辑自洽检验**：今日资产表现是否表明宏观框架需要修正？
3. **错失信号识别**：今日盘面是否有无法用已推送事件解释的异动？
4. **框架修正建议**：是否需要调整原油-黄金相关性判断？对 D 状态持仓建议有何反思？
5. **明日初步预案**：明日核心观察指标和潜在情景。

（盘后复盘不要求输出交易信号 json 块。）"""


# 关键心理价位（原项目硬编码在 prompt.js 中，这里抽为模块级配置便于调整）
KEY_LEVELS = {
    "wti": [95, 100, 105],
    "brent": [98, 103, 108],
    "gold": [4650, 4700, 4750],
    "silver": [84, 86, 88],
    "copper": [4.50, 4.80, 5.10],
    "nasdaq": [28500, 29000, 29500],
    "dxy": [97.5, 98.0, 98.5],
    "usdcnh": [6.75, 6.80, 6.85],
}


def get_trend_context(symbol: str, item: dict, history: list) -> str:
    """
    生成资产的趋势描述：日内动能 / 最近关键位及距离 / 振幅 / 连涨连跌 / 近5日高低。
    item: 面板中该品种的 {price, prev_close, change_pct, high, low}；
    history: 宏观历史数组（store.load_macro_history()）。
    """
    price = item.get("price") or 0
    if price <= 0:
        return ""
    prev = item.get("prev_close") or 0
    parts = []
    # 日内动能（无开盘价，用昨收近似）
    if price > prev:
        parts.append("动能偏多")
    elif price < prev:
        parts.append("动能偏空")
    # 关键位
    levels = KEY_LEVELS.get(symbol, [])
    above = next((l for l in levels if l > price), None)
    below = next((l for l in reversed(levels) if l < price), None)
    if above:
        parts.append(f"阻力{above}（{(above - price) / price * 100:.1f}%）")
    if below:
        parts.append(f"支撑{below}（{(price - below) / price * 100:.1f}%）")
    # 振幅
    hi, lo = item.get("high") or 0, item.get("low") or 0
    if hi and lo:
        amp = (hi - lo) / price * 100
        if amp > 2:
            parts.append(f"日内振幅{amp:.1f}%（偏大）")
    # 近期趋势（历史里该 symbol 的最近 5 条）
    recent = [h.get(symbol, {}).get("price") for h in history[-5:]]
    recent = [p for p in recent if p]
    if len(recent) >= 2:
        up = down = 0
        for a, b in zip(recent, recent[1:]):
            if b > a:
                up, down = up + 1, 0
            elif b < a:
                down, up = down + 1, 0
        if up >= 2:
            parts.append(f"连涨{up}日")
        elif down >= 2:
            parts.append(f"连跌{down}日")
        if recent:
            parts.append(f"近5日高{max(recent + [price]):.2f} 低{min(recent + [price]):.2f}")
    return "，".join(parts)


# ================================================================
#  三、通用格式化（宏观快照 / 事件簇 / ETF）
# ================================================================

def format_macro_snapshot(panel: dict, history: list) -> str:
    """宏观面板 → 提示词文本（带趋势上下文）。panel 为 app/macro.get_macro_panel()。"""
    lines = []

    def add(label, symbol, key, dollar=True):
        item = panel.get(key)
        if not item or not item.get("price"):
            return
        chg = item.get("change_pct") or 0
        ctx = get_trend_context(symbol, item, history)
        prefix = "$" if dollar else ""
        ctx_s = f"  [{ctx}]" if ctx else ""
        lines.append(f"- {label}: {prefix}{item['price']} "
                     f"({'+' if chg > 0 else ''}{chg:.2f}%){ctx_s}")

    add("布伦特原油", "brent", "brent")
    add("纽约原油", "wti", "wti")
    add("COMEX黄金", "gold", "gold")
    add("COMEX白银", "silver", "silver")
    add("COMEX铜", "copper", "copper")
    d = panel.get("_derived", {})
    ratios = []
    if d.get("copper_oil_ratio"):
        ratios.append(f"铜油比: {d['copper_oil_ratio']}")
    if d.get("gold_silver_ratio"):
        ratios.append(f"金银比: {d['gold_silver_ratio']}")
    if d.get("copper_gold_ratio"):
        ratios.append(f"铜金比: {d['copper_gold_ratio']}")
    if ratios:
        lines.append(f"- {'  '.join(ratios)}")
    add("纳指期货", "nasdaq", "nasdaq")
    add("日经225期货", "nikkei", "nikkei")
    add("恒生科技指数", "", "hstech", dollar=False)
    add("美元指数", "dxy", "dxy", dollar=False)
    add("离岸人民币", "usdcnh", "usdcnh", dollar=False)
    # 美债收益率曲线：2Y=政策利率预期锚，10Y=基准，30Y=长端折现率锚
    for key, label in (("us2y", "美2年期国债收益率%"), ("us10y", "美10年期国债收益率%"),
                       ("us30y", "美30年期国债收益率%")):
        add(label, "", key, dollar=False)
    if d.get("us_curve_10y2y_bp") is not None:
        cur_chg = d.get("us_curve_bp_change", 0) or 0
        flatten = "走平/倒挂加深=加息预期升温" if cur_chg < -2 else ("陡峭化=宽松预期" if cur_chg > 2 else "形态稳定")
        lines.append(f"- 美债10Y-2Y利差: {d['us_curve_10y2y_bp']}bp "
                     f"({'+' if cur_chg > 0 else ''}{cur_chg}bp/日, {flatten})")
    add("VIX", "", "vix", dollar=False)
    # 国内增强项
    add("富时A50期货", "", "a50", dollar=False)
    add("中国10年期国债收益率%", "", "cn10y", dollar=False)
    black = []
    for k, name in (("rebar", "螺纹钢"), ("iron", "铁矿石")):
        item = panel.get(k)
        if item and item.get("price"):
            black.append(f"{name}{item['price']}({'+' if item['change_pct'] > 0 else ''}{item['change_pct']:.2f}%)")
    if black:
        lines.append(f"- 黑色系: {'，'.join(black)}")
    return "\n".join(lines)


def _internal_context() -> str:
    """
    【闭环增强】注入 v2 内部状态：市场温度 + 宏观方向分 + 板块资金流 Top5。
    任一获取失败就跳过该段，不影响 prompt 其余部分。
    """
    sections = []
    try:
        from app.routers.market import market_temperature
        t = market_temperature()
        if isinstance(t, dict) and t.get("temperature") is not None:
            sections.append(
                f"- 市场环境温度: {t['temperature']}（{t['level']}）| 涨跌比 "
                f"{t['breadth']['ratio']}（{t['breadth']['up']}涨/{t['breadth']['down']}跌）| {t['advisory']}")
    except Exception:
        pass
    try:
        from app.macro import get_macro_snapshot
        snap = get_macro_snapshot()
        dirn = snap.get("direction", {})
        tags = "、".join(snap.get("tags_bull", []) + snap.get("tags_bear", [])) or "无"
        sections.append(
            f"- 宏观规则方向分: {dirn.get('score')}（{dirn.get('level')}）| 触发标签: {tags}")
    except Exception:
        pass
    try:
        from app.eastmoney import get_sector_flow
        flows = get_sector_flow("industry", limit=5)
        if flows:
            top = "，".join(f"{f['name']}({'+' if f['net_inflow'] > 0 else ''}{f['net_inflow'] / 1e8:.1f}亿)"
                            for f in flows)
            sections.append(f"- 行业主力净流入 Top5: {top}")
    except Exception:
        pass
    if not sections:
        return ""
    return "## 内部市场状态（量化系统提供，原项目没有的增量信息）\n" + "\n".join(sections) + "\n"


def _calendar_context(days: int = 3) -> str:
    """
    【财经日历】未来 days 天内的重要宏观事件（★>=4）。

    与 _internal_context() 分开注入：后者是"本系统的量化状态"（市场温度/方向分），
    而日历是外部排期数据（非农/CPI/央行决议的时间与前值/预期）。

    为什么只送 ★>=4：实测一周 64 条里 3 星占 54 条，用 >=3 等于没过滤（84% 都是
    3 星，白烧 token）；>=4 只剩个位数，才是真正能驱动行情的事件。

    无数据/异常 → 返回空串，不影响 prompt 其余部分（与 _internal_context 同策略）。
    """
    try:
        from app.flash.calendar import format_for_llm
        text = format_for_llm(days=days, min_star=4, limit=8)
        if text:
            return ("## 近期重要财经事件（金十日历，★=重要性）\n"
                    f"{text}\n\n")
    except Exception as e:
        print(f"[llm] 财经日历注入失败（跳过）: {e}")
    return ""


def format_cluster_text(clusters: list) -> str:
    """事件簇列表 → 提示词文本（簇名 + 更新次数 + 原文引用）。"""
    if not clusters:
        return "暂无事件簇。"
    raw_items = {i["id"]: i for i in store.load_raw_items()}
    lines = []
    for idx, c in enumerate(clusters):
        urgent = "[⏰时间敏感]" if c.get("hadUrgent") else ""
        hot = "🔴" if c.get("hotMax") == "爆" else "🟠"
        lines.append(f"{idx + 1}. {hot} {urgent} **{c['cluster']}** (更新{c.get('pushCount', 1)}次)")
        item = raw_items.get(c.get("lastUpdateId"))
        if item and item.get("content"):
            lines.append(f"   > {item['content']}")
    return "\n".join(lines)


def format_etf_list(holdings: list, core_etfs: list) -> str:
    """ETF 实时价格表（复盘流给 LLM 报点位用）。"""
    if not holdings:
        return "暂无ETF实时价格数据。"
    lines = ["## ETF实时价格表", "请基于这些价格给出支撑位和阻力位建议：", ""]
    for h in holdings:
        if h["name"] not in core_etfs:
            continue
        icon = "🔺" if h["change"] >= 0 else "🔻"
        lines.append(f"- **{h['name']}**：现价 {h['price']}，涨跌 {icon}{h['changeStr']}%")
    return "\n".join(lines)


def format_etf_performance(holdings: list) -> str:
    if not holdings:
        return "无ETF数据（非交易时段）。"
    s = sorted(holdings, key=lambda h: h["change"], reverse=True)
    top = " | ".join(f"🔺 {h['name']} +{h['changeStr']}%" for h in s[:5])
    bottom = " | ".join(f"🔻 {h['name']} {h['changeStr']}%" for h in s[-5:])
    return f"**领涨**：{top}\n**领跌**：{bottom}"


def format_etf_history(days: int = 7) -> str:
    history = store.load_etf_close_history(days)
    if not history:
        return ""
    lines = ["## ETF历史收盘（最近几天）", ""]
    for day in history:
        holdings = day.get("holdings", [])[:6]
        disp = " | ".join(f"{h['name']} {h['price']}" for h in holdings)
        lines.append(f"- {day.get('date', '')}: {disp}")
    return "\n".join(lines) + "\n"


# ================================================================
#  四、诊断流（快讯触发，JSON 模式）
# ================================================================

def _format_holdings_for_llm(holdings: list, holdings_text: str) -> str:
    """
    ETF 盘面文本。【修复原项目 bug】原版遍历了字符串 HOLDINGSTEXT 导致 ETF 区块恒为空；
    这里直接按涨跌幅分组输出全部持仓。
    """
    if not holdings:
        return ('当前为非交易时段，无ETF实时盘面数据。"盘面交叉验证"改为'
                '"逻辑自洽性检验"（新闻之间是否矛盾？）。')
    s = sorted(holdings, key=lambda h: h["change"], reverse=True)
    rows = " | ".join(
        f"{'🔺' if h['change'] > 0 else '🔻' if h['change'] < 0 else '➖'}"
        f"{h['name']} {'+' if h['change'] > 0 else ''}{h['changeStr']}%" for h in s)
    return f"## ETF盘面（按涨跌幅排序）\n{rows}\n"


def _fmt_item(item: dict) -> str:
    """面板单品种 → 诊断 prompt 的一行（含昨结/涨跌/高低）。"""
    chg = item.get("change_pct") or 0
    return (f"{item.get('price')} (昨结:{item.get('prev_close')} "
            f"{'+' if chg > 0 else ''}{chg}%) [日内高:{item.get('high')} 低:{item.get('low')}]")


def build_diagnosis_prompt(clusters: list, panel: dict, holdings: list,
                           quality: dict, holdings_text: str) -> str:
    """诊断流完整 prompt（数据预检 + 核心诊断逻辑 + 持仓映射 + 事件簇 + JSON schema）。"""
    flash_text = []
    for c in clusters:
        size_tag = f" [本簇共{c['_clusterSize']}条]" if c["_clusterSize"] > 1 else ""
        oil_tag = " [原油核心]" if c["_cluster"] in ("原油能源", "伊朗局势", "中东战争") else ""
        urgent_tag = " [时间敏感]" if rules.has_urgent_time(c.get("content") or "") else ""
        contents = list(dict.fromkeys((it.get("content") or "").strip() for it in c["_allItems"]))
        body = "\n".join(f"{i + 1}. {t}" if len(contents) > 1 else t for i, t in enumerate(contents))
        flash_text.append(
            f"[{c['_clusterHot']}]{size_tag}{oil_tag}{urgent_tag} {c['_cluster']}\n"
            f"时间: {c.get('time')}\n内容:\n{body}")
    flash_text = "\n\n---\n\n".join(flash_text)

    clock = quality["market_clock"]
    clock_desc = ("A股/港股盘中，ETF实时数据可用，可进行盘面交叉验证"
                  if clock["is_a_stock_trading"] else
                  "美股活跃时段，ETF已收盘，仅能进行逻辑自洽检验" if clock["is_us_trading"] else
                  "亚盘已收盘，所有ETF无实时数据，盘面验证自动降级为逻辑自洽检验")
    d = panel.get("_derived", {})
    g = panel.get

    return f"""【角色定义】
你目前是宏观交易信号过滤引擎。你的首要任务不是"给出答案"，而是"诚实地评估数据能支撑什么结论"。
核心原则：宁可不交易，不可用残缺数据做决策。

## 第一部分：数据预检（必须优先执行）
### 1.1 当前数据快照
- 数据质量评估状态: {json.dumps(quality, ensure_ascii=False)}
- 当前市场时段: {clock['beijing_time']} 北京时间 | {clock_desc}

【核心能源与避险】
- 布伦特原油: {_fmt_item(g('brent') or {})}
- 纽约原油: {_fmt_item(g('wti') or {})}
- COMEX黄金: {_fmt_item(g('gold') or {})}
- 黄金现货: {g('gold_spot', {}).get('price', '未知')}

【全球风险资产】
- 纳指期货(NQ): {_fmt_item(g('nasdaq') or {})}
- 恒生科技指数: {_fmt_item(g('hstech') or {})}

【宏观定价锚与汇率】
- 美元指数(DXY): {_fmt_item(g('dxy') or {})}
- 离岸人民币(CNH): {_fmt_item(g('usdcnh') or {})}
- 美10年期国债收益率: {_fmt_item(g('us10y') or {})}
- 美2年期国债收益率: {_fmt_item(g('us2y') or {})}（政策利率预期锚）
- 美30年期国债收益率: {_fmt_item(g('us30y') or {})}（长端折现率锚）
- 美债10Y-2Y利差: {d.get('us_curve_10y2y_bp', 'N/A')}bp（日变化{d.get('us_curve_bp_change', 0):+.1f}bp，走平=加息预期升温）
- VIX恐慌指数: {_fmt_item(g('vix') or {})}

【工业需求与交叉验证】
- COMEX白银: {_fmt_item(g('silver') or {})}
- COMEX铜: {_fmt_item(g('copper') or {})}

【国内增量数据】
- 富时A50期货: {_fmt_item(g('a50') or {})}
- 中国10年期国债收益率: {_fmt_item(g('cn10y') or {})}

【前置宏观比率(极高参考价值)】
- 金银比: {d.get('gold_silver_ratio', '未知')} (避险/工业情绪，>80极度恐慌)
- 铜金比: {d.get('copper_gold_ratio', '未知')} (经济动能/避险，越小越衰退)
- 铜油比: {d.get('copper_oil_ratio', '未知')} (需求/供给博弈)

### 1.2 盘面实况 (ETF)
{_format_holdings_for_llm(holdings, holdings_text)}

{_internal_context()}
{_calendar_context(2)}

## 第二部分：核心诊断逻辑
### 2.1 盘面交叉验证
- 若存在盘面数据，验证新闻逻辑与盘面表现是否一致；否则降级为"逻辑自洽性检验"。
- 【强制反身性校验】：若新闻利多但盘面放量滞涨/高开低走，或新闻利空但盘面缩量抗跌/低开高走，
  必须触发"主力逻辑切换"警报，并在输出中标注 dominant_narrative 的 fragility 为"极高"。

### 2.2 原油-黄金相关性诊断（优先级最高）
- 步骤1：计算日内相关性方向与相对强弱（原油大跌 + 黄金涨/平/显著抗跌 = D状态）。
- 步骤2：D状态成因诊断（核心！）：
  - D1(供给驱动)：地缘缓和/增产导致油价跌。验证：铜价企稳或上涨，铜金比上升，VIX未飙升。
  - D2(衰退驱动)：需求崩塌导致油价跌。验证：铜价同步暴跌，铜金比骤降，金银比飙升，VIX异常走高。
- 步骤3：极端走势校验（价格与日内高低点的关系：贴低点=抛压未释放；大幅回收=买盘承接）。
- 步骤4：D状态下的美元与风险资产确认（需纳指/恒科/美债表现）。

### 2.3 D状态专属规则（强制遵守）
- ❌ 严禁基于"协议达成"逻辑推荐做空黄金。
- ❌ 严禁基于"油价暴跌"逻辑推荐抄底油气ETF。
- ✅ D1(供给驱动)下允许：科技ETF（成本下降）、黄金ETF（独立支撑）。
- ⚠️ 军工ETF与海外冲突联动弱（驱动力是国防政策/军贸订单），不得仅因海外地缘冲突推荐军工。
- ❌ D2(衰退驱动)下严禁：科技ETF、宽基ETF（盈利预期恶化）。
- ✅ D2(衰退驱动)下允许：国债ETF、黄金ETF、短融ETF。

### 2.4 事件簇分析
- 1个事件：禁止创建多情景，仅单线推演。
- 2-3个事件：最多2个情景，含冲突检测。
- 4个以上：最多3个情景，优先级排序。

## 第三部分：持仓映射规则
- 必须在以下持仓中选：
{holdings_text}
- 必须通过传导链检验：事件 → 宏观变量 → 行业/资产 → 对应ETF。

## 输入事件簇
{flash_text}

请严格按以下 JSON 格式输出：
{{
  "diagnostic_status": {{
    "data_quality": "{quality['data_quality']}",
    "missing_items": {json.dumps(quality['missing_items'], ensure_ascii=False)},
    "activated_steps": {json.dumps(quality['activated_steps'], ensure_ascii=False)},
    "aborted_steps": {json.dumps(quality['aborted_steps'], ensure_ascii=False)},
    "overall_confidence": "{quality['overall_confidence']}"
  }},
  "correlation_diagnosis": {{
    "oil_direction": "string",
    "gold_direction": "string",
    "correlation_state": "正相关/负相关/D状态/无法判断",
    "d_state_type": "D1供给驱动/D2衰退驱动/不适用/无法判断",
    "dollar_confirmation": "string",
    "risk_asset_confirmation": "string",
    "current_phase": "A/B/C/D/无法判断"
  }},
  "market_mood": "string",
  "uncertainty_level": "高/中/低",
  "dominant_narrative": {{"narrative": "string", "fragility": "string", "conflicting_signals": ["string"]}},
  "scenarios": [
    {{"scenario_name": "string", "generation_rule": "单事件推演/多事件推演",
      "probability_qualitative": "string", "assumptions": ["string"], "oil_path": "string",
      "affected_etfs": ["string"], "action_if_confirmed": "string", "trigger_to_watch": "string"}}
  ],
  "top_events": [
    {{"cluster_name": "string", "time_sensitivity_level": "紧急/中等/背景", "time_sensitive": "boolean",
      "value_score": "number (1-10)", "oil_impact": "string",
      "transmission_chain": "string (事件->宏观变量->行业逻辑->具体ETF)",
      "transmission_confidence": "强/中/弱", "action": "加仓/减仓/调仓/观望/埋伏/无法判断",
      "target": "string (从持仓列表选，无法映射填'无对应持仓')",
      "urgency": "即刻/本周/观察/中长期", "why": "string",
      "market_validation": "string", "risk": "string"}}
  ],
  "daily_strategy": {{
    "overall_position": "string", "max_position_confidence": "高/中/低/不可操作",
    "core_logic": "string", "pre_market_checklist": ["string"],
    "key_risks": ["string"], "do_not_touch": ["string"]
  }},
  "d_state_compliance": {{
    "is_d_state": "boolean", "d_state_type": "D1供给驱动/D2衰退驱动/不适用",
    "gold_short_recommended": "boolean", "oil_bottom_fishing_recommended": "boolean",
    "tech_recommended_in_d2": "boolean", "allowed_recommendations_used": ["string"],
    "compliance_note": "string (说明如何遵守2.3规则，特别是D1/D2的区分)"
  }}
}}"""


def analyze_with_llm(clusters: list, panel: dict, holdings: list, holdings_text: str) -> dict:
    """
    诊断流入口：评估数据质量 → 构建 prompt → JSON 模式调用。
    未配置 LLM / 调用失败 → 返回带 degraded 标记的占位结果。
    """
    quality = rules.evaluate_data_quality(panel, holdings)
    blocked = llm_blocked_reason()
    if blocked:
        return {"degraded": "no_llm", "market_mood": blocked,
                "top_events": [], "diagnostic_status": quality}
    prompt = build_diagnosis_prompt(clusters, panel, holdings, quality, holdings_text)
    result = _call_json(
        "你是冷酷的原油宏观交易员。当前一切以油价为核心。对无价值信息要毫不留情。必须输出合法JSON。",
        prompt, temperature=0.1)
    if not result:
        return {"degraded": "llm_failed", "market_mood": "未知",
                "top_events": [], "diagnostic_status": quality}
    result["_model"] = LLM_MODEL
    return result


def strip_d_state_violations(analysis: dict) -> list:
    """
    D 状态合规的代码层审查（LLM 自报违规 → 强制剔除）。
    只清洗 top_events（与原项目一致）。返回被剔除的描述列表。
    """
    removed = []
    comp = analysis.get("d_state_compliance") or {}
    events = analysis.get("top_events") or []

    def _strip(pred, label):
        nonlocal events
        bad = [e for e in events if pred(e)]
        if bad:
            removed.append(label)
            events = [e for e in events if not pred(e)]

    if comp.get("gold_short_recommended"):
        _strip(lambda e: "黄金ETF" in (e.get("target") or "") and e.get("action") == "减仓",
               "违规做空黄金建议已剔除")
    if comp.get("oil_bottom_fishing_recommended"):
        _strip(lambda e: "标普油气ETF" in (e.get("target") or "") and e.get("action") in ("加仓", "埋伏"),
               "违规抄底油气建议已剔除")
    analysis["top_events"] = events
    return removed


# ================================================================
#  五、复盘流（三段式）+ 结构化信号提取
# ================================================================

_PHASE_SKILLS = {
    "premarket": get_premarket_skill,
    "lunchbreak": get_midmarket_skill,
    "postmarket": get_postmarket_skill,
}
_PHASE_TITLES = {
    "premarket": "📅 A股盘前策略", "lunchbreak": "☀️ A股午盘策略", "postmarket": "📊 A股盘后复盘",
}


def format_user_holdings() -> str:
    """用户个股持仓（全部用户合并）+ 当日实时价格/涨跌幅 → 提示词文本。
    供盘前/午盘/盘后 LLM 做『持仓影响点评』；不参与 ETF 信号体系。
    附带涨跌幅让点评从“行业相关”升级到“已跌 x%，事件是否已被定价”。
    行情一次批量请求（腾讯单请求上限内）；失败降级为无行情的基础格式。
    """
    try:
        rows = db.fetch(
            "SELECT code, name, MIN(cost) AS cost FROM user_portfolio "
            "GROUP BY code, name ORDER BY name")
    except Exception as e:
        print(f"[llm] 读取用户持仓失败: {e}")
        return ""
    if not rows:
        return ""
    # 批量拉当日行情（非交易时段返回最近收盘价，涨跌幅为最近交易日全天涨跌）
    quotes = {}
    try:
        from app.tencent import get_stocks_batch
        for info in get_stocks_batch([r["code"] for r in rows[:20]]):
            quotes[info["code"]] = info
    except Exception as e:
        print(f"[llm] 持仓实时行情获取失败（降级为无行情输出）: {e}")
    lines = []
    for r in rows:
        s = f"- {r['name']}({r['code']})"
        if r.get("cost"):
            s += f" 成本{r['cost']}"
        q = quotes.get(r["code"])
        if q and q.get("price"):
            sign = "+" if q["change_pct"] >= 0 else ""
            s += f" | 现价 {q['price']} 涨跌 {sign}{q['change_pct']}%"
        lines.append(s)
    return "## 用户持仓\n" + "\n".join(lines) + "\n"


def build_review_prompt(phase: str, clusters: list, panel: dict, holdings: list,
                        holdings_text: str, core_etfs: list, hours: int) -> str:
    """复盘流 prompt（事件链 + 宏观锚定 + ETF + 内部状态 + 核心技能 + 阶段技能）。"""
    history = store.load_macro_history()
    macro_text = format_macro_snapshot(panel, history)
    cluster_text = format_cluster_text(clusters)
    etf_list = format_etf_list(holdings, core_etfs)
    etf_perf = format_etf_performance(holdings)
    etf_hist = format_etf_history(7)
    yesterday = store.load_etf_close()
    yest_text = ""
    if yesterday.get("holdings"):
        top = "，".join(f"{h['name']} {h['changeStr']}%" for h in yesterday["holdings"][:5])
        yest_text = f"\n昨日收盘ETF参考：{top}\n"
    role_desc = {
        "premarket": "A股即将开盘。以下是最近 24 小时已推送的事件链。",
        "lunchbreak": "A股上午收盘，下午即将开盘。以下是最近 6 小时的事件链。",
        "postmarket": "A股已收盘。以下是今日已推送的事件簇。",
    }[phase]
    perf_section = f"## ETF实际表现\n{etf_perf}\n\n" if phase != "premarket" else ""
    output_style = ("请用简练的Markdown输出，包含 emoji 增强可读性。" if phase != "postmarket"
                    else "请用简练的Markdown输出，必须体现复盘性质（逐条比对、验证逻辑），而非泛泛总结。")
    user_holdings = format_user_holdings()
    holdings_note = ""
    if user_holdings:
        holdings_note = ("\n【持仓影响点评要求】若事件链或宏观信息与持仓个股所属行业/题材明确相关，"
                         "请用『⚡ 对您的持仓影响』小节逐只点评（只点评有明确关联的，最多 3 只）；"
                         "点评时须结合持仓的当日涨跌幅判断事件影响是否已被定价（如利空但已先跌、利好但未涨），"
                         "盘前时段给出的是最近交易日涨跌，请注明；"
                         "无关联则写『今日事件与持仓无明显直接关联』。"
                         "严禁臆测个股业务与事件的关联，严禁对个股给出买卖指令，个股点评不得进入信号 JSON。\n")
    return f"""【角色定义】
你是宏观交易策略复盘与决策引擎。当前时间为北京时间，{role_desc}

## 事件链
{cluster_text}

## 当前全球宏观锚定物
{macro_text}
{yest_text}
{etf_hist}
{etf_list}

{perf_section}{_internal_context()}
{_calendar_context(3)}{user_holdings}{holdings_note}
{get_core_skill()}

{_PHASE_SKILLS[phase]()}

{output_style}"""


def run_review_llm(phase: str, clusters: list, panel: dict, holdings: list,
                   holdings_text: str, core_etfs: list, hours: int) -> tuple:
    """
    复盘流入口：(markdown, signals, model)。
    【修复原项目缺陷】信号不再用正则从 Markdown 抓数字，而是要求 LLM 在报告末尾
    输出 ```json 信号块，这里只做解析和字段校验。
    """
    blocked = llm_blocked_reason()
    if blocked:
        return (f"LLM 暂不可用：{blocked}。", [], "")
    prompt = build_review_prompt(phase, clusters, panel, holdings, holdings_text, core_etfs, hours)
    md = call_llm(
        "你是A股宏观策略复盘专家，输出简洁、专业的Markdown格式。必须基于给定数据推理，不编造信息。",
        prompt, temperature=0.3)
    if not md:
        return ("LLM 分析暂时不可用，请稍后重试。", [], LLM_MODEL)
    signals = extract_structured_signals(md)
    # 剥离末尾的 ```json 信号块（信号已单独解析入库，落盘/推送/前端展示不再暴露原始 JSON）
    clean_md = _JSON_BLOCK_RE.sub("", md).rstrip()
    return clean_md, signals, LLM_MODEL


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def extract_structured_signals(markdown: str) -> list:
    """
    从复盘 Markdown 中提取 ```json {"signals":[...]} 块并校验。
    校验失败/无块 → 空列表（绝不猜测）。信号只保留通过校验的字段。
    """
    blocks = _JSON_BLOCK_RE.findall(markdown)
    if not blocks:
        return []
    try:
        data = json.loads(blocks[-1])    # 取最后一个块（报告末尾的信号块）
    except json.JSONDecodeError:
        return []
    raw = data.get("signals")
    if not isinstance(raw, list):
        return []
    out = []
    for s in raw:
        try:
            etf = str(s.get("etfName", "")).strip()
            direction = s.get("direction")
            support = float(s.get("support"))
            resistance = float(s.get("resistance"))
            if not etf or direction not in ("long", "short"):
                continue
            if support <= 0 or resistance <= 0 or support >= resistance:
                continue
            out.append({
                "etfName": etf,
                "direction": direction,
                "support": support,
                "resistance": resistance,
                "reasoning": str(s.get("reasoning", ""))[:200],
            })
        except (TypeError, ValueError):
            continue
    return out[:5]   # 最多 5 个，防 LLM 超发
