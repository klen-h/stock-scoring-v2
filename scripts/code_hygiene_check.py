# -*- coding: utf-8 -*-
"""代码卫生门（ratchet 棘轮模式）—— 2026-10-24 清理收口时建立。

用途：拦住"同类错误再犯"，不追究存量（存量见 docs/BACKLOG.md，修一条降一次基线）。
用法：
    python scripts/code_hygiene_check.py                  # 对照基线检查，超量则 exit 1
    python scripts/code_hygiene_check.py --update-baseline  # 重算基线（修复后主动收紧用）

规则（每条都对应已发生的事故，出处见 docs/DEV_CHECKLIST.md §6 / docs/PROJECT_GUARDRAILS.md）：
  utcnow              datetime.utcnow() + 手加偏移（坑 7：服务器时区双加/少加）
  naive_now           无时区 datetime.now()（生产 UTC ⇒ 差 8h；仅统计 backend/app）
  naive_fromtimestamp 不带 tz 的 fromtimestamp（同上，BK-08 同型）
  except_pass         同行 `except...: pass`（坑 12 / BK-51：静默死亡，GUARDRAILS #11）
  req_no_timeout      requests 调用缺 timeout
  sleep_in_request    time.sleep 出现在请求路径（routers/main.py，Guardrails #3）
  refresh_all_in_api  接口路径调用 refresh_all_stocks（全市场刷新 ~14s，Guardrails #3）

口径说明：扫描范围 = backend/app + scripts + frontend/src 的 .py/.vue/.js；
docs/reports/.codebuddy/dist 不扫。基线文件随代码提交，降基线 = 修好的证据。
"""
import ast
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, 'scripts', 'code_hygiene_baseline.json')

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.zcode', 'docs', '.codebuddy',
             'reports', 'dist', 'tmp_archive'}
SCAN_EXT = {'.py', '.vue', '.js', '.ts'}

PATTERNS = [
    ("utcnow", re.compile(r'datetime\.utcnow\('), 'datetime.utcnow() 手加时区（坑 7）'),
    ("naive_now", re.compile(r'datetime\.now\(\)'), 'datetime.now() 无时区（生产 UTC 差 8h）'),
    ("naive_fromtimestamp", re.compile(r'fromtimestamp\((?![^)]*tz)[^)]*\)'),
     'fromtimestamp 未带 tz（BK-08 同型）'),
    # except_pass 改用 AST 检测（见 count_all）：同行正则查不到跨行 `except:\n    pass`，
    # 而 2026-10-24 审查的 4 处静默死亡全是跨行形态
    ("req_no_timeout", re.compile(r'requests\.(get|post|request)\((?![^)]*timeout)[^)]*\)', re.S),
     'requests 缺 timeout'),
    ("sleep_in_request", re.compile(r'time\.sleep\('), '请求路径 time.sleep（Guardrails #3）'),
    ("refresh_all_in_api", re.compile(r'refresh_all_stocks\('), '接口路径全市场刷新（Guardrails #3）'),
]

AST_ONLY = ("except_pass", None, 'except 处理体只有 pass（GUARDRAILS #11，AST 检测，.py）')

# ★★ 2026-10-25（真实事故两起）：`const [info, fund, sr, rsi] = await Promise.allSettled([...])`
#   把 `getStockScore` 塞进第二批后没改解构名，下面却写 `score.status` ⇒
#   `ReferenceError: score is not defined`，onMounted 直接中断、整页白屏。
#   **构建不会报错**（esbuild 不做未声明变量检查），所以只能靠这条启发式：
#   解构出来的名字在整文件里只出现 1 次（= 声明处）= 必然没被使用；
#   若同时有别处引用了"本该叫这个名字"的变量，就是这个事故形态。
#   `_` 前缀视为有意忽略。
UNUSED_DESTRUCTURE = ("unused_destructure", None,
                      'Promise.allSettled 解构名声明后从未使用（错名 ⇒ 运行时 ReferenceError）')
