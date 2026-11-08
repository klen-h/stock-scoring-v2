# -*- coding: utf-8 -*-
"""代码卫生门（ratchet 棘轮模式）—— 2026-10-24 清理收口时建立。

用途：拦住"同类错误再犯"，不追究存量（存量见 docs/BACKLOG.md，修一条降一次基线）。
用法：
    python scripts/code_hygiene_check.py                  # 对照基线检查，超量则 exit 1
    python scripts/code_hygiene_check.py --update-baseline  # 重算基线（修复后主动收紧用）

规则（每条都对应已发生的事故，出处见 docs/DEV_CHECKLIST.md §6 / docs/PROJECT_GUARDRAILS.md）：
  utcnow              datetime.utcnow() + 手加偏移（坑 7：服务器时区双加/少加）
  naive_now           无时区 datetime.now()（生产 UTC ⇒ 差 8h；**仅 backend/app**）
  naive_fromtimestamp 不带 tz 的 fromtimestamp（同上，BK-08 同型；**仅 backend/app**）
  except_pass         处理体只有 pass（坑 12 / BK-51：静默死亡，GUARDRAILS #11）
  req_no_timeout      requests 调用缺 timeout
  sleep_in_request    time.sleep 出现在请求路径（routers/main.py，Guardrails #3）
  refresh_all_in_api  接口路径调用 refresh_all_stocks（全市场刷新 ~14s，Guardrails #3）

口径说明：扫描范围 = backend/app + scripts + frontend/src 的 .py/.vue/.js；
docs/reports/.codebuddy/dist 不扫。基线文件随代码提交，降基线 = 修好的证据。

★★ 2026-11-08 三处口径修正（起因：CI 连续失败且**报错位置全部指错**）

  1. **baseline 从「只存计数」改为「计数 + 按文件计数」**（本次最重要）
     旧实现只能靠「按路径排序后取尾部 N 个」指认新增点。一旦某个**旧**违规被修好
     （排序靠前），后面的点整体前移，最后一个**旧**违规就被当成新增。
     实证：真实净增在 `backend/app/` 的 13 个文件里，报出来的 17 个却全是
     `scripts/` 中字母序靠后的文件，**一个都不对**。现在改为按文件比对，
     报错形如 `+1 backend/app/x.py (现 1 处，基线 0；行号 6)`。

  2. **`naive_fromtimestamp` 的正则改为 AST**
     旧正则只在括号里找字面量 `tz`，而正确写法 `datetime.fromtimestamp(ts, _BJ)`
     传的是**位置参数**，括号里没有 "tz" ⇒ **正确代码被判违规**。
     改 AST 后：8 → 2，**6 个是假阳性**。

  3. **时区三条规则补 scope（只统计 backend/app）**
     本 docstring 一直写着「仅统计 backend/app」，但实现把 `scripts/` 也算进去了，
     且 **AST 分支完全没走 `PATH_SCOPED`**，导致 scope 形同虚设。
     `scripts/` 是本机 Windows 运维脚本，本地时区即北京时间，`now()` 语义正确；
     把它们判违规只能靠抬基线消化，是掩盖而非解决。修正后 naive_now 121 → 104。

  ★ `except_pass` 新增**豁免通道**：行内写 `# noqa: except_pass  <理由>` 即不计入。
    理由：原规则**没有豁免**，"有兜底的容错"（简报里某数据块取不到就不放进去、
    `float()` 转换失败跳过某字段）和"静默死亡"被一视同仁，只能靠抬基线消化。
    豁免强制要求**同一行写明理由**，使"有意忽略"与"忘了处理"在代码上可区分。
    ⚠️ 不要用它掩盖真问题：如果 except 之后**没有任何兜底**（既不 return 默认值、
    也不记日志、也不继续安全路径），那它就是静默死亡，**必须修而不是加 noqa**。
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

# ★★ 2026-11-06：`utcnow` / `naive_now` 从**逐行正则**改为 **AST 检测**（见下方 AST_NAIVE）。
#   起因（CI 实测）：正则会把"提到这些词"的文本也算成违规 ——
#     · 本文件第 11 行 docstring 里写着 `datetime.now()`（规则说明本身）
#     · `pbw_replay.py:30` 的注释里也用这个写法解释坑
#   两者都不是调用，却让基线从 150 涨到 155，且**每加一条规则说明就会再涨一次**。
#   ⇒ 只认真正的 `ast.Call`（`datetime.now()` 无参 / `datetime.utcnow()`）。
#   这也与 `except_pass` 的 AST 检测口径统一了。
AST_NAIVE = [
    ("utcnow", None, 'datetime.utcnow() 手加时区（坑 7）'),
    ("naive_now", None, 'datetime.now() 无时区（生产 UTC 差 8h）'),
    ("naive_fromtimestamp", None, 'fromtimestamp 未带 tz（BK-08 同型；AST：认第2位置参数/tz=）'),
]

PATTERNS = [
    # ★★ 2026-11-08：`naive_fromtimestamp` **从正则改为 AST**（同 naive_now 的理由，但更严重）。
    #   旧正则 `fromtimestamp\((?![^)]*tz)[^)]*\)` 只在括号内找 **`tz` 字面量**，
    #   而正确写法 `datetime.fromtimestamp(ts, _BJ)` 传的是**位置参数**，括号里没有 "tz"
    #   ⇒ **正确代码被判违规**。反过来 `fromtimestamp(ts, timezone.utc)` 因为有 "tz" 才侥幸通过。
    #   ⇒ 这是**假阳性的根源**：只要有人用位置参数传 tz，就会被误报。
    #   现改为 AST：认「第 2 个位置参数」或「`tz=` 关键字」。
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
    # ★★ 2026-11-08：时区三条**只对 `backend/app` 生效** —— 本文件 docstring 第 11-12 行
    #   本来就写着「仅统计 backend/app」，但实现一直没加 scope，把 `scripts/` 也算进去了。
    #   为什么 scripts 不该算：`datetime.now()` 的危害是「**服务器**跑在 UTC ⇒ 差 8h」，
    #   而 `scripts/` 是**本机 Windows 运维脚本**（日批也是本地跑），本地时区就是北京时间，
    #   无 tz 的 `now()` 语义正确。**把本地脚本的 now() 判违规属于口径错位**，
    #   只能靠抬基线消化（本次 4 个新增全在 scripts/：lhb_seats_backfill / paper_vs_backtest_check×2 /
    #   refresh_indicators）——那是掩盖，不是解决。
    #   注：`except_pass` **不限 scope**（静默死亡在 scripts 里同样有害）。
    "naive_now": lambda rel: rel.startswith('backend/app/'),
    "utcnow": lambda rel: rel.startswith('backend/app/'),
    "naive_fromtimestamp": lambda rel: rel.startswith('backend/app/'),
}


def iter_files():
    for base in ('backend/app', 'scripts', 'frontend/src'):
        b = os.path.join(ROOT, base.replace('/', os.sep))
        for dirpath, dirnames, files in os.walk(b):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in files:
                if os.path.splitext(f)[1].lower() in SCAN_EXT:
                    yield os.path.join(dirpath, f)


def _attr_root(n):
    """`a.b.c` → 'a'（取最左的 Name），用于识别 `datetime.now()` 的调用者。"""
    while isinstance(n, ast.Attribute):
        n = n.value
    return n.id if isinstance(n, ast.Name) else ''


def count_all():
    counts = {name: [] for name, _rx, _d in PATTERNS}
    counts[AST_ONLY[0]] = []
    counts[UNUSED_DESTRUCTURE[0]] = []
    for _n, _rx, _d in AST_NAIVE:      # ★ AST 检测的两条也要有 key，否则 KeyError
        counts[_n] = []
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
            _lines = src.splitlines()

            def _noqa_pass(lineno):
                """该行是否带 `# noqa: except_pass`（显式宣告"此处吞异常是有意的"）。

                ★ 2026-11-08：原规则**没有豁免通道**，导致"有兜底的容错"和
                  "静默死亡"被一视同仁 —— 例如简报生成器里"某数据块取不到就不放进去"、
                  `float()` 转换失败跳过某字段，这些都是**正确写法**，却只能靠抬基线消化。
                  抬基线是**掩盖**，不是解决。故加豁免：要求**同一行写明理由**，
                  使得"有意忽略"和"忘了处理"在代码上可区分。
                """
                if not (1 <= lineno <= len(_lines)):
                    return False
                ln = _lines[lineno - 1]
                return 'noqa: except_pass' in ln or 'noqa:except_pass' in ln

            # ★ 2026-11-08：时区三条的 scope（见 PATH_SCOPED 注释）。AST 分支原本**完全
            #   没走 PATH_SCOPED**，导致 docstring 写的"仅统计 backend/app"形同虚设。
            _tz_scoped = PATH_SCOPED['naive_now'](rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 \
                        and isinstance(node.body[0], ast.Pass):
                    # except 那一行或 pass 那一行任一带 noqa 即豁免
                    if _noqa_pass(node.lineno) or _noqa_pass(node.body[0].lineno):
                        continue
                    counts[AST_ONLY[0]].append('%s:%d' % (rel, node.body[0].lineno))
                # ★ 2026-11-06：naive_now / utcnow 的 AST 版（见 AST_NAIVE 上方说明）。
                #   只认真正的调用：`datetime.now()` **无参数**才算；
                #   `datetime.now(_BJ)` / `datetime.now(timezone.utc)` 是正确写法，不报。
                elif _tz_scoped and isinstance(node, ast.Call) \
                        and isinstance(node.func, ast.Attribute):
                    f = node.func
                    if _attr_root(f) != 'datetime':
                        continue
                    if f.attr == 'utcnow':
                        counts['utcnow'].append('%s:%d' % (rel, node.lineno))
                    elif f.attr == 'now' and not node.args and not node.keywords:
                        counts['naive_now'].append('%s:%d' % (rel, node.lineno))
                    # ★ 2026-11-08：fromtimestamp 的 tz 判定改成 AST。
                    #   正确写法有两类：`fromtimestamp(ts, tz_obj)`（**位置参数**）
                    #   或 `fromtimestamp(ts, tz=tz_obj)`。旧正则只查字面 "tz"，
                    #   位置参数写法会被误判 ⇒ 假阳性。
                    elif f.attr == 'fromtimestamp':
                        has_tz = len(node.args) >= 2 or any(
                            k.arg == 'tz' for k in node.keywords)
                        if not has_tz:
                            counts['naive_fromtimestamp'].append('%s:%d' % (rel, node.lineno))
    # ★ 排序：`ast.walk` 的顺序不保证行号递增，而 `main()` 用
    #   `counts[name][base_n:]` 取"超出基线的新增点" —— 不排序会指错位置。
    def _k(s):
        parts = s.split(':')
        return (parts[0], int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)

    for _k2 in counts:
        counts[_k2].sort(key=_k)
    return counts


def _split_file(loc):
    return loc.split(':')[0]


def _by_file(locs):
    out = {}
    for x in locs:
        f = _split_file(x)
        out[f] = out.get(f, 0) + 1
    return out


def main():
    counts = count_all()
    if '--update-baseline' in sys.argv:
        # ★★ 2026-11-08：baseline 从「只存计数」升级为「存计数 + 按文件计数」。
        #   原因（本次 CI 实证）：只存计数时，`main()` 只能靠「按路径排序后取尾部 N 个」
        #   来指认"新增点"。一旦某个**旧**违规被修好（排序靠前），后面的点整体前移，
        #   最后一个**旧**违规就被当成新增 ⇒ **报出来的位置完全不可信**。
        #   本次实际发生：真实净增在 `backend/app/*`（13 个文件），
        #   而报出来的 17 个全是 `scripts/*` 里按字母序靠后的文件，一个都不对。
        #   ⇒ 改存 `by_file`：判定"某文件计数变大"才叫新增，不受行号漂移与排序影响。
        data = {k: {'n': len(v), 'by_file': _by_file(v)}
                for k, v in sorted(counts.items())}
        io.open(BASELINE, 'w', encoding='utf-8', newline='\n').write(
            json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + '\n')
        print('baseline updated ->', BASELINE)
        for k in sorted(counts):
            print('  %-22s %d' % (k, len(counts[k])))
        return 0
    baseline = json.load(io.open(BASELINE, encoding='utf-8'))
    failed = False
    all_names = ([(n, _rx, d) for n, _rx, d in PATTERNS]
                 + [AST_ONLY, UNUSED_DESTRUCTURE] + AST_NAIVE)
    for name, _rx, desc in all_names:
        cur = counts[name]
        entry = baseline.get(name, 0)
        if isinstance(entry, dict):
            base_n = int(entry.get('n', 0))
            base_by_file = entry.get('by_file') or {}
            by_file_mode = True
        else:                       # 兼容旧格式（纯 int）——退化为只比总数
            base_n, base_by_file, by_file_mode = int(entry or 0), None, False
        now_n = len(cur)
        if now_n <= base_n:
            print('OK  %-22s now=%-4d baseline=%-4d  %s' % (name, now_n, base_n, desc))
            continue
        failed = True
        print('FAIL %-22s now=%-4d baseline=%-4d  %s' % (name, now_n, base_n, desc))
        if by_file_mode:
            # ★ 按文件报"哪个文件涨了、涨了多少"，并列出该文件当前的全部命中行
            now_by_file = _by_file(cur)
            for f, n in sorted(now_by_file.items()):
                b = int(base_by_file.get(f, 0))
                if n > b:
                    lines = [x.split(':')[1] for x in cur if _split_file(x) == f]
                    print('     +%d  %s   (现 %d 处，基线 %d；行号 %s)'
                          % (n - b, f, n, b, ','.join(lines)))
        else:
            # 旧 baseline：只能给"排序后多出来的那几行"，**位置不可信**，明确提示
            for loc in cur[base_n:]:
                print('     ? -> %s    (旧基线无按文件信息，位置仅供参考)' % loc)
            print('     ⚠ 建议执行 --update-baseline 升级为按文件记录（见 main() 注释）')
    if failed:
        print('\n检查不通过：出现超出基线的新增违规。修复它，或（确属误报时）'
              '在 scripts/code_hygiene_check.py 调整口径后 --update-baseline 并说明理由。')
        return 1
    print('\n检查通过：无新增违规。（基线只许降不许升；修复后可 --update-baseline 收紧）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
