# -*- coding: utf-8 -*-
"""静态检查：composable 模块层是否引用了只在工厂函数作用域里存在的 DI 参数。

【为什么需要】
  `composables/` 里的模式是：
      export function useXxx({ selectedDate, foo }) { ... }
  若把**依赖这些参数的函数**留在模块顶层（export function 之前），
  运行时就是 `ReferenceError: selectedDate is not defined`，
  而且通常被 `try/catch` 吞成"请求失败"，**用户看不出是代码错**。

  本项目已踩三次：
    · 2026-11-14 `defErr / defGroup is not defined`（useDefensiveBoard 拆分）
    · 2026-11-14 `useWorkbenchBrief` 空体（生成但没真调用）
    · 2026-11-16 `selectedDate is not defined`（useObsAIReview 的 loadObsAI 留模块层）

【判定】
  取每个 `export function useXxx({ a, b, c })` 的 DI 参数名，
  在**该 export function 之前**的文本里查找这些名字（整词匹配）。
  命中 ⇒ 模块层引用了函数作用域变量 ⇒ 报错。

  局限性：只检查"参数名是否在模块层出现"，不做完整作用域分析；
  但它恰好覆盖本项目的实际踩坑模式，且零依赖、可进 CI。

用法：python scripts/check_composable_scope.py [--json]
退出码：0 通过 / 1 有问题
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPO = ROOT / "frontend" / "src" / "composables"

# export function useXxx({ a, b })  /  export function useXxx(a, { b })
SIG = re.compile(
    r"export\s+function\s+(use\w+)\s*\(\s*(?:\{[^}]*\}|[^)]*?)\s*\)\s*\{", re.S)
PARAMS = re.compile(r"\{([^{}]*)\}", re.S)


def _param_names(sig_text):
    """从签名里抽出解构出来的 DI 参数名。"""
    names = []
    for block in PARAMS.findall(sig_text):
        for piece in block.split(","):
            p = piece.split(":")[0].split("=")[0].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", p or ""):
                names.append(p)
    return names


def check_file(path: Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits = []
    for m in SIG.finditer(text):
        fn = m.group(1)
        names = _param_names(text[m.start():m.end()])
        if not names:
            continue
        head = text[:m.start()]          # export function 之前的模块层
        # 去掉注释，避免注释里提到参数名造成误报
        head_nc = re.sub(r"//[^\n]*", "", head)
        head_nc = re.sub(r"/\*.*?\*/", "", head_nc, flags=re.S)
        for n in names:
            if re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(n), head_nc):
                hits.append({"file": path.name, "function": fn, "param": n})
    return hits


def main() -> int:
    if not COMPO.exists():
        print("✗ 找不到 %s" % COMPO)
        return 1

    problems = []
    files = sorted(COMPO.glob("*.js"))
    for f in files:
        problems.extend(check_file(f))

    if "--json" in sys.argv:
        print(json.dumps({"files": len(files), "problems": problems},
                         ensure_ascii=False, indent=2))
    else:
        print("检查 %d 个 composable" % len(files))
        if problems:
            print("")
            for p in problems:
                print("  ✗ %s → %s() 在模块层引用了 DI 参数 `%s`"
                      % (p["file"], p["function"], p["param"]))
            print("")
            print("  ⇒ 把引用该参数的函数**移到 export function 体内**"
                  "（只有纯自持状态可以留模块层）")
        else:
            print("✓ 通过：模块层没有引用工厂函数作用域里的参数")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
