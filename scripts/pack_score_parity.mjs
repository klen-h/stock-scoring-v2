/**
 * 前端评分引擎对齐验证（Node 侧）：读 pack_parity_input.json，
 * 用与浏览器完全相同的 scoringEngine.scoreStock 打分，输出 *_out.json。
 *
 * scoringEngine.js 是 ESM 但在 frontend package.json（无 type:module）下
 * 会被 Node 当 CJS —— 复制为 .mjs 临时文件再 import。
 */
import { readFileSync, writeFileSync, copyFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const inPath = process.argv[2]
if (!inPath) throw new Error('usage: node pack_score_parity.mjs <input.json>')

const cases = JSON.parse(readFileSync(inPath, 'utf-8'))

// 复制到临时 .mjs（规避 CJS 解析）；Windows 下动态 import 需要 file:// URL
const tmp = mkdtempSync(join(tmpdir(), 'parity-'))
const engPath = join(tmp, 'scoringEngine.mjs')
copyFileSync(join(here, '..', 'frontend', 'src', 'utils', 'scoringEngine.js'), engPath)

const { scoreStock } = await import(pathToFileURL(engPath).href)

const out = cases.map(c => {
  const r = scoreStock({
    code: c.code,
    name: c.name,
    technicalData: c.series,
    stockInfo: c.stock_info,
    finance: null,
    weights: null,
  })
  return { code: c.code, frontend: r.total_score }
})

writeFileSync(inPath + '.out', JSON.stringify(out))
rmSync(tmp, { recursive: true, force: true })
console.log(`[parity] frontend scored ${out.length} stocks`)
