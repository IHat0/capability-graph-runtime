// Unrelated development cases only. Never defaults to the held-out challenge.
import { expect, test } from '@playwright/test'
import { writeFile, mkdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'

test.skip(!process.env.PULSATE_OFF_TARGET_ACCEPTANCE, 'Requires real isolated engines and provider.')
test.setTimeout(1_200_000)
let computedScreenCount = 0
test.afterAll(() => {
  if (process.env.PULSATE_OFF_TARGET_REQUIRE_COMPUTATION) expect(computedScreenCount,
    'At least one unrelated real alternative-target calculation must succeed; unsupported cases are not numerical proof.').toBeGreaterThan(0)
})

for (const question of [
  'Investigate I-BET762 as a human BRD4 bromodomain 1 drug candidate.',
  'Investigate erlotinib as a human EGFR kinase domain drug candidate.',
]) test(question, async ({ page }, info) => {
  await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
  await page.goto('/')
  await page.getByLabel('Scientific question', { exact: true }).fill(question)
  const initial = page.waitForResponse(r => r.url().endsWith('/research/sessions') && r.request().method() === 'POST', { timeout: 300_000 })
  await page.getByRole('button', { name: 'Start research session', exact: true }).click()
  const response = await initial
  expect(response.ok()).toBeTruthy()
  let session = await response.json()
  await writeFile(info.outputPath('initial-session.json'), JSON.stringify(session, null, 2))
  if (session.status === 'planned') {
    const pending = page.waitForResponse(r => r.url().endsWith('/execute'), { timeout: 950_000 })
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    session = await (await pending).json()
  }
  await writeFile(info.outputPath('session.json'), JSON.stringify(session, null, 2))
  console.log(JSON.stringify({ session: session.session_identifier, status: session.status, questions: session.next_questions }))
  expect(session.status).toBe('completed')
  expect(session.scientist_result.verification_status).toBe('passed')
  const visualization = await (await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/visualization`,
    { headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` } })).json()
  await writeFile(info.outputPath('visualization.json'), JSON.stringify(visualization, null, 2))
  const assessment = visualization.prospective_assessment
  expect(assessment.computation_selection.quantum_selected).toBe(false)
  expect(assessment.candidates.every((c: { alternative_targets: unknown }) => c.alternative_targets)).toBe(true)
  await mkdir(info.outputPath('artifacts'), { recursive: true })
  for (const ref of visualization.export_items) {
    const result = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${ref.artifact_identifier}`,
      { headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` } })
    expect(result.ok(), ref.artifact_identifier).toBeTruthy()
    const bytes = await result.body()
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(ref.content_sha256)
    await writeFile(info.outputPath('artifacts', ref.artifact_identifier), bytes)
  }
  const targets = assessment.candidates.flatMap((c: { alternative_targets: { targets: Array<{ status: string }> } }) => c.alternative_targets.targets)
  console.log(JSON.stringify({ session: session.session_identifier, nominated: targets.length,
    computed: targets.filter((t: { status: string }) => t.status === 'computed').length, targets }))
  computedScreenCount += targets.filter((t: { status: string }) => t.status === 'computed').length
  await expect(page.getByText('Alternative-target investigation — hypotheses, not toxicity findings', { exact: true })).toBeVisible()
  await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
  await page.screenshot({ path: info.outputPath('result.png'), fullPage: true })
  await page.reload()
  await expect(page.getByText('Alternative-target investigation — hypotheses, not toxicity findings', { exact: true })).toBeVisible({ timeout: 45_000 })
  await page.screenshot({ path: info.outputPath('reopened.png'), fullPage: true })
})
