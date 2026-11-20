import { expect, test } from '@playwright/test'

test('页面导航保留全部入口，小屏到桌面均无整页横向溢出', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  for (const label of ['知识 Wiki', '整理进度', '知识图谱', '发现与回顾', '设置与用量', '我的收藏']) {
    const menu = page.getByRole('button', { name: '打开导航', exact: true })
    if (await menu.isVisible()) await menu.click()
    await page.getByRole('link', { name: label, exact: true }).click()
    await expect(page.getByRole('heading', { level: 1 })).toContainText(label)
    await expect(page.getByRole('dialog', { name: '页面导航' })).not.toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  }
  // 同一内容在 320、768、1024、1440 四个断点复查，避免只适配一张截图。
  for (const width of [320, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  }
  expect(errors).toEqual([])
})

test('搜索快捷键、录入焦点、导航退出与主按钮对比度可用', async ({ page }) => {
  await page.goto('/')
  const search = page.getByLabel('搜索知识库', { exact: true })
  await page.keyboard.press('Control+k')
  await expect(search).toBeFocused()
  const capture = page.getByRole('button', { name: '记录内容', exact: true })
  await capture.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog').filter({ has: page.getByLabel('内容', { exact: true }) })
  await expect(dialog.getByLabel('内容', { exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(capture).toBeFocused()
  const menu = page.getByRole('button', { name: '打开导航', exact: true })
  if (await menu.isVisible()) {
    await menu.click()
    const navigation = page.getByRole('dialog', { name: '页面导航' })
    await expect(navigation).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(navigation).not.toBeVisible()
    await expect(menu).toBeFocused()
  }
  const contrast = await capture.evaluate(element => {
    const style = getComputedStyle(element)
    const luminance = (color: string) => {
      const rgb = color.match(/\d+/g)!.slice(0, 3).map(Number).map(value => {
        const s = value / 255
        return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4
      })
      return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
    }
    const values = [luminance(style.color), luminance(style.backgroundColor)].sort((a, b) => a - b)
    return (values[1] + .05) / (values[0] + .05)
  })
  expect(contrast).toBeGreaterThanOrEqual(4.5)
})
