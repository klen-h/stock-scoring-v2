#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】前端 api 按域拆分（http.js → modules/*.js）的**导出/引用对账**
================================================================================
【为什么必须有】（2026-11-14，连续 5 批部署后运行时 ReferenceError）

  按域拆分时，漏一个 import 的后果是**构建照样通过**：
    · ESM 的**自由变量**，Rollup/Vite 不做静态未定义检查；
    · 只有请求真正走到那一行（如 axios 请求拦截器）才抛
      `ReferenceError: xxx is not defined`。
  实测当天连续出错：`LOCAL_CACHE` → `_cacheGet/_cacheSet/CACHE_PREFIX`
  → `useWorkbenchBrief` → ... → `_cacheKey`（`loadSizing` 触发的第一个 GET）。

  同样隐蔽的两类问题：
    · **import 了 http.js 没有导出的名字** ⇒ 拿到 `undefined`，不抛错但逻辑全废；
    · 多个 modules 导出**同名**符号 + `index.js` 用 `export *`
      ⇒ ESM 变成 ambiguous，import 时静默拿到 `undefined`（不报错）。

【检查项】
  1. `modules/*.js` 里用到 `http.js` 导出符号但**没 import**；
  2. `modules/*.js` 里 `import { ... } from '../http'` 的名字**确实存在**于 http.js；
  3. modules 之间导出名**重名**（`export *` ambiguous）；
  4. `index.js` 是否 re-export 了全部 modules。

【用法】
    python scripts/check_frontend_exports.py        # 全绿返回 0，有问题返回 1
  ⚠️ 纯静态、零依赖、不联网 ⇒ 可挂到 CI / 提交前钩子。
================================================================================
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "frontend", "src", "api")
HTTP = os.path.join(ROOT, "http.js")
INDEX = os.path.join(ROOT, "index.js")
MODULES = os.path.join(ROOT, "modules")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _strip_comments(text):
    """剔除整行注释（避免把说明里的标识符当成用法）。

    ⚠️ 只处理以 // * /* 开头的**整行**注释，不处理行尾注释 ——
      行尾注释里出现被查符号的概率极低，且误判成"使用了"最多是多报一次，
      比"漏报"安全。
    """
    return "\n".join(ln for ln in text.split("\n")
                     if not ln.strip().startswith(("//", "*", "/*")))


def http_exports(src):
    names = set(re.findall(r"export\s+(?:async\s+)?function\s+(\w+)", src))
    names |= set(re.findall(r"export\s+(?:const|let|var)\s+(\w+)", src))
    for m in re.finditer(r"export\s*\{([^}]+)\}", src):
        for part in m.group(1).split(","):
            name = part.strip().split(" as ")[-1].strip()
            if name:
                names.add(name)
    return names


def main():
    problems = []
    hsrc = _read(HTTP)
    exports = http_exports(hsrc)
    print("http.js 导出（%d）: %s" % (len(exports), ", ".join(sorted(exports))))
    print()

    mod_files = sorted(f for f in os.listdir(MODULES) if f.endswith(".js"))
    # {name: [module...]} 用于查重名
    exported_by = {}

    for fn in mod_files:
        text = _read(os.path.join(MODULES, fn))
        code = _strip_comments(text)

        imported = set()
        for m in re.finditer(
                r"import\s*\{([^}]+)\}\s*from\s*['\"]\.\./http['\"]", text):
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                if name:
                    imported.add(name)

        declared = set(re.findall(
            r"^(?:export\s+)?(?:const|let|var|function|class)\s+(\w+)",
            text, re.M))

        # ① 用了但没 import
        for name in sorted(exports):
            if name in imported or name in declared:
                continue
            if re.search(r"(?<![\w.])" + re.escape(name) + r"\b", code):
                problems.append("%s: 使用 `%s` 但未从 ../http import"
                                % (fn, name))

        # ② import 了不存在的名字
        for name in sorted(imported - exports):
            problems.append("%s: 从 ../http import 的 `%s` 并不存在（会拿到 undefined）"
                            % (fn, name))

        # ③ 收集本模块的导出名
        for m in re.finditer(
                r"^export\s+(?:async\s+)?(?:const|let|var|function|class)\s+(\w+)",
                text, re.M):
            exported_by.setdefault(m.group(1), []).append(fn)
        for m in re.finditer(r"^export\s*\{([^}]+)\}", text, re.M):
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[-1].strip()
                if name:
                    exported_by.setdefault(name, []).append(fn)

        print("  %-16s import(%d): %s"
              % (fn, len(imported), ", ".join(sorted(imported)) or "(无)"))

    # ③ 重名（export * 会变成 ambiguous → 静默 undefined）
    for name, files in sorted(exported_by.items()):
        if len(files) > 1:
            problems.append("导出重名 `%s` 出现在 %s —— index.js 用 `export *` "
                            "会让它变成 ambiguous（静默 undefined）"
                            % (name, ", ".join(files)))

    # ④ index.js 覆盖
    idx = _read(INDEX)
    missing = [f[:-3] for f in mod_files if f[:-3] not in idx]
    for m in missing:
        problems.append("index.js 未 re-export 模块 `%s`" % m)
    print()
    print("index.js re-export 覆盖: %d/%d 个模块"
          % (len(mod_files) - len(missing), len(mod_files)))

    print()
    if problems:
        print("=== ✗ 发现 %d 处问题 ===" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("=== ✓ 通过：无漏 import / 无无效 import / 无重名 / re-export 齐全 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
