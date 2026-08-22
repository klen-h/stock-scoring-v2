// 前后端指标对照脚本（前端侧）：数据包末几根 DIF/DEA/MACD 高精度输出（直接调 calcMACD，不取整）
// 用法: cd backend; node _cmpM.cjs [股票代码]   （默认 002479）
// 对照: python _cmpM.py [股票代码]
const fs = require('fs');
const path = require('path');
const https = require('https');
const vm = require('vm');
const zlib = require('zlib');

const CODE = process.argv[2] || '002479';
const PACK_URL = 'https://klen-h.github.io/stock-scoring-v2/data/kline-pack-latest.json.gz';

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

(async () => {
  const gz = await fetchBin(PACK_URL);
  const pack = JSON.parse(zlib.gunzipSync(gz).toString('utf8'));
  const entry = pack.stocks[CODE];
  if (!entry) { console.error(`数据包不含 ${CODE}`); process.exit(1); }
  const kl = entry.klines;
  console.log('K线根数:', kl.length, '末3日期:', kl.slice(-3).map(k => k[0]));

  const workerSrc = fs.readFileSync(path.resolve(__dirname, '../frontend/src/workers/indicatorWorker.js'), 'utf8');
  const ctx = vm.createContext({ self: { postMessage: () => {} } });
  vm.runInContext(workerSrc, ctx);
  ctx.__closes = kl.map(k => k[4]);
  const rows = vm.runInContext(`(() => {
    const { dif, dea, macd } = calcMACD(__closes);
    return __closes.map((c, i) => ({ close: c, dif: dif[i], dea: dea[i], macd: macd[i] }));
  })()`, ctx);

  const dates = kl.map(k => k[0]);
  for (let i = rows.length - 5; i < rows.length; i++) {
    console.log(dates[i], 'close=', rows[i].close, 'DIF=', rows[i].dif, 'DEA=', rows[i].dea, 'MACD=', rows[i].macd);
  }
})().catch(e => { console.error(e); process.exit(1); });
