// Node 专用配置验收：修改公共端口后代理随之变化，错误不泄露配置内容。
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import { parse, stringify } from 'yaml'
import { loadProjectConfig } from '../project-config.ts'

test('public config drives ports and rejects invalid values without exposing them', () => {
  const folder = mkdtempSync(join(tmpdir(), 'wiki-config-'))
  const path = join(folder, 'config.yaml')
  const original = process.env.WIKI_CONFIG_FILE
  try {
    const config = parse(readFileSync(new URL('../../config.yaml', import.meta.url), 'utf8'))
    config.services.api_port = 18080
    config.services.frontend_port = 15174
    writeFileSync(path, stringify(config))
    process.env.WIKI_CONFIG_FILE = path
    assert.equal(loadProjectConfig().apiUrl, 'http://127.0.0.1:18080')
    assert.equal(loadProjectConfig().port, 15174)
    config.services.api_host = '::'
    writeFileSync(path, stringify(config))
    assert.equal(loadProjectConfig().apiUrl, 'http://[::1]:18080')
    config.services.api_port = 'do-not-log-secret'
    writeFileSync(path, stringify(config))
    assert.throws(loadProjectConfig, error =>
      error.message.includes('services.api_port') && !error.message.includes('do-not-log-secret'))
  } finally {
    if (original === undefined) delete process.env.WIKI_CONFIG_FILE
    else process.env.WIKI_CONFIG_FILE = original
    // 仅删除本测试由 mkdtemp 创建的临时目录。
    assert.ok(resolve(folder).startsWith(resolve(tmpdir()) + sep))
    rmSync(folder, { recursive: true })
  }
})
