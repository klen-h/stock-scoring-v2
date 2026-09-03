"""
矛盾扫描引擎（Contradiction Scanner）

基于《Agent知识库_完整操作手册_v3.1》中的三层矛盾模型：
  L1 预期差（Expected vs Actual）
  L2 行为背离（Said vs Done / Narrative vs Flow）
  L3 信息断层（Public vs Private）

本模块先落地 L2 行为背离的纯规则扫描，作为 MVP。
"""

from .scanner import scan_all, L2_SCANNERS
from .store import save_contradictions, load_contradictions, save_report, load_report

__all__ = [
    "scan_all",
    "L2_SCANNERS",
    "save_contradictions",
    "load_contradictions",
    "save_report",
    "load_report",
]
