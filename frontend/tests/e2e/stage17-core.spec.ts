import { expect, test } from '@playwright/test'

test('stage17 isolated enterprise knowledge flow', async ({ page }) => {
  const suffix = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  const username = `stage17_e2e_${suffix}`
  const email = `${username}@example.test`
  const password = 'Stage17_safe_password!'
  const knowledgeName = `stage17_e2e_kb_${suffix}`
  const applicationName = `stage17_e2e_app_${suffix}`

  await test.step('register isolated user and enter protected workspace', async () => {
    await page.goto('/register')
    await page.getByLabel('用户名').fill(username)
    await page.getByLabel('邮箱').fill(email)
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByLabel('确认密码').fill(password)
    await page.getByRole('button', { name: '创建账号并进入工作台' }).click()
    await expect(page).toHaveURL(/\/dashboard/)
  })

  await test.step('create knowledge base', async () => {
    await page.getByRole('button', { name: /知识空间/ }).click()
    await expect(page).toHaveURL(/\/knowledge$/)
    await page.getByRole('button', { name: '新建知识库' }).click()
    await page.getByLabel('名称').fill(knowledgeName)
    await page.getByLabel('描述').fill('stage17 自动化隔离数据')
    await page.getByRole('button', { name: '创建', exact: true }).click()
    await expect(page.getByRole('heading', { name: knowledgeName })).toBeVisible()
    await page.getByRole('heading', { name: knowledgeName }).click()
    await expect(page).toHaveURL(/\/knowledge\/\d+/)
  })

  await test.step('upload, process, preview and reprocess a document', async () => {
    await page.locator('[data-testid="document-upload"] input[type=file]').setInputFiles({
      name: 'stage17_e2e_manual.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('错误码 E1701 表示连接超时。处理步骤：检查网络，然后重启服务。'),
    })
    await expect(page.getByText('处理成功').first()).toBeVisible({ timeout: 60_000 })
    await page.getByRole('button', { name: '查看切片' }).click()
    await expect(page.getByTestId('paragraph-drawer')).toContainText('错误码 E1701')
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: '重新处理' }).click()
    await expect(page.getByText(/后台处理队列|处理成功/).first()).toBeVisible()
  })

  await test.step('stream answer, citations and URL-backed conversation restore', async () => {
    await page.getByTestId('chat-input').fill('错误码 E1701 如何处理？')
    await page.getByTestId('chat-send').click()
    await expect(page.getByTestId('chat-message-assistant')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('chat-references')).toBeVisible({ timeout: 60_000 })
    await expect(page).toHaveURL(/conversation=\d+/)
    const conversationUrl = page.url()
    await page.reload()
    await expect(page).toHaveURL(conversationUrl)
    await expect(page.getByText('错误码 E1701 如何处理？')).toBeVisible()
  })

  await test.step('create and isolate a second conversation', async () => {
    await page.getByRole('button', { name: '历史会话' }).click()
    await page.getByTestId('new-conversation').click()
    await expect(page).not.toHaveURL(/conversation=/)
    await expect(page.getByTestId('chat-empty')).toBeVisible()
    await page.getByTestId('chat-input').fill('第二个会话的问题')
    await page.getByTestId('chat-send').click()
    await expect(page).toHaveURL(/conversation=\d+/, { timeout: 60_000 })
    await page.getByRole('button', { name: '历史会话' }).click()
    await expect(page.locator('[data-testid^="conversation-"]')).toHaveCount(2)
  })

  await test.step('model secrets remain blank and application can be created', async () => {
    await page.goto('/model-configs')
    for (const input of await page.locator('input[type=password]').all()) await expect(input).toHaveValue('')
    await page.getByRole('button', { name: /AI 应用/ }).click()
    await page.getByRole('button', { name: '创建应用' }).click()
    await page.getByLabel('应用名称').fill(applicationName)
    await page.getByLabel('应用说明').fill('stage17 自动化隔离应用')
    await page.getByRole('button', { name: '创建并配置' }).click()
    await expect(page).toHaveURL(/\/applications\/\d+$/)
  })

  await test.step('logout revokes protected navigation', async () => {
    await page.getByLabel('退出登录').click()
    await expect(page).toHaveURL(/\/login/)
    await page.goto('/knowledge')
    await expect(page).toHaveURL(/\/login/)
  })
})
