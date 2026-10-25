# wechat_fmt.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

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

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- （无项目内依赖）

## 被谁依赖

- `backend/app/backtest/run.py`
- `backend/app/daily_report.py`
- `backend/app/data_files.py`
- `backend/app/flash/wechat.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`table_to_lines`, `_label_col`, `_cells`, `_convert_block`, `markdown_tables_to_lists`

## 已知坑

- 企微消息格式化；企微不支持 markdown 表格（自动转列表）。

规模：123 行。