_DESTRUCT_RE = re.compile(r'const\s*\[([^\]]+)\]\s*=\s*await\s+Promise\.(?:allSettled|all)\(')
JS_EXT = ('.vue', '.js', '.ts')

# sleep/refresh 只在请求路径算红旗；其余路径（调度循环/日批）是合法使用
PATH_SCOPED = {
    "sleep_in_request": lambda rel: '/routers/' in rel or rel.endswith('app/main.py'),
    "refresh_all_in_api": lambda rel: '/routers/' in rel or rel.endswith('app/main.py'),
}


def iter_files():
    for base in ('backend/app', 'scripts', 'frontend/src'):
        b = os.path.join(ROOT, base.replace('/', os.sep))
        for dirpath, dirnames, files in os.walk(b):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in files:
                if os.path.splitext(f)[1].lower() in SCAN_EXT:
                    yield os.path.join(dirpath, f)


def count_all():
    counts = {name: [] for name, _rx, _d in PATTERNS}
    counts[AST_ONLY[0]] = []
    counts[UNUSED_DESTRUCTURE[0]] = []
    for p in iter_files():
        rel = os.path.relpath(p, ROOT).replace(os.sep, '/')
        try:
            src = io.open(p, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        for i, ln in enumerate(src.splitlines(), 1):
            for name, rx, _d in PATTERNS:
                if rx.search(ln):
                    scope = PATH_SCOPED.get(name)
                    if scope and not scope(rel):
                        continue
                    counts[name].append('%s:%d' % (rel, i))
            # 解构错名（前端，构建查不出）
            if p.lower().endswith(JS_EXT):
                m = _DESTRUCT_RE.search(ln)
                if m:
                    for nm in [x.strip().split(':')[0].strip()
                               for x in m.group(1).split(',')]:
                        if not nm or nm.startswith('_'):
                            continue
                        if len(re.findall(r'\b%s\b' % re.escape(nm), src)) <= 1:
                            counts[UNUSED_DESTRUCTURE[0]].append('%s:%d:%s' % (rel, i, nm))
        # AST 检测（仅 .py）：except 处理体 = [Pass]（含带注释的 pass 行）
        if p.endswith('.py'):
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 \
                        and isinstance(node.body[0], ast.Pass):
                    counts[AST_ONLY[0]].append('%s:%d' % (rel, node.body[0].lineno))
    return counts


def main():
    counts = count_all()
    if '--update-baseline' in sys.argv:
        io.open(BASELINE, 'w', encoding='utf-8', newline='\n').write(
            json.dumps({k: len(v) for k, v in sorted(counts.items())},
                       ensure_ascii=False, indent=1) + '\n')
        print('baseline updated ->', BASELINE)
        for k in sorted(counts):
            print('  %-22s %d' % (k, len(counts[k])))
        return 0
    baseline = json.load(io.open(BASELINE, encoding='utf-8'))
    failed = False
    all_names = ([(n, _rx, d) for n, _rx, d in PATTERNS]
                 + [AST_ONLY, UNUSED_DESTRUCTURE])
    for name, _rx, desc in all_names:
        now_n, base_n = len(counts[name]), int(baseline.get(name, 0))
        mark = 'OK ' if now_n <= base_n else 'FAIL'
        if now_n > base_n:
            failed = True
            print('%s %-22s now=%-4d baseline=%-4d  %s' % (mark, name, now_n, base_n, desc))
            for loc in counts[name][base_n:]:   # 只列超出基线的新增点
                print('     new -> %s' % loc)
        else:
            print('%s %-22s now=%-4d baseline=%-4d  %s' % (mark, name, now_n, base_n, desc))
    if failed:
        print('\n检查不通过：出现超出基线的新增违规。修复它，或（确属误报时）'
              '在 scripts/code_hygiene_check.py 调整口径后 --update-baseline 并说明理由。')
        return 1
    print('\n检查通过：无新增违规。（基线只许降不许升；修复后可 --update-baseline 收紧）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
