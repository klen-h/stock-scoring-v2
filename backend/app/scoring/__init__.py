"""
【文件作用】scoring 包的初始化文件。

把 ScoreEngine 暴露出来，这样外部可以用 from app.scoring import ScoreEngine
（而不是更长的 from app.scoring.engine import ScoreEngine）。

__all__ 声明这个包对外暴露的名称列表（控制 from app.scoring import * 的行为）。
"""
from .engine import ScoreEngine

__all__ = [
    "ScoreEngine"
]
