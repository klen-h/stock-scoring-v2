# scoring/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】scoring 包的初始化文件。

把 ScoreEngine 暴露出来，这样外部可以用 from app.scoring import ScoreEngine
（而不是更长的 from app.scoring.engine import ScoreEngine）。

__all__ 声明这个包对外暴露的名称列表（控制 from app.scoring import * 的行为）。

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.backtest.market_regime`
- `app.database`
- `app.finance`
- `app.flash`
- `app.flash.rules`
- `app.mainforce.state`
- `app.routers.scoring`
- `app.scoring.engine`
- `app.scoring.indicator_cache`
- `app.scoring.kline_cache`
- `app.tencent`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/mainforce/gate.py`
- `backend/app/mainforce/lhb.py`
- `backend/app/mainforce/state.py`
- `backend/app/mainline.py`
- `backend/app/routers/scoring.py`
- `backend/app/sector_strength.py`
- `backend/app/strategies/base.py`
- `backend/app/strategies/paper_trading.py`
- `backend/app/tencent.py`
- `backend/test_engine.py`
- `backend/tests/generated/test_scoring_decay.py`
- `backend/tmp_archive/tmp_align_test.py`
- `backend/tmp_archive/tmp_batch_test.py`
- `backend/tmp_archive/tmp_diag3.py`

## 关键阈值 / 环境变量

- `NB_TURNOVER_CAP`、`NB_TURNOVER_CAP_PCT`、`NB_TURNOVER_CAP_SCORE`

## 顶层结构（def/class，前 25 个）

`resolve_weights`, `dim_weight_mult`, `decay_total`, `_ind_dist_fingerprint`, `_ind_dist_src_load`, `_ind_dist_src_save`, `_fin_sub_industry`, `DimensionScore`, `ScoreResult`, `_clamp`, `_round1`, `_score_in_range`, `ScoreEngine`, `_score_flow5`, `init_indicator_cache_table`, `ensure_indicator_cache_table`, `get_cached_indicators`, `get_cached_technical`, `get_cached_technical_batch`, `get_cached_technical_batch_sql`, `get_cached_indicators_batch`, `save_indicator_cache`, `compute_latest_indicators`, `refresh_indicator_cache`, `get_indicator_cache_status`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：4678 行。
