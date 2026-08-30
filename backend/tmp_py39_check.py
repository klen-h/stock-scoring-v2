# -*- coding: utf-8 -*-
"""扫描全项目：找出 Python 3.9 会报错的 PEP 604 类型语法（X | Y）
排除已加 from __future__ import annotations 的文件（那里注解不求值）。"""
import ast, os, sys

root = os.path.dirname(os.path.abspath(__file__))
bad = []

def has_future_annotations(tree):
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(a.name == "annotations" for a in node.names):
                return True
    return False

def is_bitor(node):
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)

def scan_file(path):
    try:
        src = open(path, "r", encoding="utf-8").read()
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        bad.append((path, 0, f"SyntaxError: {e}"))
        return
    future = has_future_annotations(tree)
    for node in ast.walk(tree):
        # 函数返回值注解
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns and is_bitor(node.returns) and not future:
                bad.append((path, node.lineno, f"返回值注解 -> {ast.unparse(node.returns)}"))
            # 参数注解 + 默认值（如 dict | None = None）
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                if a.annotation and is_bitor(a.annotation) and not future:
                    bad.append((path, node.lineno, f"参数 {a.arg}: {ast.unparse(a.annotation)}"))
        # 变量注解
        if isinstance(node, ast.AnnAssign) and node.annotation and is_bitor(node.annotation) and not future:
            bad.append((path, node.lineno, f"变量注解: {ast.unparse(node.annotation)}"))

count = 0
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in
                   {"__pycache__", "node_modules", ".git", "venv", ".venv", "data", "backtest_reports"}]
    for f in filenames:
        if f.endswith(".py"):
            count += 1
            scan_file(os.path.join(dirpath, f))

print(f"已扫描 {count} 个 Python 文件\n")
if bad:
    print(f"发现 {len(bad)} 处 Python 3.9 不兼容的写法：")
    for path, line, desc in bad:
        print(f"  {os.path.relpath(path, root)}:{line}  {desc}")
    sys.exit(1)
else:
    print("全部兼容 Python 3.9（无 PEP 604 类型语法残留）")
