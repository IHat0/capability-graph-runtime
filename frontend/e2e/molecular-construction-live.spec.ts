import { expect, test } from '@playwright/test'
import { writeFile, mkdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'

test.skip(!process.env.PULSATE_CONSTRUCTION_ACCEPTANCE, 'Requires real isolated scientific runtime.')
test.setTimeout(600_000)

test('plain-language molecular construction', async ({ page }, info) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
  await page.goto('/')
  await page.getByLabel('Scientific question', { exact: true }).fill('Create caffeine.')
  const pending = page.waitForResponse(r => r.url().endsWith('/research/sessions') && r.request().method() === 'POST', { timeout: 300_000 })
  await page.getByRole('button', { name: 'Start research session', exact: true }).click()
  const response = await pending
  await writeFile(info.outputPath('initial-session.json'), await response.text())
  expect(response.ok()).toBeTruthy()
  let session = await response.json()
  if (session.status === 'planned') {
    const running = page.waitForResponse(r => r.url().endsWith('/execute'), { timeout: 1_200_000 })
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    session = await (await running).json()
  }
  await writeFile(info.outputPath('result.json'), JSON.stringify(session, null, 2))
  await page.screenshot({ path: info.outputPath('result.png'), fullPage: true })
  console.log(JSON.stringify({ session: session.session_identifier, status: session.status,
    questions: session.next_questions, answer: session.scientist_summary }))
  if (process.env.PULSATE_CONSTRUCTION_REQUIRE_SUCCESS) {
    expect(session.status).toBe('completed')
    expect(session.scientist_result.verification_status).toBe('passed')
    expect(session.input_references).toHaveLength(0)
    expect(session.compilation.compatibility_objective.quantum_execution_target).toBe('none')
    expect(session.compilation.compatibility_objective.active_space_policy).toBe('not_requested')
    expect(session.execution_steps.every((step: { status: string }) => step.status === 'succeeded')).toBe(true)
    expect(session.execution_steps.some((step: { capability_name: string }) => step.capability_name.startsWith('quantum.'))).toBe(false)
    const visualization = await (await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/visualization`, {
      headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` },
    })).json()
    await writeFile(info.outputPath('visualization.json'), JSON.stringify(visualization, null, 2))
    expect(visualization.structures.some((s: { artifact_type: string }) => s.artifact_type === 'constructed_molecule_sdf')).toBe(true)
    const identifiers = [...new Set<string>(session.execution_steps.flatMap(
      (step: { output_artifact_identifiers: string[] }) => step.output_artifact_identifiers))]
    await mkdir(info.outputPath('artifacts'), { recursive: true })
    const manifest = []
    for (const identifier of identifiers) {
      const artifact = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${identifier}`, {
        headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` },
      })
      expect(artifact.ok(), identifier).toBeTruthy()
      const bytes = await artifact.body()
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(artifact.headers()['x-content-sha256'])
      await writeFile(info.outputPath('artifacts', identifier), bytes)
      manifest.push({ identifier, sha256: createHash('sha256').update(bytes).digest('hex'), bytes: bytes.length })
    }
    await writeFile(info.outputPath('artifact-manifest.json'), JSON.stringify(manifest, null, 2))
    expect(visualization.construction_summary.selected_compute).toBe('classical')
    expect(visualization.construction_summary.computation_reason).toContain('Classical computation is sufficient')
    expect(visualization.export_items.some((a: { artifact_type: string }) => a.artifact_type === 'constructed_molecule_xyz')).toBe(true)
    expect(visualization.export_items.some((a: { artifact_type: string }) => a.artifact_type.startsWith('quantum_') || a.artifact_type === 'variational_ground_state_result')).toBe(false)
    for (const kind of ['molecular_identity_verification', 'computation_selection_decision', 'molecular_construction_execution_receipt']) {
      const reference = visualization.export_items.find((a: { artifact_type: string }) => a.artifact_type === kind)
      expect(reference, kind).toBeTruthy()
      const response = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${reference.artifact_identifier}`, {
        headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` },
      })
      const document = await response.json()
      if (kind === 'molecular_identity_verification') {
        expect(document.passed).toBe(true)
        expect(Object.values(document.checks).every(Boolean)).toBe(true)
      }
      if (kind === 'computation_selection_decision') {
        expect(document.quantum_selected).toBe(false)
        expect(document.quantum_execution_target).toBe('none')
      }
      if (kind === 'molecular_construction_execution_receipt') {
        expect(document.verified).toBe(true)
        expect(document.hartree_fock.converged).toBe(true)
        expect(document.workflow).toHaveLength(6)
      }
    }
    // No viewer button or downloaded-file opening: completion must reveal Mol* automatically.
    await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
    await expect(page.locator('.viewer-render-error')).toHaveCount(0)
    await expect(page.locator('.viewer-meta')).toContainText(`${visualization.construction_summary.atom_count} atoms`)
    await expect(page.locator('.viewer-titlebar')).toContainText('Pulsate-generated coordinates')
    const viewer = page.locator('.molstar-host')
    const box = await viewer.boundingBox()
    expect(box).toBeTruthy()
    const originalView = createHash('sha256').update(await viewer.screenshot()).digest('hex')
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
    await page.mouse.down()
    await page.mouse.move(box!.x + box!.width / 2 + 100, box!.y + box!.height / 2 + 40, { steps: 12 })
    await page.mouse.up()
    await page.mouse.move(20, 20)
    await expect.poll(async () => createHash('sha256').update(await viewer.screenshot()).digest('hex')).not.toBe(originalView)
    await page.getByRole('button', { name: 'Fit structure', exact: true }).click()
    await page.locator('.research-result').getByRole('heading', { name: /^Constructed / }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: info.outputPath('structure-3d.png'), fullPage: true })
    for (const format of ['SDF', 'XYZ']) {
      const pendingDownload = page.waitForEvent('download')
      await page.getByRole('button', { name: `Download ${format} evidence`, exact: true }).click()
      const download = await pendingDownload
      expect(download.suggestedFilename()).toMatch(new RegExp(`\\.${format.toLowerCase()}$`))
      await download.saveAs(info.outputPath(`generated-molecule.${format.toLowerCase()}`))
    }
    await page.reload()
    await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
    await expect(page.locator('.viewer-render-error')).toHaveCount(0)
    await expect(page.locator('.research-result').getByRole('heading', { name: /^Constructed / })).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.research-heading').getByText('Create caffeine.', { exact: true })).toBeVisible()
    await page.screenshot({ path: info.outputPath('saved-structure-and-results.png'), fullPage: true })
  }
})

test('saved constructed molecule view', async ({ page }, info) => {
  test.skip(!process.env.PULSATE_CONSTRUCTION_SAVED_SESSION, 'Requires an already verified construction session.')
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
  await page.goto(`/?research=${encodeURIComponent(process.env.PULSATE_CONSTRUCTION_SAVED_SESSION!)}`)
  const heading = page.locator('.research-result').getByRole('heading', { name: /^Constructed / })
  await expect(heading).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
  await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
  await expect(page.locator('.viewer-render-error')).toHaveCount(0)
  await heading.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('saved-structure-and-results.png'), fullPage: true })
})
