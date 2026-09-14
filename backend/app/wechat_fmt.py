# -*- coding: utf-8 -*-
"""
企微友好文本渲染（Markdown 表格 → 逐行列表）。

★ 背景（2026-09-15）：企微自定义机器人 markdown **不支持表格**——`| a | b |`
  会按纯文本渲染，竖线散乱、表头与内容不在一条竖线上。凡是要**推到企微**的
  表格，都应改用本模块转成「每行一条」的列表式。

设计要点：
  - 字段用**全角空格**分隔：企微是比例字体，任何"空格填充对齐"都不可靠，
    故不做列对齐，改用全角空格 + 首列加粗做视觉分组。
  - 不做列名表头（列名重复出现纯属噪音），靠首列锚点定位。

⚠️ 仅用于**推送企微**的内容；前端仍用 Markdown 表格（浏览器渲染正常），
   `wechat=False` 的默认路径不改。

用法：
    from app.wechat_fmt import table_to_lines
    body = "\\n".join(table_to_lines(headers, rows, numbered=False, kv=True))
"""
from typing import List, Sequence

import re

FULL_SPACE = "\u3000"   # 全角空格：视觉分隔（不依赖定宽字体对齐）

# markdown 表格分隔行单元格：:?-{3,}:?（走标准写法，避免把「| - |」数据行误判）
_SEP_RE = re.compile(r"^:?-{3,}:?$")


def table_to_lines(headers: Sequence[str], rows: Sequence[Sequence], *,
                   numbered: bool = False, kv: bool = False,
                   bold_first: bool = True, bold_idx: int = 0,
                   sep: str = FULL_SPACE) -> List[str]:
    """(表头, 数据行) → 企微列表行。

    参数：
      headers:    列名（kv=True 时用作「列名 值」的标签）
      rows:       每行是序列（与 headers 对齐）
      bold_first: 是否加粗锚点列
      bold_idx:   加粗哪一列（默认 0；可指向"标签列"如名称，避免排名/代码列
                  被加粗——见 _label_col）
      numbered:   行首加「N. 」
      kv:         非锚点列渲染为「列名 值」（列多或语义不直观时更清晰），
                  否则只连值（列少、语义自明时更紧凑）
    """
    out: List[str] = []
    hdr = list(headers or [])
    for i, row in enumerate(rows):
        cells = [("" if c is None else str(c)) for c in row]
        if not cells:
            continue
        segs: List[str] = []
        for j, c in enumerate(cells):
            num = f"{i + 1}. " if (j == 0 and numbered) else ""
            if bold_first and j == bold_idx:
                segs.append(num + f"**{c}**")
            elif kv and j < len(hdr):
                segs.append(f"{hdr[j]} {c}")
            else:
                segs.append(num + c)
        out.append(sep.join(segs))
    return out


def _label_col(rows: List[List[str]]) -> int:
    """猜「标签列」= 第一个**非纯数字**列（排名/代码是纯数字 → 跳过）。

    把加粗锚点放到有意义的列上：否则首列是排名时 `**1**` 毫无信息量。
    全数字列 → 回退 0（如纯指标表）。
    """
    if not rows:
        return 0
    ncols = max(len(r) for r in rows)
    for j in range(ncols):
        vals = [str(r[j]).strip() for r in rows if j < len(r) and str(r[j]).strip()]
        if vals and not all(v.isdigit() for v in vals):
            return j
    return 0


def _cells(row: str) -> List[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _convert_block(block: List[str], *, kv: bool, bold_first: bool) -> List[str]:
    """一个表格块（连续 | 行）→ 列表行；跳过第 2 行的分隔行。"""
    if not block:
        return []
    header = _cells(block[0])
    body = block[1:]
    if body and all(_SEP_RE.match(c or "") for c in _cells(body[0])):
        body = body[1:]              # 丢掉 |---|---| 分隔行
    rows = [_cells(r) for r in body if r.strip().strip("|").strip()]
    if not rows:
        return []
    return table_to_lines(header, rows, kv=kv, bold_first=bold_first,
                          bold_idx=_label_col(rows))


def markdown_tables_to_lists(md: str, *, kv: bool = True,
                             bold_first: bool = True) -> str:
    """把 markdown 文本里**所有表格块**转成企微友好列表，其余行原样保留。

    用于「同一份 markdown 落库给前端看表格、推企微前转列表」——见
    `daily_report.run_daily_report`。非表格内容（标题/引用/正文）不受影响；
    文本里没有表格时结果与原串一致（幂等，可安全重复调用）。
    """
    lines = (md or "").split("\n")
    out: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("|") and s.endswith("|"):
            block = []
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            out.extend(_convert_block(block, kv=kv, bold_first=bold_first))
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)
