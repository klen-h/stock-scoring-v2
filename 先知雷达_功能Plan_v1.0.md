================================================================================
【功能Plan】先知雷达（Prophet Radar）—— 矛盾洞察与超前预判引擎
版本：v1.0
日期：2026-09-06
状态：待评审
================================================================================

一、功能定位
================================================================================

【一句话定义】
先知雷达是项目的"认知层"核心模块，通过L1/L2/L3三层矛盾扫描，在价格反应
之前识别市场的预期差、行为背离与信息断层，输出超前信号、状态判定与持仓
决策，将系统从"跟随市场"升级为"预判市场"

【与现有系统的关系】
  现有系统架构：
    数据层  ->  macro_panel / sector_flow
    评分层  ->  scoring/engine.py
    LLM层   ->  diagnosis / review
    执行层  ->  模拟盘 / 用户持仓
                  |
         【新增】先知雷达（认知层）
                  |
    矛盾扫描 -> 状态判定 -> LLM推理 -> 信号生成 -> 持仓顾问

【核心价值】
  1. 时间优势：L1领先1-3天，L2领先3-7天，L3领先7-30天
  2. 风险过滤：通过矛盾识别，过滤假突破、诱多、拥挤陷阱
  3. 决策闭环：从"数据->评分->信号->持仓->风控"全链路打通
  4. 认知沉淀：每次矛盾验证结果回写知识库，持续进化

================================================================================
二、架构设计
================================================================================

【模块拆分】

先知雷达 = 5大子模块 + 1个知识库

  矛盾扫描器(Scanner) -> 状态判定器(Regime) -> LLM推理层(Reasoner)
                                                  |
  知识库(Knowledge) <- 信号生成器(Signal) <- 持仓顾问(Advisor)

【数据流】

  开盘前(9:00)          盘中(9:30-15:00)          收盘后(15:30)
      |                       |                         |
      v                       v                         v
  矛盾扫描(L1/L2/L3)     矛盾监控(实时触发)         矛盾验证(昨日预判)
      |                       |                         |
      v                       v                         v
  状态判定               状态维持或切换              状态更新写入历史
      |                       |                         |
      v                       v                         v
  LLM推理(生成预案)      LLM即时分析                LLM复盘验证逻辑
      |                       |                         |
      v                       v                         v
  信号生成+持仓建议      信号刷新+风险警示           信号归档+知识沉淀

================================================================================
三、五大子模块详细设计
================================================================================

【3.1】矛盾扫描器（Contradiction Scanner）

职责：每日三次扫描（开盘前、午间、收盘后），识别L1/L2/L3矛盾

扫描任务调度：
  09:00  pre_market_scan()   -> 扫描隔夜事件、财经日历、外围市场
  12:00  mid_market_scan()   -> 扫描上午盘面异常、板块背离
  15:30  post_market_scan()  -> 扫描全天矛盾、验证昨日预判
  21:30  us_market_scan()    -> 扫描美股、美联储讲话、数据发布

【L1扫描逻辑】

输入：财经日历事件 + CME FedWatch + 个股财报
输出：L1矛盾列表（按surprise_score排序）

代码框架：
```python
class L1Scanner:
    def scan(self, events):
        contradictions = []
        for event in events:
            if not event.has_expectation:
                continue
            surprise = (event.actual - event.expected) / abs(event.expected) * 10
            if abs(surprise) > config.l1_surprise_threshold:
                contradictions.append(L1Contradiction(
                    event=event.name,
                    expected=event.expected,
                    actual=event.actual,
                    surprise_score=surprise,
                    correction_direction="up" if surprise > 0 else "down",
                    confidence="高" if abs(surprise) > 5 else "中",
                    time_window="1-3日",
                ))
        return sorted(contradictions, key=lambda x: abs(x.surprise_score), reverse=True)
```

【L2扫描逻辑】

六维背离检测：

