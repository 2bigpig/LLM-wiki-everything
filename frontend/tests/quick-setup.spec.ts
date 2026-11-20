import { test, expect, type Page, type APIRequestContext } from '@playwright/test'

// 浏览器服务拥有独立测试数据库；所有测试调用都使用明确注入的离线模型。
test.beforeEach(async ({ request }) => {
  const response = await request.put('/api/model-settings/bindings', { data: { bindings: {} } })
  expect(response.ok()).toBeTruthy()
})
async function open(page: Page) {
  await page.goto('/settings')
  await expect(page.getByRole('region', { name: '快速接入模型' })).toBeVisible()
}
async function fill(page: Page, provider = 'deepseek') {
  const form = page.getByRole('region', { name: '快速接入模型' })
  await form.getByLabel('厂商', { exact: true }).selectOption(provider)
  await form.getByLabel('API Key', { exact: false }).fill('quick-browser-secret')
  await form.getByLabel('模型 ID', { exact: true }).fill('quick-browser-model')
  return form
}
async function seed(request: APIRequestContext) {
  const connection = await (await request.post('/api/model-settings/connections', { data: {
    name: '原默认连接', provider: 'custom', protocol: 'openai', base_url: 'http://localhost:9991/v1', api_key: '',
  } })).json()
  const model = await (await request.post('/api/model-settings/models', { data: {
    connection_id: connection.id, name: '原默认模型', model: 'original-model', kind: 'llm',
  } })).json()
  await request.put('/api/model-settings/bindings', { data: { bindings: { default: model.id, analyze: model.id } } })
  return model.id as string
}

