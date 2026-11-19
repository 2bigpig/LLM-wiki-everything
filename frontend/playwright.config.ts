import { defineConfig, devices } from '@playwright/test'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const windowsPython = resolve('../.venv/Scripts/python.exe')
const unixPython = resolve('../.venv/bin/python')
const python = process.env.PYTHON_BIN || (existsSync(windowsPython) ? windowsPython : existsSync(unixPython) ? unixPython : 'python')
const systemChrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || (process.platform === 'win32' && existsSync(systemChrome) ? systemChrome : undefined)

export default defineConfig({
  testDir: './tests',
  outputDir: '../data/playwright-results',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure',
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    launchOptions: { executablePath } },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 } } },
  ],
  webServer: [
    { command: `"${python}" ../backend/tests/serve_app.py`, url: 'http://127.0.0.1:8000/healthz',
      reuseExistingServer: false, timeout: 30000 },
    { command: 'npm run dev -- --port 5173 --strictPort', url: 'http://127.0.0.1:5173',
      reuseExistingServer: false, timeout: 30000 },
  ],
})