| 维度 | 叙事层数据 | 行为层数据 | 背离判定 |
|------|-----------|-----------|----------|
| 散户 | sentiment_index | margin_balance_change | 情绪↑但融资↓ |
| 机构 | analyst_consensus | northbound_flow | 推荐买入但流出 |
| 政策 | policy_statement | actual_liquidity | 宽松表态但缩量 |
| 板块 | narrative_clusters | sector_flow | 利好叙事但资金流出 |
| 指数 | index_price | market_breadth | 指数红但宽度差 |
| 量价 | price_trend | volume_trend + MACD | 价升量减 |

代码框架：
```python
class L2Scanner:
    def scan(self):
        checks = [
            self._check_retail_divergence(),
            self._check_institutional_divergence(),
            self._check_policy_divergence(),
            self._check_sector_divergence(),
            self._check_index_divergence(),
            self._check_price_volume_divergence(),
        ]
        contradictions = [c for c in checks if c.is_divergence]
        for c in contradictions:
            c.persistence = self._check_persistence(c.dimension, days=2)
            if c.severity == "严重" and c.persistence >= 2:
                c.trigger_alert = True
        return contradictions
```

【L3扫描逻辑】

财报季/重大事件后触发：

```python
class L3Scanner:
    def scan_financial_reports(self, stocks):
        gaps = []
        for code in stocks:
            report = self._get_latest_report(code)
            ocf_ni_ratio = report.operating_cashflow / report.net_profit
            if ocf_ni_ratio < config.l3_ocf_ni_ratio_threshold:
                gaps.append(L3Gap(
                    dimension="财报断层",
                    public_info=f"净利润{report.net_profit}亿",
                    verified_info=f"OCF/NI={ocf_ni_ratio:.2f}",
                    gap_size="大" if ocf_ni_ratio < 0.3 else "中",
                ))
        return gaps
```

【3.2】状态判定器（Regime Detector）

职责：基于ADX/MA/宽度/外围指数，判定市场状态，支持hysteresis缓冲

```python
class RegimeDetector:
    def detect(self, panel, history):
        adx = panel.get("adx", 0)
        ma_trend = self._ma_trend(panel)
        breadth_ratio = self._breadth_ratio(panel)
        foreign_panic = self._foreign_panic_index(panel)

        if adx > config.adx_trend_threshold:
            regime = "offensive" if ma_trend == "up" else "defensive"
        else:
            if ma_trend == "down" and (breadth_ratio < config.breadth_panic or foreign_panic):
                regime = "neutral_bearish"
            else:
                regime = "neutral"

        regime = self._apply_hysteresis(regime, history)

        return MarketRegime(
            name=regime,
            weights=config.regime_weights[regime],
            adx=adx,
            breadth_ratio=breadth_ratio,
            foreign_panic=foreign_panic,
        )
```

【3.3】LLM推理层（Prophet Reasoner）

职责：将矛盾扫描结果输入LLM，生成超前预判和叙事分析

【双模型路由】

| 场景 | 模型 | 温度 | JSON模式 | 理由 |
|------|------|------|----------|------|
| 矛盾扫描结果解析 | Qwen3-32B | 0.1 | 是 | 快、准、JSON稳 |
| 盘前/盘后复盘 | DeepSeek-R1 | 0.3 | 否 | 推理深、可解释 |
| 紧急矛盾（L3） | DeepSeek-R1 | 0.2 | 否 | 需要深度因果链 |

【LLM输入模板核心规则】

你是"先知雷达"的核心推理引擎。你的任务不是解释已发生的价格变动，
而是在价格变动之前，通过识别矛盾来预判方向。

核心原则：
1. 矛盾越大，后续修正烈度越大
2. 矛盾越早识别，超额收益越丰厚
3. 三层矛盾可以联动升级（L1->L2->L3）
4. 行为层（资金实际动向）比叙事层（口头表态）更真实

