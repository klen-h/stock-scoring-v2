# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】路由唯一性自检 —— 抓「一个函数被挂了别人家的路径」这类静默事故
================================================================================

背景（2026-11-12 真实事故，藏了 ~4 天）：
  `routers/market.py` 里出现过

      @router.get("/emotion")          # ← 本该属于 market_emotion
      @router.get("/auction-panel")
      def auction_panel(): ...

  而真正的 `market_emotion()` **一个装饰器都没有** ⇒ 从未被暴露。
  FastAPI **不会报错**（两行装饰器都"注册成功"），只在匹配时按顺序命中前者
  ⇒ 前端 `GET /api/market/emotion` 一直拿到**竞价面板结构**：
  没有 `price_date`、没有 `auction` ⇒ 界面常驻"非当日口径 · 属于 —"、主卡空白。
  同时 `/market/pulse` 里那份 `emotion` 是完整的 ⇒ 两个数据源先后写同一个前端 ref
  ⇒ **先渲染完整数据（一闪）→ 再被错误结构覆盖**。

  这类 bug 的共同点：**改动一处、静默影响另一处**，且 `compileall` / `import app.main`
  / pyflakes **全都发现不了**（语法合法、名字存在、导入成功）。

检测口径：
  · 同一个 `(METHOD, path)` 出现在多条路由上 ⇒ 报错（后者永远不可达）
  · `@router.get` 堆在同一个函数上但语义不同 ⇒ 由上一条间接抓到
    （若两个路径都不同则不报，属合法的别名路由）

用法：
    python scripts/route_audit.py          # 有问题 exit 1
    cd backend && python ../scripts/route_audit.py
================================================================================
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

# 路径基于 __file__：从仓库根或 backend/ 下跑都能工作
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(os.path.dirname(_HERE), 'backend')
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def main() -> int:
    from app.main import app          # noqa: E402（必须在 sys.path 之后导入）

    seen = defaultdict(list)
    for r in app.routes:
        path = getattr(r, 'path', None)
        if not path:
            continue
        fn = getattr(r, 'endpoint', None)
        name = getattr(fn, '__name__', str(fn))
        methods = getattr(r, 'methods', None) or {'GET'}
        for m in methods:
            if m in ('HEAD', 'OPTIONS'):
                continue
            seen[(m, path)].append(name)

    dup = {k: v for k, v in seen.items() if len(set(v)) > 1}
    if dup:
        print('❌ 发现重复路由（同一 METHOD+path 被多个函数占用，后者永不可达）：')
        for (m, p), names in sorted(dup.items()):
            print('   %-7s %-40s -> %s' % (m, p, ' / '.join(names)))
        print()
        print('根因通常是装饰器堆叠（把 X 的路径写到了 Y 上）。')
        # ⚠️ 提示里要**去掉 `/api` 前缀**：`app.routes` 的 path 含全局前缀，
        #   而源码里写的是 `@router.get("/emotion")`，直接拿带前缀的串去 grep 会扑空。
        _p = sorted(dup)[0][1]
        _p = _p.split('/api/', 1)[-1] if '/api/' in _p else _p
        print('查法：grep -rn \'@router.*"%s"\' backend/app/routers/' % _p)
        return 1

    print('✅ 路由唯一性检查通过（%d 条 METHOD+path，无重复）' % len(seen))
    return 0


if __name__ == '__main__':
    sys.exit(main())
