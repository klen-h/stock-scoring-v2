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

export default renderMd
