import { expect, test } from '@playwright/test'

test('调度开关、结束策略、跨午夜和刷新保存可用，配置不触发模型调用', async ({ page, request }, info) => {
  const original = (await (await request.get('/api/model-settings/schedule')).json()).policy
  let probes = 0
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/test')) probes++ })
  try {
    await page.goto('/settings')
    const card = page.getByRole('region', { name: 'LLM 执行时间', exact: true })
    await expect(card.getByLabel('按每日优惠时段运行', { exact: true })).not.toBeChecked()
    await card.getByLabel('按每日优惠时段运行', { exact: true }).check()
    await expect(card.getByLabel('结束时的处理方式', { exact: true })).toHaveValue('finish_batch')
    await card.getByLabel('开始时间', { exact: true }).fill('22:00')
    await card.getByLabel('结束时间', { exact: true }).fill('06:00')
    await card.getByLabel('时区', { exact: true }).fill('Asia/Shanghai')
    await card.getByLabel('结束时的处理方式', { exact: true }).selectOption('wait_next_window')
    await card.getByRole('button', { name: '保存执行时间', exact: true }).click()
    await expect(page.getByText('LLM 执行时间已保存，等待任务将按新策略调度。', { exact: true })).toBeVisible()
    await page.reload()
    await expect(card.getByLabel('按每日优惠时段运行', { exact: true })).toBeChecked()
    await expect(card.getByLabel('开始时间', { exact: true })).toHaveValue('22:00')
    await expect(card.getByLabel('结束时间', { exact: true })).toHaveValue('06:00')
    await expect(card.getByLabel('结束时的处理方式', { exact: true })).toHaveValue('wait_next_window')
    await page.evaluate(() => window.scrollTo(0, 0))
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(0)
    await page.screenshot({ path: `../data/llm-schedule-${info.project.name}.png`, fullPage: true })
    await page.setViewportSize({ width: 320, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await card.getByLabel('按每日优惠时段运行', { exact: true }).uncheck()
    await card.getByRole('button', { name: '保存执行时间', exact: true }).click()
    await expect(card.getByText('立即处理', { exact: true })).toBeVisible()
    expect(probes).toBe(0)
  } finally {
    await request.put('/api/model-settings/schedule', { data: original })
  }
})
