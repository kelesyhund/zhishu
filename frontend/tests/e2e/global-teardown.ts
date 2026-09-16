import { rmSync } from 'node:fs'
import { resolve } from 'node:path'

export default function globalTeardown() {
  for (const target of ['.stage17-e2e.sqlite3', '.stage17-e2e-media']) {
    try { rmSync(resolve(import.meta.dirname, '../../../backend', target), { recursive: true, force: true }) }
    catch { /* CI cleanup repeats this after services have stopped. */ }
  }
}
