// 前后端引擎一致性测试：读 Python 生成的同一份输入，跑前端 JS 引擎
import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, join } from 'path'
import { scoreStock } from './src/utils/scoringEngine.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const input = JSON.parse(
  readFileSync(join(__dirname, '..', 'backend', 'tmp_engine_input.json'), 'utf-8'))

const r = scoreStock(input)
console.log('[JS] 总分', r.total_score)
const dims = r.dimensions
const rows = [
  ['技术面', dims.technical],
  ['资金面', dims.capital],
  ['基本面', dims.fundamental],
  ['成长', dims.growth],
  ['质量', dims.quality],
]
for (const [name, d] of rows) {
  if (!d) { console.log(`  ${name}=null`); continue }
  console.log(`  ${name}=${d.score}`)
}
console.log('\n[Python 基准] 总分 53.6 | 技术面=47.1 资金面=47.3 基本面=51.8 成长=49.8 质量=88.8')
