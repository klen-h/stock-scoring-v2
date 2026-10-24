# -*- coding: utf-8 -*-
"""让 backend/tests/generated 下的用例能 `import app.*`（把 backend/ 加入 sys.path）。

★ 安全（MEMORY §6 纪律）：generated 下全部是**单元测试**，绝不允许连到线上 Supabase——
  若本机导出过 DATABASE_URL（真库），在这里强制摘除，让 database.py 走 SQLite 回退。
  需要真库的集成测试请放别的目录并显式配置。
"""
import os
import sys

# ★★ 2026-10-24 二次加固：pop 不够——app/__init__ 随后从 backend/.env 把真库
# URL 重新灌回（实测本机因此把 pytest 跑到了线上 Supabase；万幸 fixture 自带
# teardown 未留残渣）。必须**强制设定** sqlite（app/__init__ 尊重已存在的 env）。
os.environ["DATABASE_URL"] = "sqlite:///(local-unit-tests)"

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
