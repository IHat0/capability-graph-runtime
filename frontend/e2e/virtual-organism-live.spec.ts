import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'

test.skip(!process.env.PULSATE_PBPK_ACCEPTANCE, 'Requires configured native engine and a real language model.')
test.setTimeout(600_000)

test('native virtual organism with evidence and saved session', async ({ page }, info) => {
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
  await expect(page.getByRole('article', { name: 'Virtual Organism result' })).toBeVisible({ timeout: 30_000 })
  if (visualization.virtual_organism.runs.length) {
    await expect(page.getByRole('img', { name: /concentration time course/ })).toBeVisible()
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