test('首屏字段可见，键盘提交与重复点击只创建一次，刷新保留默认', async ({ page }, info) => {
  await page.setViewportSize({ width: info.project.name === 'desktop' ? 1440 : 390, height: info.project.name === 'desktop' ? 900 : 844 })
  await open(page)
  const form = await fill(page)
  const secret = form.getByLabel('API Key', { exact: true })
  await expect(secret).toHaveAttribute('type', 'password')
  await form.getByRole('button', { name: '显示密钥', exact: true }).click()
  await expect(secret).toHaveAttribute('type', 'text')
  await expect(secret).toHaveValue('quick-browser-secret')
  await form.getByRole('button', { name: '隐藏密钥', exact: true }).press('Enter')
  await expect(secret).toHaveAttribute('type', 'password')
  const submit = form.locator('.quick-actions > .primary')
  for (const control of [form.getByLabel('厂商', { exact: true }), form.getByLabel('API Key'), form.getByLabel('模型 ID'), submit]) {
    const box = await control.boundingBox()
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.y + box!.height).toBeLessThanOrEqual((await page.viewportSize())!.height)
  }
  await expect(page.getByRole('button', { name: '添加连接', exact: true })).not.toBeVisible()
  await page.screenshot({ path: `../data/quick-setup-${info.project.name}.png` })
  let connections = 0, models = 0, probes = 0
  // 让请求保持在途，稳定验证快速重复点击不会再创建，而非依赖响应快慢。
  let releaseProbe!: () => void
  const probeGate = new Promise<void>(resolve => { releaseProbe = resolve })
  await page.route('**/api/model-settings/models/*/test', async route => { await probeGate; await route.continue() })
  page.on('request', request => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/model-settings/connections')) connections++
    if (request.url().endsWith('/model-settings/models')) models++
    if (request.url().endsWith('/test')) probes++
  })
  await form.getByLabel('模型 ID').press('Enter')
  await expect(submit).toBeDisabled()
  await submit.evaluate((button: HTMLButtonElement) => { button.click(); button.click() })
  releaseProbe()
  await expect(form.getByText('当前默认模型', { exact: true })).toBeVisible()
  expect([connections, models, probes]).toEqual([1, 1, 1])
  await page.reload()
  await expect(form.getByRole('heading', { name: 'quick-browser-model', exact: true })).toBeVisible()
  expect(await page.evaluate(() => JSON.stringify(sessionStorage) + JSON.stringify(localStorage))).not.toContain('quick-browser-secret')
  await page.setViewportSize({ width: 320, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

test('测试失败保留旧默认，修正后复用对象，启用失败不重复付费测试并保留覆写', async ({ page, request }) => {
  const original = await seed(request)
  await open(page)
  await page.getByRole('button', { name: '更换模型', exact: true }).click()
  const form = await fill(page)
  let probes = 0, connections = 0, models = 0, bindingFail = true
  page.on('request', r => {
    if (r.method() === 'POST' && r.url().endsWith('/connections')) connections++
    if (r.method() === 'POST' && r.url().endsWith('/models')) models++
  })
  await page.route('**/api/model-settings/models/*/test', async route => {
    probes++
    if (probes === 1) await route.fulfill({ json: { succeeded: false, detail: '模型不支持 JSON' } })
    else await route.continue()
  })
  await page.route('**/api/model-settings/bindings', async route => {
    if (route.request().method() === 'PUT' && bindingFail) { bindingFail = false; await route.fulfill({ status: 503, json: { detail: '用途保存暂时失败' } }) }
    else await route.continue()
  })
  await form.getByRole('button', { name: '测试并启用' }).click()
  await expect(form.getByRole('alert')).toContainText('尚未切换默认模型')
  expect((await (await request.get('/api/model-settings')).json()).bindings.default).toBe(original)
  await expect(form.getByLabel('API Key')).toHaveValue('')
  await form.getByLabel('模型 ID').fill('corrected-model')
  await form.getByRole('button', { name: '测试并启用' }).click()
  await expect(form.getByRole('alert')).toContainText('用途保存暂时失败')
  await form.getByRole('button', { name: '测试并启用' }).click()
  await expect(form.getByText('当前默认模型', { exact: true })).toBeVisible()
  expect([connections, models, probes]).toEqual([1, 1, 2])
  const result = await (await request.get('/api/model-settings')).json()
  expect(result.bindings.analyze).toBe(original)
  expect(result.bindings.default).not.toBe(original)
  await expect(form).toContainText('部分用途仍使用单独配置')
})

test('保存响应丢失时先核对，不重复创建连接', async ({ page }) => {
  await open(page)
  const form = await fill(page)
  let creates = 0
  await page.route('**/api/model-settings/connections', async route => {
    if (route.request().method() === 'POST') {
      creates++; await route.fetch(); await route.abort('failed')
    } else await route.continue()
  })
  await form.getByRole('button', { name: '测试并启用' }).click()
  await expect(form.getByRole('alert')).toContainText('保存结果尚未确认')
  await expect(form.getByRole('button', { name: '测试并启用' })).toBeDisabled()
  await form.getByRole('button', { name: '核对保存结果' }).click()
  await expect(form.getByRole('alert')).toContainText('已确认保存结果')
  await form.getByRole('button', { name: '测试并启用' }).click()
  await expect(form.getByText('当前默认模型', { exact: true })).toBeVisible()
  expect(creates).toBe(1)
})

test('本地免密、自定义地址和 Azure 必填项直接呈现', async ({ page }) => {
  await open(page)
  const form = page.getByRole('region', { name: '快速接入模型' })
  await form.getByLabel('厂商', { exact: true }).selectOption('custom')
  await expect(form.getByLabel('服务地址', { exact: true })).toBeVisible()
  await form.getByLabel('厂商', { exact: true }).selectOption('azure')
  await expect(form.getByLabel('服务地址', { exact: true })).toHaveValue('')
  await expect(form.getByLabel('Azure API 版本')).toBeVisible()
  await expect(form.getByLabel('API Key')).toHaveAttribute('required', '')
  await form.getByLabel('厂商', { exact: true }).selectOption('ollama')
  await expect(form.getByLabel('API Key')).not.toHaveAttribute('required')
  await form.getByLabel('模型 ID').fill('local-test-model')
  await form.getByRole('button', { name: '测试并启用' }).click()
  await expect(form.getByRole('heading', { name: 'local-test-model', exact: true })).toBeVisible()
})

test('不携带口令即可打开设置，刷新后仍直接配置', async ({ page }) => {
  await page.goto('/settings')
  await expect(page.getByRole('region', { name: '快速接入模型' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '解锁配置', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('本地访问口令', { exact: true })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: '测试并启用', exact: true })).toBeVisible()
})
