# -*- coding: utf-8 -*-
"""`/api/score/industry-dist` 的回归测试。

2026-11-16 事故：评分榜**后端算的和前端本地算的差很多**。
实测（同一份输入）：
    工商银行  资产负债率：后端 75.0  vs  前端 26.95
    三一重工  毛利率：    后端 52.3  vs  前端 64.79
其余 all 维度完全一致 ⇒ 差异**全部**来自后端的两条前端没有的逻辑：
  ① PE/PB/毛利率/负债率 **行业内分位优先**
  ② 金融行业特判（银行负债率固定 75）

修法是把**分布本身**下发给前端（阈值抄两份必然再漂）。
本文件锁住这个接口的结构 —— 前端靠它复现 bisect 分位。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.routers import scoring as scoring_router
from app.scoring.engine import ScoreEngine


def _fake_dist():
    return {
        "dist": {
            "pe": {"机械行业": [8.0, 10.0, 12.0]},
            "pb": {},
            "debt_ratio": {"金融·银行": [90.0, 92.0, 94.0],
                           "机械行业": [40.0, 52.0, 60.0]},
            "gross_margin": {"机械行业": [20.0, 27.0, 35.0]},
        },
        "code_ind": {"601398": "金融行业", "600031": "机械行业"},
        "code_sub": {"601398": "金融·银行"},
    }


def test_industry_dist_shape(monkeypatch):
    """返回结构必须齐全：前端要靠 dist / code_ind / code_sub 三件套。"""
    monkeypatch.setattr(ScoreEngine, "_industry_dist",
                        classmethod(lambda cls: _fake_dist()))
    r = scoring_router.score_industry_dist()
    assert set(r) >= {"dist", "code_ind", "code_sub", "metrics", "buckets"}
    assert r["code_ind"]["601398"] == "金融行业"
    assert r["code_sub"]["601398"] == "金融·银行"
    # 分布值必须保留升序（前端 bisect 依赖有序，否则分位算错）
    assert r["dist"]["debt_ratio"]["机械行业"] == [40.0, 52.0, 60.0]


def test_industry_dist_empty_is_safe(monkeypatch):
    """分布为空（服务刚重启、实时行情未刷新）⇒ 不能抛异常。

    此时后端自己也降级到绝对曲线，前端也降级 ⇒ 两边仍一致。
    """
    monkeypatch.setattr(ScoreEngine, "_industry_dist",
                        classmethod(lambda cls: {}))
    r = scoring_router.score_industry_dist()
    assert r["dist"] == {} or all(not v for v in r["dist"].values())
    assert r["code_ind"] == {}


def test_industry_dist_rounding(monkeypatch):
    """数值保留 3 位小数（体积控制），但不能改变分位顺序。"""
    monkeypatch.setattr(ScoreEngine, "_industry_dist",
                        classmethod(lambda cls: {
                            "dist": {"pe": {}, "pb": {}, "debt_ratio":
                                     {"X": [1.0004, 1.0006, 2.0]},
                                     "gross_margin": {}},
                            "code_ind": {}, "code_sub": {}}))
    r = scoring_router.score_industry_dist()
    vals = r["dist"]["debt_ratio"]["X"]
    assert vals == [1.0, 1.001, 2.0]
    assert vals == sorted(vals), "取整后必须仍是有序的"
