import { expect, test } from '@playwright/test'
import { writeFile, mkdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'

test.skip(!process.env.PULSATE_PROSPECTIVE_ACCEPTANCE, 'Requires the isolated real-model runtime.')
test.setTimeout(900_000)

test('blind prospective candidate investigation', async ({ page }, info) => {
  await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
  await page.goto('/')
  const question = process.env.PULSATE_PROSPECTIVE_QUERY ?? 'Investigate torcetrapib as a CETP drug candidate.'
  await page.getByLabel('Scientific question', { exact: true }).fill(question)
  const initial = page.waitForResponse(r => r.url().endsWith('/research/sessions') && r.request().method() === 'POST', { timeout: 300_000 })
  await page.getByRole('button', { name: 'Start research session', exact: true }).click()
  const response = await initial
  expect(response.ok()).toBeTruthy()
  let session = await response.json()
  await writeFile(info.outputPath('initial-session.json'), JSON.stringify(session, null, 2))
  expect(response.request().postDataJSON().input_references ?? []).toHaveLength(0)
  if (session.status === 'planned') {
    const running = page.waitForResponse(r => r.url().endsWith('/execute'), { timeout: 800_000 })
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    session = await (await running).json()
  }
  await writeFile(info.outputPath('result.json'), JSON.stringify(session, null, 2))
  await page.screenshot({ path: info.outputPath('result.png'), fullPage: true })
  console.log(JSON.stringify({ session: session.session_identifier, status: session.status,
    questions: session.next_questions, steps: session.execution_steps, answer: session.scientist_summary }))
  if (process.env.PULSATE_PROSPECTIVE_REQUIRE_SUCCESS) {
    expect(session.status).toBe('completed')
    expect(session.scientist_result.verification_status).toBe('passed')
    expect(session.execution_steps.every((s: { status: string }) => s.status === 'succeeded')).toBe(true)
    expect(session.scientist_result.methods).toContain('discovery.prospective_assess')
    expect(session.scientist_result.principal_result).toContain('Prospective assessment')
    const visualization = await (await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/visualization`,
      { headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` } })).json()
    await writeFile(info.outputPath('visualization.json'), JSON.stringify(visualization, null, 2))
    expect(visualization.prospective_assessment).toBeTruthy()
    expect(visualization.prospective_assessment.computation_selection.quantum_selected).toBe(false)
    await mkdir(info.outputPath('artifacts'), { recursive: true })
    const manifest = []
    const identifiers = [...new Set<string>([...session.artifact_references.map((r: { artifact_identifier: string }) => r.artifact_identifier),
      ...session.execution_steps.flatMap((s: { output_artifact_identifiers: string[]; evidence_artifact_identifiers: string[] }) => [...s.output_artifact_identifiers, ...s.evidence_artifact_identifiers])])]
    for (const identifier of identifiers) {
      const reference = visualization.export_items.find((r: { artifact_identifier: string }) => r.artifact_identifier === identifier)
      expect(reference, identifier).toBeTruthy()
      const response = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${identifier}`,
        { headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` } })
      expect(response.ok()).toBeTruthy()
      const bytes = await response.body()
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(reference.content_sha256)
      await writeFile(info.outputPath('artifacts', identifier), bytes)
      manifest.push(reference)
    }
    await writeFile(info.outputPath('artifact-manifest.json'), JSON.stringify(manifest, null, 2))
    await expect(page.getByRole('heading', { name: 'Candidate investigation', exact: true })).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
    await expect(page.locator('.viewer-render-error')).toHaveCount(0)
    await page.screenshot({ path: info.outputPath('prospective-result-3d.png'), fullPage: true })
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Candidate investigation', exact: true })).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
    await expect(page.locator('.viewer-render-error')).toHaveCount(0)
    await page.screenshot({ path: info.outputPath('reopened-result.png'), fullPage: true })
    const frozen = JSON.stringify({ schema: 'pulsate.frozen-prospective-session/v1', frozen_at: new Date().toISOString(),
      session, visualization, artifact_manifest: manifest, source_commit: process.env.PULSATE_SOURCE_COMMIT,
      source_archive_sha256: process.env.PULSATE_SOURCE_ARCHIVE_SHA256 }, null, 2)
    await writeFile(info.outputPath('frozen-prospective-result.json'), frozen, { flag: 'wx' })
    const sha256 = createHash('sha256').update(frozen).digest('hex')
    await writeFile(info.outputPath('frozen-result-sha256.json'), JSON.stringify({ sha256, session_identifier: session.session_identifier }, null, 2), { flag: 'wx' })
    console.log(JSON.stringify({ frozen_result_sha256: sha256, session: session.session_identifier }))
  }
})