输出要求：
- 必须基于输入数据推理，禁止编造
- 置信度<60%时标注"低置信度"，建议降级为"观望"
- 禁止在reasoning中使用"通常/历史上/一般"等模糊词
- 必须给出具体的传导链：矛盾->宏观变量->行业->资产->操作

【3.4】信号生成器（Signal Generator）

职责：将LLM推理结果转化为可执行的交易信号

```python
class SignalGenerator:
    def generate(self, contradictions, regime, llm_output):
        signals = []
        for c in contradictions:
            if c.layer == "L1" and c.confidence == "高":
                signals.append(self._from_l1(c, regime))
            elif c.layer == "L2" and c.severity == "严重":
                signals.append(self._from_l2(c, regime))
            elif c.layer == "L3" and c.gap_size == "大":
                signals.append(self._from_l3(c, regime))

        signals.extend(self._from_llm(llm_output))
        signals = [s for s in signals if self._validate(s, regime)]
        return signals[:5]

    def _validate(self, signal, regime):
        # 震荡偏空禁止长期做多
        if regime.name == "neutral_bearish" and signal.direction == "long":
            if signal.timeframe not in ["日内", "波段(3-5日)"]:
                return False
        # 拥挤度过滤
        if signal.crowding_score < 30:
            signal.confidence = "低"
            signal.position_sizing = "轻仓试探"
        # D状态合规
        if signal.d_state_compliance_violation:
            return False
        return True
```

【信号Schema】

```json
{
  "signals": [
    {
      "etfName": "黄金ETF",
      "direction": "long",
      "timeframe": "波段(3-5日)",
      "confidence": "中",
      "entry_condition": "回调至4450附近且未跌破4400",
      "invalidation": "跌破4400或9月CPI超预期反弹",
      "support": 4400,
      "resistance": 4600,
      "position_sizing": "轻仓试探",
      "reasoning": "央行购金结构性支撑+加息概率已部分定价",
      "contradiction_source": "L1_预期差_沃勒鸽派",
      "regime_alignment": "neutral_bearish_允许",
      "crowding_score": 45
    }
  ]
}
```

【3.5】持仓顾问（Portfolio Advisor）

职责：基于矛盾洞察和信号，对用户持仓给出具体操作建议

```python
class PortfolioAdvisor:
    def advise(self, portfolio, contradictions, signals):
        advices = []
        for holding in portfolio:
            advice = HoldingAdvice(
                code=holding.code,
                name=holding.name,
                cost=holding.cost,
                current_price=holding.current_price,
                pnl_pct=holding.pnl_pct
            )

            # 矛盾影响评估
            relevant = self._find_relevant(holding, contradictions)
            if relevant:
                advice.contradiction_impact = self._assess_impact(holding, relevant)

            # 定价程度判断
            advice.pricing_degree = self._judge_pricing(holding, signals)

            # 具体操作建议
            advice.action = self._recommend_action(holding, advice, regime)
            advice.stop_loss = self._calculate_stop_loss(holding)
            advice.take_profit = self._calculate_take_profit(holding)

            advices.append(advice)
        return advices

    def _recommend_action(self, holding, advice, regime):
        # 磨底期纪律
        if regime.name in ["neutral_bearish", "defensive"]:
            if holding.pnl_pct < -5:
                return "持有观望，不补仓"
            elif holding.pnl_pct > 3:
                return "利用反弹减仓"

        # 拥挤度纪律
        if advice.crowding_score < 30 and holding.pnl_pct > 0:
            return "拥挤度高，止盈减仓"

        # 矛盾驱动
        if advice.contradiction_impact == "利空" and holding.pnl_pct > 0:
            return "矛盾利空，减仓避险"

        return "持有观察"
```

持仓建议输出格式示例：

