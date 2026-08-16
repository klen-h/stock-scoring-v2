"""
================================================================================
【文件作用】评分引擎的单元测试脚本（手动验证评分逻辑是否合理）
================================================================================

运行方式：python test_engine.py

测试思路：
  构造两组"假数据"，一组模拟上涨趋势股，一组模拟下跌趋势股，
  分别喂给评分引擎，验证"上涨股的分数应该 > 下跌股的分数"。

  这是一个"冒烟测试"——不依赖网络/数据库，纯粹验证评分算法本身。
================================================================================
"""

import sys
# 把 backend 目录加到 Python 搜索路径，这样能 import app.xxx
# 注意：第 2 行的路径是写死的（/home/z/...），换环境要改，不算最佳实践
sys.path.insert(0, '/home/z/my-project/stock-scoring/backend')
from app.scoring.engine import ScoreEngine
from app.routers.scoring import _calc_technical

# 创建引擎实例
e = ScoreEngine()

# ──────────────────────────────────────────────
# 测试 1：上涨趋势股（应该得高分）
# ──────────────────────────────────────────────
# 构造 100 天的 K线，价格从 10 元每天涨 0.12 元（明显上涨趋势）
tech = []
for i in range(100):
    tech.append({
        'close': float(10 + i * 0.12),       # 收盘价递增
        'open': float(10 + i * 0.12 - 0.05),
        'high': float(10 + i * 0.12 + 0.2),
        'low': float(10 + i * 0.12 - 0.1),
        'volume': float(1000000 + i * 20000),  # 成交量递增（放量上涨）
        'date': 'd' + str(i),
    })
# 用 _calc_technical 算出技术指标
t = _calc_technical(tech)
# 构造实时行情（PE 合理、换手温和、涨幅适中）
info = {'pe': 15, 'pb': 1.2, 'market_cap': 500000, 'float_cap': 400000,
        'turnover_rate': 3.0, 'amplitude': 3.5, 'change_pct': 2.0, 'amount': 50000000}
r1 = e.score_stock('000001', 'UP', t, info, {})
print('UP: score=' + str(r1.total_score) + ' signal=' + r1.signal)

# ──────────────────────────────────────────────
# 测试 2：下跌趋势股（应该得低分）
# ──────────────────────────────────────────────
# 构造 100 天 K线，价格从 20 元每天跌 0.1 元（明显下跌趋势）
tech2 = []
p = 20.0
for i in range(100):
    p -= 0.1
    tech2.append({
        'close': float(p),                    # 收盘价递减
        'open': float(p + 0.05),
        'high': float(p + 0.1),
        'low': float(p - 0.15),
        'volume': float(2000000 - i * 15000),  # 成交量递减（缩量下跌）
        'date': 'd' + str(i),
    })
t2 = _calc_technical(tech2)
# 实时行情（PE 高估、换手偏高、跌幅）
info2 = {'pe': 90, 'pb': 7, 'market_cap': 5000000, 'float_cap': 4000000,
         'turnover_rate': 12, 'amplitude': 8, 'change_pct': -4.0, 'amount': 20000000}
r2 = e.score_stock('600000', 'DN', t2, info2, {})
print('DN: score=' + str(r2.total_score) + ' signal=' + r2.signal)

# 断言：上涨股分数必须 > 下跌股分数，否则说明评分逻辑有问题
assert r1.total_score > r2.total_score, f"UP({r1.total_score}) should > DN({r2.total_score})"
print('ASSERTION PASSED')
