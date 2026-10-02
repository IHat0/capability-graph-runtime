import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'

test.skip(!process.env.PULSATE_PBPK_ACCEPTANCE, 'Requires configured native engine and a real language model.')
test.setTimeout(600_000)

test('native virtual organism with evidence and saved session', async ({ page }, info) => {
  test.skip(Boolean(process.env.PULSATE_REOPEN_ONLY), 'Read-only existing-session inspection selected.')
  const question = process.env.PULSATE_PBPK_QUERY
  if (!question) throw new Error('Supply an explicit unrelated research scenario; there is no default held-out candidate.')
  await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
  await page.goto('/')
  const suppliedInputs: Array<{ label: string; path: string }> = JSON.parse(process.env.PULSATE_PBPK_INPUTS ?? '[]')
  for (const input of suppliedInputs) await page.getByLabel(input.label, { exact: true }).setInputFiles(input.path)
  await page.getByLabel('Scientific question', { exact: true }).fill(question)
  const started = page.waitForResponse(r => r.url().endsWith('/research/sessions') && r.request().method() === 'POST', { timeout: 200_000 })
  await page.getByRole('button', { name: 'Start research session', exact: true }).click()
  let session = await (await started).json()
  await writeFile(info.outputPath('initial-session.json'), JSON.stringify(session, null, 2))
  if (session.status === 'awaiting_clarification' && process.env.PULSATE_PBPK_REPLY) {
    const previousIdentifier = session.session_identifier
    await page.getByLabel('Your reply', { exact: true }).fill(process.env.PULSATE_PBPK_REPLY)
    const continued = page.waitForResponse(r => r.url().includes(`/research/sessions/${previousIdentifier}`) && r.request().method() === 'POST', { timeout: 200_000 })
    await page.getByRole('button', { name: 'Continue', exact: true }).click()
    session = await (await continued).json()
    expect(session.session_identifier).toBe(previousIdentifier)
    await writeFile(info.outputPath('continued-session.json'), JSON.stringify(session, null, 2))
  }
  if (session.status === 'planned') {
    const executed = page.waitForResponse(r => r.url().endsWith('/execute'), { timeout: 300_000 })
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    session = await (await executed).json()
  }
  await writeFile(info.outputPath('session.json'), JSON.stringify(session, null, 2))
  await page.screenshot({ path: info.outputPath('result.png'), fullPage: true })
  console.log(JSON.stringify({ session: session.session_identifier, status: session.status, questions: session.next_questions, answer: session.scientist_result?.principal_result }))
  expect(session.status).toBe('completed')
  expect(session.scientist_result.verification_status).toBe('passed')
  expect(session.execution_steps.every((s: { status: string }) => s.status === 'succeeded')).toBe(true)
  const response = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/visualization`, { headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` } })
  expect(response.ok()).toBeTruthy()
  const visualization = await response.json()
  await writeFile(info.outputPath('visualization.json'), JSON.stringify(visualization, null, 2))
  expect(visualization.virtual_organism).toBeTruthy()
  expect(visualization.virtual_organism.computation_selection.quantum_selected).toBe(false)
  if (process.env.PULSATE_ADME_ACCEPTANCE) {
    await expect(page.getByRole('region', { name: 'ADME Parameterization' })).toBeVisible()
    const adme = visualization.virtual_organism.adme_parameterization
    expect(adme.verification.passed).toBe(true)
    expect(adme.prediction.status).toBe('predicted')
    const predictions = adme.prediction.dossiers.flatMap((d: { adme_parameters: Record<string, { prediction: { model_sha256: string } }> }) => Object.values(d.adme_parameters))
    expect(predictions.length).toBeGreaterThan(0)
    expect(predictions.every((p: { prediction: { model_sha256: string } }) => p.prediction.model_sha256.length === 64)).toBe(true)
    if (process.env.PULSATE_ADME_ACCEPTANCE === 'graph') {
      expect(visualization.virtual_organism.candidate.pubchem_cid).toBeNull()
      expect(visualization.virtual_organism.runs).toHaveLength(0)
      expect(visualization.virtual_organism.status).toBe('insufficient_parameterization')
      expect(adme.prediction.dossiers.every((d: { adme_parameters: Record<string, { prediction: { applicability: { training_graph_seen: boolean } } }> }) =>
        Object.values(d.adme_parameters).every(p => !p.prediction.applicability.training_graph_seen))).toBe(true)
    }
    if (process.env.PULSATE_ADME_ACCEPTANCE === 'conditional') {
      expect(visualization.virtual_organism.drug_parameter_uncertainty.scenarios.length).toBeGreaterThan(0)
      if (process.env.PULSATE_PBPK_EXPECT_DOSES) {
        const doses = process.env.PULSATE_PBPK_EXPECT_DOSES.split(',').map(Number)
        expect(visualization.virtual_organism.request.dose_sweep).toEqual(doses)
        expect(visualization.virtual_organism.drug_parameter_uncertainty.scenarios.filter((s: { scenario_kind: string }) => s.scenario_kind === 'dose_sweep')).toHaveLength(doses.length - 1)
        expect(visualization.virtual_organism.drug_parameter_uncertainty.dose_analysis).toHaveLength(1)
      }
      await page.getByLabel('Inspect independently simulated scenario').selectOption('0')
    }
  }
  await expect(page.getByRole('article', { name: 'Virtual Organism result' })).toBeVisible({ timeout: 30_000 })
  if (visualization.virtual_organism.runs.length) {
    await expect(page.getByRole('img', { name: /concentration time course/ }).first()).toBeVisible()
    await page.getByLabel('Inspect modeled compartment').selectOption({ label: 'Liver · Tissue' })
    await expect(page.getByRole('img', { name: /Liver Tissue concentration time course/ })).toBeVisible()
    if (visualization.virtual_organism.runs.some((r: { species: string }) => r.species === 'Rat')) {
      await page.getByRole('button', { name: 'Rat', exact: true }).click()
      await expect(page.getByRole('heading', { name: 'Virtual rat', exact: true })).toBeVisible()
    }
  }
  await mkdir(info.outputPath('artifacts'), { recursive: true })
  const manifest = []
  for (const reference of visualization.export_items) {
    const response = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${reference.artifact_identifier}`, { headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` } })
    expect(response.ok()).toBeTruthy()
    const bytes = await response.body()
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(reference.content_sha256)
    await writeFile(info.outputPath('artifacts', reference.artifact_identifier), bytes)
    manifest.push(reference)
  }
  await writeFile(info.outputPath('manifest.json'), JSON.stringify(manifest, null, 2))
  await page.screenshot({ path: info.outputPath('virtual-organism.png'), fullPage: true })
  await page.reload()
  await expect(page.getByRole('article', { name: 'Virtual Organism result' })).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: info.outputPath('reopened.png'), fullPage: true })
})

test('saved ADME sessions remain readable with endpoint provenance and native curves', async ({ page }, info) => {
  test.skip(!process.env.PULSATE_REOPEN_ONLY, 'Requires saved acceptance session IDs.')
  const identifiers: string[] = JSON.parse(process.env.PULSATE_REOPEN_SESSIONS ?? '[]')
  expect(identifiers.length).toBeGreaterThan(0)
  await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
  for (const [number, identifier] of identifiers.entries()) {
    await page.goto('/')
    await page.getByText('Reopen a research session', { exact: true }).click()
    await page.getByLabel('Saved session identifier', { exact: true }).fill(identifier)
    await page.getByRole('button', { name: 'Open session', exact: true }).click()
    await expect(page.getByRole('region', { name: 'ADME Parameterization' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/These endpoint dossiers are not necessarily the simulation inputs/)).toBeVisible()
    await page.screenshot({ path: info.outputPath(`session-${number}-current.png`), fullPage: true })
    const images = page.getByRole('img', { name: /concentration time course/ })
    if (await images.count()) {
      await images.first().locator('..').screenshot({ path: info.outputPath(`session-${number}-curve.png`) })
    }
  }
})
