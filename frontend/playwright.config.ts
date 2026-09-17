import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const backendDirectory = resolve(import.meta.dirname, '../backend')
const frontendDirectory = resolve(import.meta.dirname)
const python = process.platform === 'win32' ? 'py -3.10' : 'python'

export default defineConfig({
  testDir: './tests/e2e',
  globalTeardown: './tests/e2e/global-teardown.ts',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['line'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `${python} manage.py migrate --noinput && ${python} manage.py runserver 127.0.0.1:8000 --noreload`,
      cwd: backendDirectory,
      url: 'http://127.0.0.1:8000/health/live/',
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        ALLOW_USER_REGISTRATION: 'true',
        CELERY_TASK_ALWAYS_EAGER: 'true',
        DOCUMENT_TASK_LOCK_BACKEND: 'memory',
        EMAIL_BACKEND: 'django.core.mail.backends.locmem.EmailBackend',
        SQLITE_DATABASE_PATH: resolve(backendDirectory, '.stage17-e2e.sqlite3'),
        MEDIA_ROOT: resolve(backendDirectory, '.stage17-e2e-media'),
      },
    },
    {
      // Exercise the optimized production bundle. Vite's development dependency
      // optimizer can reload the whole page when a lazy route discovers a new
      // Element Plus import, which makes an otherwise valid user flow flaky.
      command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 5173',
      cwd: frontendDirectory,
      url: 'http://127.0.0.1:5173/login',
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
})
