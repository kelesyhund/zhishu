import { gzipSync } from 'node:zlib'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const assetsDir = fileURLToPath(new URL('../dist/assets/', import.meta.url))
const files = readdirSync(assetsDir).filter((name) => name.endsWith('.js'))
const metrics = files.map((name) => {
  const path = join(assetsDir, name)
  const content = readFileSync(path)
  return { name, raw: statSync(path).size, gzip: gzipSync(content).length }
}).sort((left, right) => right.raw - left.raw)

const totalRaw = metrics.reduce((sum, item) => sum + item.raw, 0)
const largest = metrics[0]
const entry = metrics.find((item) => item.name.startsWith('index-'))
const budgets = [
  ['总 JavaScript', totalRaw, 1_500_000],
  ['最大 Chunk', largest?.raw || 0, 650_000],
  ['首屏入口 Chunk', entry?.raw || 0, 100_000],
]

console.table(metrics)
let failed = false
for (const [label, actual, limit] of budgets) {
  const ok = actual <= limit
  console.log(`${ok ? 'PASS' : 'FAIL'} ${label}: ${actual} / ${limit} bytes`)
  failed ||= !ok
}
if (failed) process.exit(1)
