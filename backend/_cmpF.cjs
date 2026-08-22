// 前后端评分对照脚本（前端侧）：复刻排行榜精算完整管线
// 管线：数据包（GitHub Pages）+ 实时行情（腾讯直连）+ indicatorWorker（vm 模拟）+ scoringEngine（vm 模拟）
// 用法: cd backend; node _cmpF.cjs [股票代码]   （默认 002479）
// 对照: python _cmpB.py [股票代码]，逐项比较 dims 下的子项分值
const fs = require('fs');
const path = require('path');
const https = require('https');
const vm = require('vm');
const zlib = require('zlib');

const CODE = process.argv[2] || '002479';
const SYMBOL = (CODE.startsWith('6') ? 'sh' : 'sz') + CODE;
const PACK_URL = 'https://klen-h.github.io/stock-scoring-v2/data/kline-pack-latest.json.gz';
const FE_SRC = path.resolve(__dirname, '../frontend/src');

function fetchBin(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return fetchBin(res.headers.location).then(resolve, reject);
      }
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve(Buffer.concat(chunks)));
    }).on('error', reject);
  });
}

function decodeGBK(buf) {
  try { return new TextDecoder('gbk').decode(buf); }
  catch { return buf.toString('latin1'); }
}

(async () => {
  // 1. 数据包
  const gz = await fetchBin(PACK_URL);
  const pack = JSON.parse(zlib.gunzipSync(gz).toString('utf8'));
  const entry = pack.stocks[CODE];
  if (!entry) { console.error(`数据包不含 ${CODE}`); process.exit(1); }
  const kl = entry.klines.slice(-150);  // 与排行榜精算口径一致（getKlines(code, 150)）
  console.log(`数据包: ${pack.date} v${pack.version} K线数: ${kl.length} 末根: ${JSON.stringify(kl[kl.length - 1])}`);

  // 2. indicatorWorker.js 用 vm 加载（等效 worker 环境，postMessage 置空）
  const workerSrc = fs.readFileSync(path.join(FE_SRC, 'workers/indicatorWorker.js'), 'utf8');
  const workerCtx = vm.createContext({ self: { postMessage: () => {} } });
  vm.runInContext(workerSrc, workerCtx);
  workerCtx.__kl = kl;
  const { series, latest } = vm.runInContext('calcTechnical(__kl)', workerCtx);
  console.log(`worker 末根指标: close=${latest.close} RSI=${latest.rsi} DIF=${latest.dif} DEA=${latest.dea} K=${latest.k} ma5=${latest.ma5} ma20=${latest.ma20}`);

  // 3. 实时行情（字段解析与 frontend/src/api/tencent.js 完全对齐）
  const raw = await fetchBin(`https://qt.gtimg.cn/q=${SYMBOL}`);
  const fields = decodeGBK(raw).split('=')[1].replace(/"/g, '').split('~');
  const stock = {
    code: CODE,
    name: fields[1],
    price: parseFloat(fields[3]) || 0,
    change_pct: parseFloat(fields[32]) || 0,
    amount: (parseFloat(fields[37]) || 0) * 10000,      // 万元 → 元
    turnover_rate: parseFloat(fields[38]) || 0,
    pe: parseFloat(fields[39]) || 0,
    pb: parseFloat(fields[46]) || 0,
    amplitude: parseFloat(fields[43]) || 0,
    market_cap: (parseFloat(fields[45]) || 0) * 10000,  // 亿元 → 万元
    float_cap: (parseFloat(fields[44]) || 0) * 10000,   // 亿元 → 万元
  };

  // 4. scoringEngine.js 用 vm 加载（去掉 ES module 的 export 关键字）
  const engineSrc = fs.readFileSync(path.join(FE_SRC, 'utils/scoringEngine.js'), 'utf8').replace(/^export /gm, '');
  const engineCtx = vm.createContext({ console });
  vm.runInContext(engineSrc, engineCtx);
  engineCtx.__args = { code: CODE, name: stock.name, technicalData: series, stockInfo: stock };
  const result = vm.runInContext('scoreStock(__args)', engineCtx);

  console.log('总分:', result.total_score);
  const out = { total: result.total_score, stockInfo: { pe: stock.pe, pb: stock.pb, market_cap: stock.market_cap, float_cap: stock.float_cap, amount: stock.amount, turnover_rate: stock.turnover_rate, amplitude: stock.amplitude, change_pct: stock.change_pct }, dims: {} };
  for (const [key, d] of Object.entries(result.dimensions || {})) {
    out.dims[key] = { score: d.score, details: d.details };
    console.log(`  ${key}: ${d.score}`);
    for (const [k, v] of Object.entries(d.details || {})) {
      console.log(`    - ${k}: ${JSON.stringify(v)}`);
    }
  }
  fs.writeFileSync(`_cmpF_${CODE}.json`, JSON.stringify(out, null, 1), 'utf8');
})().catch(e => { console.error(e); process.exit(1); });
