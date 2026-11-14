# -*- coding: utf-8 -*-
"""按日三时段复盘聚合（`coach.audit.daily_review_summary` / `_row_verdict`）。

背景（2026-11-14，用户："对盘前盘中盘后进行复盘回测，验证准确性及优化；
我主要在企微看，需要将重要内容简要常驻到工作台"）：

  `rule_stats` 只按**规则**聚合、`execution_consistency` 只算执行率，
  都回答不了"**某一天**系统说了什么、哪些说对了"——而这正是复盘的天然单位。
  本模块把"教练卡 + 重要推送"按 盘前/盘中/盘后 三时段对账，
  每条教练建议带事后方向判定（与 `rule_stats` 同口径）。

  ⚠️ 教练纪律卡走分类群通道，**不进 push_log** ⇒ 必须直接查 `coach_alerts`。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.coach import audit


# ── `_row_verdict`：方向判定与三级取值 ────────────────────────────────

def test_row_verdict_directions():
    """expect=down 的规则：事后跌=命中、涨=打脸；up 反之。"""
    assert audit._row_verdict('gate_no_add', None, -1.2, None)[0] == 'hit'
    assert audit._row_verdict('gate_no_add', None, 1.2, None)[0] == 'miss'
    assert audit._row_verdict('gate_add', None, 2.0, None)[0] == 'hit'
    assert audit._row_verdict('gate_add', None, -2.0, None)[0] == 'miss'


def test_row_verdict_priority_stock_over_bench():
    """个股级（有 outcome_pct）优先于基准 —— 与 rule_stats 同口径。"""
    # gate_no_add 期待跌：个股 +3（涨）⇒ miss，即使基准是跌的
    v, basis, val = audit._row_verdict('gate_no_add', 3.0, -1.0, None)
    assert v == 'miss' and basis == 'stock' and val == 3.0


def test_row_verdict_spread_rule_uses_spread_column():
    """`gate_add_weak_direction` 必须走分化列（基准涨跌答非所问）。"""
    v, basis, val = audit._row_verdict('gate_add_weak_direction', None, 5.0, -0.5)
    assert v == 'hit' and basis == 'spread' and val == -0.5


def test_row_verdict_not_evaluable_and_pending():
    """`emotion_fuse` 无方向语义 ⇒ 不评价；outcome 全空 ⇒ 待验证（不是"不评价"）。"""
    assert audit._row_verdict('emotion_fuse', None, None, None)[0] == 'not_evaluable'
    assert audit._row_verdict('gate_no_add', None, None, None)[0] == 'pending'
    # 未知规则（不在 _RULE_EXPECT）也不评价
    assert audit._row_verdict('unknown_rule', 1.0, 1.0, 1.0)[0] == 'not_evaluable'


# ── `_segment_of`：三时段切点 ─────────────────────────────────────────

def test_segment_boundaries():
    """盘前 <09:30 ／ 盘中 09:30~15:05 ／ 盘后 >15:05。"""
    assert audit._segment_of('09:15') == 'premarket'
    assert audit._segment_of('08:55') == 'premarket'
    assert audit._segment_of('09:30') == 'intraday'
    assert audit._segment_of('15:05') == 'intraday'     # 缓冲：收盘后几分钟仍算盘中
    assert audit._segment_of('15:06') == 'postmarket'
    assert audit._segment_of('19:30') == 'postmarket'


# ── `daily_review_summary`：mock 集成 ─────────────────────────────────

def _alert(rule_id, time='09:15:00', bench=None, stock=None, spread=None,
           pushed=True, msg='测试消息'):
    return {'rule_id': rule_id, 'label': rule_id, 'severity': 'info', 'code': '',
            'message': msg, 'alert_time': '2026-11-13T' + time,
            'pushed': pushed, 'executed': '',
            'outcome_pct': stock, 'outcome_bench_pct': bench,
            'outcome_spread_pp': spread}


def _push(time, title, category='brief'):
    return {'ts': '2026-11-13 ' + time, 'title': title, 'category': category}


def test_summary_segments_noise_and_counts(monkeypatch):
    """时段分组 + 运维噪音过滤 + 汇总计数 + 趋势。"""
    alerts = [
        _alert('gate_no_add', '09:15:00', bench=-1.0),          # 盘中? 不 —— 09:15 盘前；事后跌 ⇒ hit
        _alert('gate_add', '09:15:30', bench=2.0),              # 盘前；涨 ⇒ hit
        _alert('gate_reduce', '13:07:00', bench=1.0),           # 盘中；expect=down 涨 ⇒ miss
        _alert('emotion_fuse', '14:45:00'),                     # 不评价
        _alert('gate_no_add', '09:15:00'),                      # 全空 ⇒ pending
    ]
    pushes = [
        _push('09:11', '🧭 交易员决策简报（盘前）'),
        _push('12:00', '⚠️ 数据任务失败告警'),                   # 运维噪音 ⇒ 必须被滤
        _push('18:35', '🧭 行业主线日报'),
    ]
    trend_rows = [
        {'alert_date': '2026-11-12', 'rule_id': 'gate_no_add',
         'outcome_pct': None, 'outcome_bench_pct': -2.0, 'outcome_spread_pp': None},
        {'alert_date': '2026-11-12', 'rule_id': 'emotion_fuse',
         'outcome_pct': None, 'outcome_bench_pct': None, 'outcome_spread_pp': None},
    ]

    def fake_fetch(sql, params=None):
        s = sql
        if 'FROM coach_alerts WHERE alert_date=%s' in s:
            return alerts
        if 'FROM push_log' in s:
            return pushes
        if 'alert_date >= %s' in s:
            return trend_rows
        raise AssertionError('意外 SQL: ' + s[:80])

    monkeypatch.setattr(audit.db, 'fetch', fake_fetch)
    rep = audit.daily_review_summary(date='2026-11-13', days=10)

    s = rep['summary']
    assert s['total'] == 5
    assert s['evaluable'] == 3            # 2 hit + 1 miss
    assert s['hit'] == 2 and s['miss'] == 1
    assert s['pending'] == 1 and s['not_evaluable'] == 1
    assert s['hit_rate'] == round(2 / 3 * 100, 1)

    seg = rep['segments']
    assert len(seg['premarket']) == 4      # 3 教练（2 判定 + 1 pending）+ 1 push（简报）
    assert len(seg['intraday']) == 2       # 2 教练（13:07 + 14:45 体检卡）；噪音 push 已滤
    assert len(seg['postmarket']) == 1     # 1 push（主线日报）
    # 时段排序：同段内按时间
    assert [x['time'] for x in seg['premarket']] == ['09:11', '09:15', '09:15', '09:15']

    # 趋势：11-12 只有 gate_no_add 可评价且命中
    assert rep['trend'][-1] == {'date': '2026-11-12', 'total': 2,
                                'evaluable': 1, 'hit': 1, 'hit_rate': 100.0}


def test_noise_filter_is_not_over_broad(monkeypatch):
    """黑名单不能误伤业务标题 —— "先知雷达·盘中风险警示"必须保留。

    教训：黑名单若含"告警/体检"这类宽词，会把风险警示和教练体检卡滤掉，
    复盘对账看起来"什么都没发生"，比缺失更危险。
    """
    pushes = [_push('13:19', '先知雷达·盘中风险警示'),
              _push('10:30', '⚠️ 因子体检告警')]              # 运维 ⇒ 滤
    monkeypatch.setattr(audit.db, 'fetch',
                        lambda sql, params=None: [] if 'coach_alerts' in sql else pushes)
    rep = audit.daily_review_summary(date='2026-11-13', days=5)
    titles = [x['title'] for x in rep['segments']['intraday']]
    assert '先知雷达·盘中风险警示' in titles
    assert '⚠️ 因子体检告警' not in titles
