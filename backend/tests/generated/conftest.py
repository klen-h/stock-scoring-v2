# -*- coding: utf-8 -*-
"""让 backend/tests/generated 下的用例能 `import app.*`（把 backend/ 加入 sys.path）。"""
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
