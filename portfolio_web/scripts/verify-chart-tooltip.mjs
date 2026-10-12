#!/usr/bin/env node
/**
 * 用 Chrome DevTools Protocol 验证饼图 tooltip。
 *
 * 图例已移除，悬浮提示是识别扇区的唯一途径，因此必须真实验证：
 *   1. 启动 headless Chrome（远程调试端口）
 *   2. 打开页面，等图表渲染
 *   3. 向扇区内部派发真实 mouseMoved 事件
 *   4. 检查 ECharts 是否生成了提示 DOM，并抓取截图
 *
 * 用法: node scripts/verify-chart-tooltip.mjs [url] [outPrefix]
 */
const URL_TO_CHECK = process.argv[2] || 'http://localhost:5173/'
const OUT_PREFIX = process.argv[3] || '/tmp/chart-tooltip'
const PORT = Number(process.env.CDP_PORT || 9333)

import { spawn } from 'node:child_process'
import { writeFileSync, mkdirSync, existsSync } from 'node:fs'

/** 按平台找 Chrome；可用 CHROME_PATH 覆盖 */
function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH
  const candidates = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  ]
  return candidates.find((p) => existsSync(p)) || 'google-chrome'
}

const CHROME = findChrome()

const chrome = spawn(CHROME, [
  '--headless=new',
  '--disable-gpu',
  '--no-sandbox',
  '--hide-scrollbars',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${process.env.CDP_PROFILE || '/tmp/cdp-profile'}`,
  '--window-size=1440,900',
  'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function getTarget() {
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const list = await res.json()
      const page = list.find((t) => t.type === 'page')
      if (page?.webSocketDebuggerUrl) return page
    } catch { /* 还没起来 */ }
    await sleep(250)
  }
  throw new Error('无法连接 Chrome 调试端口')
}

let msgId = 0
function makeClient(ws) {
  const pending = new Map()
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    }
  })
  return (method, params = {}) => {
    const id = ++msgId
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
      ws.send(JSON.stringify({ id, method, params }))
    })
  }
}

async function evaluate(send, expression) {
  const r = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text)
  return r.result.value
}

try {
  const target = await getTarget()
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true })
    ws.addEventListener('error', rej, { once: true })
  })
  const send = makeClient(ws)

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Page.navigate', { url: URL_TO_CHECK })

  // 等图表 canvas 出现
  let ready = false
  for (let i = 0; i < 60; i++) {
    await sleep(300)
    ready = await evaluate(send, `!!document.querySelector('.chart-canvas canvas')`)
    if (ready) break
  }
  if (!ready) throw new Error('图表 canvas 未渲染')
  await sleep(600)

  // 取第一个饼图的 canvas 边界
  const box = await evaluate(send, `
    (() => {
      const cv = document.querySelector('.chart-canvas canvas');
      const r = cv.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    })()
  `)
  console.log('canvas box:', box)

  // 圆心 + 半径 0.55 处（正右方）→ 落在从 12 点顺时针开始的第一个大扇区内
  const cx = box.x + box.w / 2
  const cy = box.y + box.h / 2
  const r = Math.min(box.w, box.h) / 2
  const px = cx + r * 0.55
  const py = cy

  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: px, y: py, button: 'none' })
  await sleep(800)
  // 再动一下，触发 ECharts 的 mousemove 处理
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: px + 1, y: py, button: 'none' })
  await sleep(800)

  // ECharts 的 tooltip 是一个绝对定位的 div
  const tip = await evaluate(send, `
    (() => {
      const card = document.querySelector('.chart-card');
      const divs = [...card.querySelectorAll('div')];
      const t = divs.find(d => /占比\\s*[\\d.]+%/.test(d.textContent || '') && d !== card);
      return t ? { text: t.textContent.trim(), html: t.innerHTML } : null;
    })()
  `)

  console.log('tooltip dom:', tip ? JSON.stringify(tip.text) : 'NOT FOUND')

  const shot = await send('Page.captureScreenshot', { format: 'png' })
  mkdirSync('/tmp', { recursive: true })
  writeFileSync(`${OUT_PREFIX}.png`, Buffer.from(shot.data, 'base64'))
  console.log('screenshot:', `${OUT_PREFIX}.png`)

  ws.close()

  if (!tip) {
    console.error('\n❌ 未检测到 tooltip')
    process.exit(1)
  }
  // 校验内容完整性
  const ok = tip.text.includes('占比') && /[\d,]+\.\d{2}/.test(tip.text)
  console.log(ok ? '\n✅ tooltip 内容完整（含金额与占比）' : '\n⚠️ tooltip 内容不完整')
  process.exit(ok ? 0 : 1)
} catch (e) {
  console.error('验证失败:', e.message)
  process.exit(1)
} finally {
  chrome.kill()
}