```
⚡ 对您的持仓影响

【000567 海德股份】成本6.976，现价6.76，浮亏-3.09%
- 相关矛盾：L1_非农大超预期(+16.2万)->紧缩预期强化
- 定价程度：已部分定价（昨日评分Top1但已跌3%）
- 建议：持有观望。非银金融有防御属性，但大盘系统性风险未解除
- 止损位：6.30（-10%）

【000617 中油资本】成本7.505，现价7.19，浮亏-4.20%
- 相关矛盾：L2_油价涨但油气板块资金流出（拉高出货）
- 定价程度：未完全定价（反身性校验失败）
- 建议：若反弹至7.30+，减仓避险
- 止损位：6.80（-10%）
```

================================================================================
四、知识库设计（Knowledge Base）
================================================================================

职责：沉淀每次矛盾的识别、验证、修正结果，持续进化系统认知

数据模型：

```python
@dataclass
class ContradictionRecord:
    id: str
    timestamp: datetime
    layer: str  # L1/L2/L3
    description: str
    identified_by: str

    # 预判
    predicted_direction: str
    predicted_magnitude: str
    predicted_time_window: str
    confidence: str

    # 验证
    validated: bool = False
    validation_timestamp: Optional[datetime] = None
    actual_direction: Optional[str] = None
    actual_magnitude: Optional[str] = None
    validation_result: Optional[str] = None  # 成功/失败/部分成功
    failure_reason: Optional[str] = None
    model_adjustment: Optional[str] = None
```

学习闭环：
  识别矛盾 -> 生成预判 -> 市场验证 -> 记录结果 -> 模型调优
      ^_______________________________________________|

================================================================================
五、与现有系统的集成接口
================================================================================

【5.1】与评分系统的集成

```python
# scoring/engine.py 中新增
from prophet import ProphetRadar

class ScoringEngine:
    def __init__(self):
        self.prophet = ProphetRadar()

    def score(self, stock):
        base_score = self._calculate_base_score(stock)

        # 先知雷达增强
        contradiction_penalty = self.prophet.get_contradiction_penalty(stock)
        crowding_penalty = self.prophet.get_crowding_penalty(stock)
        regime_multiplier = self.prophet.get_regime_multiplier(stock)

        final_score = (base_score + contradiction_penalty + crowding_penalty) * regime_multiplier

        return Score(
            value=final_score,
            regime=self.prophet.current_regime.name,
            contradictions=self.prophet.active_contradictions,
        )
```

【5.2】与LLM诊断流的集成

在 llm/prompt.py 中新增"先知雷达技能"：

步骤1：L1扫描（预期差）
  - 检查今日是否有重要数据发布
  - 对比"市场预期"与"实际值"
  - 若|surprise_score|>3，标记为高预期差事件

步骤2：L2扫描（行为背离）
  - 检查六维背离：散户/机构/政策/板块/指数/量价
  - 若发现严重背离且持续2日，触发"主力逻辑切换"警报

步骤3：L3扫描（信息断层）
  - 财报季检查：经营现金流/净利润比率
  - 地缘事件检查：官方声明 vs 物理数据

步骤4：矛盾联动分析
  - L1+L2同时出现 = 高概率修正
  - L1+L2+L3同时出现 = 极高概率剧烈修正

步骤5：生成超前预判
  - 基于矛盾，预判未来1-3天的价格方向
  - 给出具体的传导链
  - 标注置信度和失效条件

【5.3】与复盘流的集成

```python
# review.py 中新增矛盾验证

def run_review_llm(...):
    # 原有复盘逻辑...

    # 新增：矛盾验证
    prophet = ProphetRadar()
    yesterday_contradictions = prophet.get_yesterday_contradictions()
    validation_results = prophet.validate(yesterday_contradictions, today_panel)

    # 将验证结果注入复盘prompt
    review_prompt += format_validation_results(validation_results)
    review_prompt += format_new_contradictions(prophet.scan())

    # 继续原有复盘逻辑...
```

================================================================================
六、实施路线图
================================================================================

【Phase 1：基础框架（2周）】

