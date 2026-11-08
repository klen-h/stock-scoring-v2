# -*- coding: utf-8 -*-
"""启动冒烟（静态层）：验证 `flash/scheduler.py` 里**所有延迟导入**确实可解析。

★★ 2026-11-08 生产事故后新增。
   事故：`d343cf54` 在 `scheduler.start()` 里写了
     `asyncio.create_task(etf_quote_loop())`
   却**没有导入** `etf_quote_loop` ⇒ uvicorn `NameError` ⇒ `Application startup failed`
   ⇒ 进程退出、8000 无监听、健康检查 40s 超时、部署失败。

为什么现有检查都抓不到：
  · `python -m compileall`  —— 只查**语法**。NameError 语法完全合法。
  · `import app.main`      —— 只执行**模块级**代码；`scheduler.start()` 是在
    FastAPI **lifespan/startup** 里才调用的，import 时根本不跑。
  · `pyflakes`             —— 能抓「名字未定义」，但**抓不到**
    「`from app.etf_board import etf_quote_loop` 写了，可模块里没这个函数」。

⇒ 本脚本补第三种：把 scheduler.py 里所有 `from app... import X` 抽出来，
  **真的 import 一次模块并 `hasattr` 检查名字**。纯静态收集 + 一次导入，
  不执行 lifespan、不连库、不外呼 ⇒ 适合放进 CI（秒级、无副作用）。

用法：
    python scripts/smoke_scheduler_imports.py
"""
import ast
import importlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

TARGET = os.path.join(BACKEND, "app", "flash", "scheduler.py")


def main() -> int:
    src = io.open(TARGET, encoding="utf-8").read()
    tree = ast.parse(src)

    # 收集：`from app... import ...`（含 as 别名）；只关心本项目内的模块
    specs = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not (node.module == "app" or node.module.startswith("app.")):
            continue
        # `from . import x` 相对导入（level>0）先跳过，本文件目前没有
        if node.level:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            specs.append((node.module, alias.name, node.lineno))

    checked_mod, bad = {}, []
    for mod_name, attr, lineno in specs:
        if mod_name not in checked_mod:
            try:
                checked_mod[mod_name] = importlib.import_module(mod_name)
            except Exception as e:
                checked_mod[mod_name] = None
                bad.append(f"line {lineno}: 模块 {mod_name} 导入失败: {str(e)[:80]}")
                continue
        mod = checked_mod[mod_name]
        if mod is None:
            continue
        if hasattr(mod, attr):
            continue
        # ★ `from app.flash import service` 这类是**导入子模块**：
        #   子模块没被加载前 `hasattr(package, 'service')` 是 False，但写法完全合法。
        #   ⇒ 再试探一次 `import_module("app.flash.service")`；能导入就算通过。
        #   这样才不会把全部 `from package import submodule` 误报成坏导入。
        try:
            importlib.import_module(mod_name + "." + attr)
        except ImportError:
            bad.append(f"line {lineno}: {mod_name} 里没有 `{attr}`（也不是子模块）")

    print(f"[smoke] scheduler.py 延迟导入：{len(specs)} 个名字 / "
          f"{len(checked_mod)} 个模块")
    if bad:
        print("❌ 以下延迟导入会在 startup 时抛 ImportError/NameError：")
        for x in bad:
            print("   -", x)
        return 1
    print("✅ scheduler 延迟导入全部可解析（不需要真跑 lifespan）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
