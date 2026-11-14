// utils/dayCache.js —— 工作台加载器共享的「当日只拉一次」缓存（2026-11-14 自 Workbench.vue 抽出）。
// 多个加载器（brief/复盘对账/emotionReview/日历/宏观锁定…）共用同一份当日标记，
//   抽到这里避免逐个 composable 各带一份。key 约定：'<功能>:<phase>'。
const _dayCache = {}          // { key: 'YYYY-MM-DD' }
const _dayCached = (key, day) => _dayCache[key] === day
const _dayMark = (key, day) => { _dayCache[key] = day }

export { _dayCache, _dayCached, _dayMark }
