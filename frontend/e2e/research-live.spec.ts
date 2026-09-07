import { expect, test } from '@playwright/test'

test.skip(!process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN, 'Requires the isolated real research API.')
test.setTimeout(90_000)

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    window.pulsateAccessTokenProvider = async () => token
  }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
})

test('scientist uploads coordinates, executes, inspects evidence and reopens the result', async ({ page }, testInfo) => {
  const pdb = [
    'HETATM    1  C1  MOL A   1       0.000   0.000   0.000  1.00  0.00           C',
    'HETATM    2  C2  MOL A   1       1.500   0.000   0.000  1.00  0.00           C',
    'HETATM    3  O1  MOL A   1       2.800   0.000   0.000  1.00  0.00           O',
    'END', '',
  ].join('\n')
  await page.goto('/')
  await page.getByLabel('Scientific question', { exact: true }).fill('Analyze this structure and report its coordinate inventory.')
  await page.locator('input[type=file]').first().setInputFiles({
    name: 'coordinate-inventory.pdb', mimeType: 'chemical/x-pdb', buffer: Buffer.from(pdb),
  })
  await page.getByRole('button', { name: 'Start research session' }).click()
  await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeVisible()
  const sessionUrl = page.url()
  expect(sessionUrl).toContain('research=research-session-')
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/execute'))
  await page.getByRole('button', { name: 'Run research', exact: true }).click()
  const response = await responsePromise
  const result = await response.json()
  await testInfo.attach('real-execution-response', { body: JSON.stringify(result, null, 2), contentType: 'application/json' })
  expect(response.status()).toBe(200)
  expect(result.status).toBe('completed')
  expect(result.scientist_result.verification_status).toBe('passed')
  expect(result.execution_steps.every((step: {status: string}) => step.status === 'succeeded')).toBe(true)
  await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Assumptions used' })).toBeVisible()
  await expect(page.getByText(/3 atoms, 1 residues, and 1 chains/).first()).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Calculation progress' })).toBeVisible()
  await page.getByRole('button', { name: 'molecular structure View' }).first().click()
  await expect(page.locator('.loaded-workspace')).toBeVisible()
  await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('.viewer-render-error')).toHaveCount(0)
  await expect(page.getByText('Partial data unavailable')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('research-result.png'), fullPage: true })
  await page.getByRole('button', { name: 'New question' }).click()
  await expect(page.getByLabel('Scientific question', { exact: true })).toBeVisible()
  await page.getByText('Reopen a research session', { exact: true }).click()
  await page.getByLabel('Saved session identifier').fill(result.session_identifier)
  await page.getByRole('button', { name: 'Open session', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible()
})

test('scientist receives computed molecular descriptors from a SMILES attachment', async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByLabel('Scientific question', { exact: true }).fill('Analyze this molecule and report its molecular descriptors.')
  await page.locator('input[type=file]').first().setInputFiles({
    name: 'molecule.smi', mimeType: 'chemical/x-daylight-smiles', buffer: Buffer.from('CCO'),
  })
  await page.getByRole('button', { name: 'Start research session' }).click()
  await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeVisible()
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/execute'))
  await page.getByRole('button', { name: 'Run research', exact: true }).click()
  const response = await responsePromise
  const result = await response.json()
  await testInfo.attach('real-molecular-result', { body: JSON.stringify(result, null, 2), contentType: 'application/json' })
  expect(result.status).toBe('completed')
  expect(result.scientist_result.verification_status).toBe('passed')
  await expect(page.getByText(/C2H6O.*3 heavy atoms/).first()).toBeVisible()
})
