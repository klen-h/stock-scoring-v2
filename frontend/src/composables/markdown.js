// ==============================================================================
// markdown 渲染器 —— **全站唯一实例**（2026-10-11，L1）
//
// 【为什么收拢】此前有 **5 个页面各自 `new MarkdownIt(...)`**
// （`Workbench` / `MonitorView` / `DailyReport` / `ContradictionsView` / `BacktestView`），
// 而且**选项已经漂移**（Workbench 是 `linkify: false`，其余 `true`）⇒ 同一段 LLM 文本
// 在不同页面渲染结果可能不同。收拢成一个实例后，"观点输出"全站行为一致。
//
// 【选项语义（改动前请想清楚影响面）】
//   · `html: false` —— **安全底线**：LLM/推送文本里若含 `<script>`/`<img onerror>` 一律
//     当纯文本转义，不会被执行。**不要为了"更好看"打开它**（内容来自模型，不可信）。
//   · `breaks: true` —— 单个换行也换行。LLM 输出多是"一行一句"的 bullet-ish 文本，
//     不开的话会挤成一大段（这是 5 处原先都开着的选项）。
//   · `linkify: true` —— 裸 URL 自动成链接。统一取**多数派**（原来只有 Workbench 是 false）。
//
// 【渲染样式】不在本文件，统一在 `src/styles/md.css`（全局，配合 `class="md-body"` 使用）：
//   `v-html="renderMd(text)"` 的容器**必须加 `md-body` 类**，否则没有排版与重点层级。
//
// ⚠️ **不用本渲染器的地方**：`ScoreRank.vue` 的"今日雷达"推送时间线正文用的是
//   `contentSegs()`（把 `名称(代码)` 切成可点链接，见 2026-09-23 的改动）——
//   那是**自定义分词**，与 markdown 渲染互斥（`html:false` 下 markdown-it 也不会替我们
//   注入组件）。要合并须另设计，别在这里偷偷开 `html: true`。
// ==============================================================================
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

/** markdown 文本 → HTML 字符串（null/undefined 安全 ⇒ 返回 ''）。 */
export function renderMd(text) {
  return md.render(String(text || ''))
}

// ── 「一句话结论」抽取（L2，2026-10-11）────────────────────────────────────────
// 【约定（**跨前后端的契约**，改这里等于改全站）】生成侧若想让结论被置顶，就在**首行**输出：
//     `**结论**：<一句话>`  或  `## 结论` + 紧随其后的一行文本
//   已约定：雷达（`routers/flash.py::flash_radar_analysis` 的 prompt）、
//          一页纸诊断（`stock_diagnosis.py::diagnose` 的 prompt）。日报/简报**尚未**约定。
//
// 【两条纪律】
//   ① **前端绝不猜**："第一段/第一句就是结论"这种启发式不在这里做 —— 日报首段是"市场脉冲"，
//      猜错会把普通段落放大成结论。**只有显式标记**才认。
//   ② **无标记 ⇒ 原样返回**（`conclusion=''`、`body=原文`）⇒ 旧数据与未改的生成器行为**完全不变**。
//      这也是为什么它可以安全上线：LLM 没照做（或没配 LLM）时，前端只是"没有结论条"，不报错。
// 两种**显式**标记（都要求带冒号，避免把 `**结论**`  这种普通加粗误判）：
const _CONCLUSION_INLINE_RE = /^\s*\*\*结论\*\*\s*[:：]\s*(.*)$/   // `**结论**：一句话…`
const _CONCLUSION_HEAD_RE = /^\s*#{1,6}\s*结论\s*[:：]?\s*$/      // `## 结论`

/** @returns {{conclusion: string, body: string}} */
export function splitInsight(text) {
  const raw = String(text || '')
  const lines = raw.split('\n')
  let i = 0
  while (i < lines.length && !lines[i].trim()) i++          // 跳过前导空行
  if (i >= lines.length) return { conclusion: '', body: raw }
  const inline = lines[i].match(_CONCLUSION_INLINE_RE)
  if (inline) {
    const c = (inline[1] || '').trim()
    // ★ `**结论**：` 后面是空的 ⇒ **宁缺勿猜**（若顺手取下一行，会把 `**现状**：…` 当成结论）
    if (!c) return { conclusion: '', body: raw }
    return { conclusion: c, body: lines.slice(i + 1).join('\n').trim() }
  }
  if (_CONCLUSION_HEAD_RE.test(lines[i])) {                 // `## 结论` ⇒ 结论在下一非空行
    let j = i + 1
    while (j < lines.length && !lines[j].trim()) j++
    if (j >= lines.length) return { conclusion: '', body: raw }   // 只有标题没内容 ⇒ 当普通文本
    return { conclusion: lines[j].trim(), body: lines.slice(j + 1).join('\n').trim() }
  }
  return { conclusion: '', body: raw }
}

// ── 结构化观点（L3，2026-10-11）──────────────────────────────────────────────
// 后端 `app/insight.py` 会把 LLM 的 JSON 校验成 `{conclusion, reasons[]}`（并同时组装 markdown）。
// 前端**按角色渲染**：结论 → 琥珀结论条；依据 → 列表。这里只做"数组 → markdown 列表"的
// 纯转换（便于在 node 里单测），实际渲染仍走同一个 `renderMd` + `md-body` 样式。
/** `['**现状**：…','**风险**：…']` → `- **现状**：…\n- **风险**：…` */
export function reasonsMarkdown(reasons) {
  if (!Array.isArray(reasons)) return ''
  return reasons.map((r) => `- ${String(r || '').trim()}`).filter((l) => l !== '- ').join('\n')
}

export default renderMd
