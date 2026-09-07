import { expect, test } from '@playwright/test'
import path from 'node:path'
import { writeFile } from 'node:fs/promises'

test.skip(!process.env.PULSATE_DRUG_ACCEPTANCE_ROOT, 'Requires isolated real API and public structure files.')
test.setTimeout(300_000)

const questions = [
  'Compare these compounds for this target and tell me which should be tested next.',
  'Which of these candidate molecules is most promising for this protein target?',
  'Where is the likely binding pocket on this protein and how well does this ligand fit it?',
]

for (const [index, question] of questions.entries()) {
  test('real drug-discovery question ' + (index + 1), async ({ page }, testInfo) => {
    await page.addInitScript((token) => {
      window.pulsateAccessTokenProvider = async () => token
    }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
    const root = process.env.PULSATE_DRUG_ACCEPTANCE_ROOT!
    await page.goto('/')
    await page.getByLabel('Scientific question', { exact: true }).fill(question)
    await page.locator('input[type=file]').nth(1).setInputFiles(path.join(root, '3MXF.pdb'))
    await page.locator('input[type=file]').nth(2).setInputFiles(
      (index === 2 ? ['JQ1.sdf'] : ['JQ1.sdf', 'EAM.sdf']).map(name => path.join(root, name)),
    )
    await page.getByRole('button', { name: 'Start research session' }).click()
    await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeVisible({ timeout: 150_000 })
    const responsePromise = page.waitForResponse(response => response.url().endsWith('/execute'), { timeout: 200_000 })
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    const response = await responsePromise
    const result = await response.json()
    await writeFile(testInfo.outputPath('verified-scientist-answer.json'), JSON.stringify({ question, result }, null, 2))
    await testInfo.attach('verified-scientist-answer', { body: JSON.stringify({ question, result }, null, 2), contentType: 'application/json' })
    expect(result.status).toBe('completed')
    expect(result.scientist_result.verification_status).toBe('passed')
    expect(result.execution_steps.every((step: {status: string}) => step.status === 'succeeded')).toBe(true)
    expect(result.scientist_result.candidate_ranking.length).toBe(index === 2 ? 1 : 2)
    await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible()
    await expect(page.getByText(/JQ1.*Vina/).first()).toBeVisible()
    await page.getByRole('button', { name: 'Focus in 3D', exact: true }).first().click()
    await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
    await expect(page.locator('.viewer-render-error')).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('drug-discovery-answer.png'), fullPage: true })
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible({ timeout: 30_000 })
  })
}
