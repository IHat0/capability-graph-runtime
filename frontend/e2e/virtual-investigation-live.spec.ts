import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

test.skip(!process.env.PULSATE_VIRTUAL_INVESTIGATION_ACCEPTANCE, 'Requires approved isolated real-model runtime.')
test.setTimeout(900_000)

test('unrelated real-model virtual investigation and reopening', async ({ page }, info) => {
  // No default candidate: the held-out investigation must never run accidentally.
  const query = process.env.PULSATE_UNRELATED_INVESTIGATION_QUERY
  expect(query, 'Explicit unrelated validation query is required').toBeTruthy()
  const token = process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!
  await page.addInitScript(value => { window.pulsateAccessTokenProvider = async () => value }, token)
  await page.goto('/')
  let session
  if (process.env.PULSATE_REOPEN_VALIDATION_SESSION) {
    const id = process.env.PULSATE_REOPEN_VALIDATION_SESSION
    await page.getByText('Reopen a research session', { exact: true }).click()
    await page.getByLabel('Saved session identifier').fill(id)
    const response = page.waitForResponse(r => r.url().endsWith(`/research/sessions/${id}`) && r.request().method() === 'GET')
    await page.getByRole('button', { name: 'Open session', exact: true }).click()
    const result = await response
    expect(result.ok()).toBe(true)
    session = await result.json()
  } else {
    await page.getByLabel('Scientific question', { exact: true }).fill(query!)
    const response = page.waitForResponse(r => r.url().endsWith('/research/sessions') && r.request().method() === 'POST', { timeout: 300_000 })
    await page.getByRole('button', { name: 'Start research session', exact: true }).click()
    const initial = await response
    expect(initial.ok()).toBe(true)
    session = await initial.json()
    expect(initial.request().postDataJSON().input_references ?? []).toHaveLength(0)
  }
  await writeFile(info.outputPath('initial-session.json'), JSON.stringify(session, null, 2))
  if (session.status === 'planned') {
    const result = page.waitForResponse(r => r.url().endsWith('/execute'), { timeout: 800_000 })
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    session = await (await result).json()
  }
  await writeFile(info.outputPath('session.json'), JSON.stringify(session, null, 2))
  await page.screenshot({ path: info.outputPath('result.png'), fullPage: true })
  console.log(JSON.stringify({ session: session.session_identifier, status: session.status,
    questions: session.next_questions, answer: session.scientist_summary }))
  expect(session.status).toBe('completed')
  expect(session.scientist_result.verification_status).toBe('passed')
  expect(session.scientist_result.methods).toContain('discovery.virtual_investigate')
  expect(session.scientist_result.methods).toContain('discovery.virtual_investigation_verify')
  const visualizationResponse = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/visualization`,
    { headers: { Authorization: `Bearer ${token}` } })
  expect(visualizationResponse.ok()).toBe(true)
  const visualization = await visualizationResponse.json()
  const investigation = visualization.prospective_assessment.virtual_investigation
  expect(investigation).toBeTruthy()
  if (process.env.PULSATE_EXPECTED_CUTOFF_INSTANT) {
    expect(Date.parse(investigation.prospective_policy.cutoff_instant)).toBe(Date.parse(process.env.PULSATE_EXPECTED_CUTOFF_INSTANT))
  }
  expect(investigation.computation_selection.quantum_selected).toBe(false)
  expect(investigation.candidates.every((c: { inference_levels: { clinical: boolean } }) => !c.inference_levels.clinical)).toBe(true)
  if (process.env.PULSATE_EXPECT_ACTIVITY_PANEL_COUNT) {
    for (const candidate of investigation.candidates) {
      const prediction=candidate.target_activity_prediction
      expect(prediction.targets).toHaveLength(Number(process.env.PULSATE_EXPECT_ACTIVITY_PANEL_COUNT))
      if (process.env.PULSATE_EXPECT_MINIMUM_ACCEPTED_BINDING) {
        expect(prediction.targets.filter((row:{status:string})=>row.status==='predicted').length)
          .toBeGreaterThanOrEqual(Number(process.env.PULSATE_EXPECT_MINIMUM_ACCEPTED_BINDING))
      }
      expect(candidate.general_safety_panel.targets).toHaveLength(prediction.targets.length)
      expect(prediction.request.graph.inchikey).toBe(candidate.identity.inchikey)
      for (const row of prediction.targets) {
        if (row.status==='predicted') {
          expect(row.research_gate.accepted).toBe(true)
          expect(row.applicability.status).toBe('in_domain')
          expect(row.applicability.training_graph_seen).toBe(false)
          expect(row.value_umol_l).toBeGreaterThan(0)
          expect(row.interval_umol_l[0]).toBeLessThanOrEqual(row.value_umol_l)
          expect(row.interval_umol_l[1]).toBeGreaterThanOrEqual(row.value_umol_l)
          expect(row.concentration_basis).toBe('assay_nominal')
          expect(row.functional_direction).toBeNull()
          expect(row.tissue_potency_transfer_supported).toBe(false)
        } else {
          expect(row.value_umol_l).toBeUndefined()
          expect(row.reason).toBeTruthy()
        }
      }
    }
    await expect(page.getByRole('heading',{name:'Candidate binding hypotheses · not measured potency',exact:true})).toBeVisible({timeout:45_000})
  }
  await writeFile(info.outputPath('visualization.json'), JSON.stringify(visualization, null, 2))
  await mkdir(info.outputPath('artifacts'), { recursive: true })
  const manifest = [...visualization.export_items]
  // Bounded concurrent read-only exports avoid turning network latency into
  // a scientific rerun. Preserve order and verify every exact artifact hash.
  const download = async (reference: typeof manifest[number]) => {
    const result = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${reference.artifact_identifier}`,
      { headers: { Authorization: `Bearer ${token}` } })
    expect(result.ok(), reference.artifact_identifier).toBe(true)
    const bytes = await result.body()
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(reference.content_sha256)
    await writeFile(info.outputPath('artifacts', reference.artifact_identifier), bytes)
  }
  for (let first = 0; first < manifest.length; first += 4) {
    await Promise.all(manifest.slice(first, first + 4).map(download))
  }
  await writeFile(info.outputPath('artifact-manifest.json'), JSON.stringify(manifest, null, 2))
  await expect(page.getByRole('heading', { name: 'Virtual Investigation', exact: true })).toBeVisible({ timeout: 45_000 })
  await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
  await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
  await expect(page.locator('.viewer-render-error')).toHaveCount(0)
  await page.screenshot({ path: info.outputPath('verified-investigation.png'), fullPage: true })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Virtual Investigation', exact: true })).toBeVisible({ timeout: 45_000 })
  await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
  await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
  await page.screenshot({ path: info.outputPath('reopened-investigation.png'), fullPage: true })
  await page.getByRole('heading', { name: 'Virtual Investigation', exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('virtual-investigation-evidence.png'), fullPage: true })
  if (process.env.PULSATE_EXPECT_ACTIVITY_PANEL_COUNT) {
    await page.getByRole('heading',{name:'Candidate binding hypotheses · not measured potency',exact:true}).scrollIntoViewIfNeeded()
    await page.screenshot({path:info.outputPath('candidate-binding-evidence.png'),fullPage:true})
    if (process.env.PULSATE_EXPECT_MINIMUM_ACCEPTED_BINDING) {
      const binding=page.getByRole('region',{name:'Binding estimates and validation',exact:true})
      await expect(binding).toHaveAttribute('tabindex','0')
      await binding.getByRole('row').filter({hasText:'Predicted Ki:'}).first().scrollIntoViewIfNeeded()
      await binding.focus()
      await binding.press('ArrowRight')
      await page.screenshot({path:info.outputPath('accepted-binding-evidence.png'),fullPage:true})
    }
  }
  await page.getByRole('heading', { name: 'Functional models', exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('functional-model-evidence.png'), fullPage: true })
  const frozen = JSON.stringify({ schema: 'pulsate.unrelated-virtual-investigation-validation/v1',
    query, session, visualization, artifact_manifest: manifest, source_archive_sha256: process.env.PULSATE_SOURCE_ARCHIVE_SHA256 }, null, 2)
  await writeFile(info.outputPath('frozen-unrelated-result.json'), frozen, { flag: 'wx' })
  console.log(JSON.stringify({ frozen_sha256: createHash('sha256').update(frozen).digest('hex'), artifacts: manifest.length }))
})
