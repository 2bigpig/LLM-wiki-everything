import { defineConfig, devices } from '@playwright/test'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { loadProjectConfig } from './project-config'

const config = loadProjectConfig()
const frontendHost = config.host === '0.0.0.0' ? '127.0.0.1' : config.host === '::' ? '::1' : config.host
const frontendUrl = `http://${frontendHost.includes(':') ? `[${frontendHost}]` : frontendHost}:${config.port}`

const windowsPython = resolve('../.venv/Scripts/python.exe')
const unixPython = resolve('../.venv/bin/python')
const python = process.env.PYTHON_BIN || (existsSync(windowsPython) ? windowsPython : existsSync(unixPython) ? unixPython : 'python')
const systemChrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || (process.platform === 'win32' && existsSync(systemChrome) ? systemChrome : undefined)

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  outputDir: '../data/playwright-results',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: { baseURL: frontendUrl, trace: 'retain-on-failure',
    extraHTTPHeaders: { 'X-API-Key': 'browser-test-token' },
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    launchOptions: { executablePath } },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 } } },
  ],
  webServer: [
    { command: `"${python}" ../backend/tests/serve_app.py`, url: config.apiUrl + '/healthz',
      reuseExistingServer: false, timeout: 30000 },
    { command: 'npm run dev', url: frontendUrl,
      reuseExistingServer: false, timeout: 30000 },
  ],
})
