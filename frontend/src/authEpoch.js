// v1.7.2：登录「代」计数器 —— 独立小模块，专为避免 api.js ↔ router.js 循环依赖。
//
// 起因：token 失效时一个页面常并发 3~5 个请求，旧逻辑会逐个弹出「登录已过期」
// 并重复 router.push('/login')，控制台留下一整墙 401 红字。
// 现在由 api.js 记录「本代是否已处理」，进入登录页时 bump 一代复位。
// 若把 bumpAuthEpoch 放在 api.js 里再由 router.js 反向 import，ESM 会踩 TDZ：
// 首屏报 `Cannot access '_n' before initialization` 直接白屏 —— 故单独成文件。
let epoch = 0

export function bumpAuthEpoch() {
  epoch += 1
}

export function currentAuthEpoch() {
  return epoch
}