Week 1:
  [ ] 搭建矛盾扫描器基础框架（L1/L2/L3类定义）
  [ ] 实现L1扫描（财经日历+CME FedWatch）
  [ ] 实现状态判定器（neutral_bearish判定逻辑）
  [ ] 集成到现有评分系统（环境变量切换）

Week 2:
  [ ] 实现L2扫描（六维背离检测）
  [ ] 实现信号生成器（基础Schema）
  [ ] 实现持仓顾问（基础建议逻辑）
  [ ] 编写LLM Prompt（先知雷达技能）
  [ ] 单元测试（用9/1-9/5数据验证）

【Phase 2：LLM集成（2周）】

Week 3:
  [ ] 实现双模型路由（诊断Qwen3-32B / 复盘DeepSeek-R1）
  [ ] 集成矛盾扫描结果到诊断流Prompt
  [ ] 集成矛盾验证到复盘流Prompt
  [ ] 实现知识库（SQLite/JSON存储）

Week 4:
  [ ] 实现L3扫描（财报质量检测）
  [ ] 实现拥挤度因子（近20日涨幅计算）
  [ ] 实现行业内分位数打分（映射表集成）
  [ ] 端到端测试（完整交易日模拟）

【Phase 3：优化迭代（2周）】

Week 5:
  [ ] 回测验证（用8/20-9/5数据验证矛盾识别准确率）
  [ ] 调优L1/L2/L3阈值（基于回测结果）
  [ ] 优化LLM Prompt（减少幻觉、提高JSON稳定性）
  [ ] 用户测试（内部试用）

Week 6:
  [ ] 修复Bug
  [ ] 性能优化（扫描速度<1秒）
  [ ] 文档完善
  [ ] 正式上线

================================================================================
七、预期效果与KPI
================================================================================

【量化指标】

| 指标 | 当前基线 | 目标 | 验证方式 |
|------|----------|------|----------|
| 矛盾识别准确率 | — | L1>70%, L2>60%, L3>50% | 回测验证 |
| 信号胜率 | 62.5%（震荡档） | +10pp | 固定持有期回测 |
| 信号盈亏比 | — | >1.5 | 模拟盘统计 |
| 回撤控制 | — | 最大回撤<15% | 模拟盘统计 |
| 响应速度 | — | 扫描<1秒，LLM<10秒 | 性能测试 |

【定性效果】

1. 从"跟随市场"到"预判市场"：在非农数据、沃什讲话等事件前，提前1-3天识别矛盾
2. 从"解释已发生"到"洞察将发生"：日报不再是事后总结，而是超前预案
3. 从"单一维度"到"三层验证"：通过L1+L2+L3联动，提高决策置信度
4. 从"静态权重"到"动态适应"：根据矛盾类型和市场状态，自动调整评分权重

================================================================================
八、风险与对策
================================================================================

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| LLM幻觉导致错误信号 | 中 | 高 | 数据真实性铁律+JSON校验+置信度过滤 |
| 矛盾识别过度敏感（噪音） | 中 | 中 | 阈值调优+hysteresis缓冲+人工审核初期 |
| 计算资源不足（双模型） | 低 | 中 | Qwen3-32B成本低，R1仅用于复盘 |
| 数据延迟（regime判定滞后） | 中 | 高 | 环境变量覆盖+实时回填机制 |
| 用户不理解矛盾理论 | 低 | 低 | 日报增加"矛盾解读"小节，渐进式教育 |

================================================================================
九、命名与品牌
================================================================================

【功能名称】先知雷达（Prophet Radar）

【命名理由】
- "先知"：体现"超前预判"的核心价值
- "雷达"：体现"扫描-识别-预警"的工作方式
- 英文Prophet：取"预言者"之意

【内部代号】CPE（Contradiction Prophet Engine）

【用户可见名称】
- 日报中："📡 先知雷达扫描"
- 前端："先知雷达 | 今日识别X个矛盾"
- 信号标签："[先知] 基于L2行为背离生成"

================================================================================
【文档结束】
================================================================================
