import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'

test.skip(!process.env.PULSATE_PBPK_ACCEPTANCE, 'Requires configured native engine and a real language model.')
test.setTimeout(Number(process.env.PULSATE_PBPK_TIMEOUT_MS ?? 600_000))

test('native virtual organism with evidence and saved session', async ({ page }, info) => {
  test.skip(Boolean(process.env.PULSATE_REOPEN_ONLY), 'Read-only existing-session inspection selected.')
  const question = process.env.PULSATE_PBPK_QUERY
  if (!question) throw new Error('Supply an explicit unrelated research scenario; there is no default held-out candidate.')
  await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
  await page.goto('/')
  const suppliedInputs: Array<{ label: string; path: string }> = JSON.parse(process.env.PULSATE_PBPK_INPUTS ?? '[]')
  for (const input of suppliedInputs) await page.getByLabel(input.label, { exact: true }).setInputFiles(input.path)
  let session
  const resumeIdentifier = process.env.PULSATE_PBPK_RESUME_SESSION
  if (process.env.PULSATE_PBPK_REASSEMBLY_ACCEPTANCE) {
    expect(resumeIdentifier, 'Recovery acceptance must reopen an existing session').toBeTruthy()
    await page.route('**/api/v1/research/**', async route => {
      expect(route.request().method(), 'Evidence recovery browser must never submit scientific execution').toBe('GET')
      await route.continue()
    })
  }
  if (resumeIdentifier) {
    await page.getByText('Reopen a research session', { exact: true }).click()
    await page.getByLabel('Saved session identifier', { exact: true }).fill(resumeIdentifier)
    const opened = page.waitForResponse(r => r.url().endsWith(`/research/sessions/${resumeIdentifier}`)
      && r.request().method() === 'GET', { timeout: 30_000 })
    await page.getByRole('button', { name: 'Open session', exact: true }).click()
    session = await (await opened).json()
    if (process.env.PULSATE_PBPK_REASSEMBLY_ACCEPTANCE) expect(session.status).toBe('completed')
  } else {
    await page.getByLabel('Scientific question', { exact: true }).fill(question)
    const started = page.waitForResponse(r => r.url().endsWith('/research/sessions') && r.request().method() === 'POST', { timeout: 200_000 })
    await page.getByRole('button', { name: 'Start research session', exact: true }).click()
    session = await (await started).json()
  }
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
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    // The HTTP execution request can legitimately outlive a browser response
    // timeout. Observe its persisted state instead of submitting it again.
    await expect.poll(async () => {
      const current = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}`, {
        headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` }, timeout: 30_000,
      })
      expect(current.ok()).toBeTruthy()
      session = await current.json()
      return session.status
    }, { intervals: [1000], timeout: 30_000 }).not.toBe('planned')
  }
  // Native protocols may take hours. Poll this exact session, never submit again.
  if (session.status === 'running') {
    await writeFile(info.outputPath('running-session.json'), JSON.stringify(session, null, 2))
    await page.screenshot({ path: info.outputPath('running.png'), fullPage: true })
    console.log(JSON.stringify({ session: session.session_identifier, status: 'running', scientific_rerun: false }))
    await expect.poll(async () => {
      const current = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}`, {
        headers: { Authorization: `Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}` }, timeout: 30_000,
      })
      expect(current.ok()).toBeTruthy()
      session = await current.json()
      await writeFile(info.outputPath('latest-session.json'), JSON.stringify(session, null, 2))
      return session.status
    }, { intervals: [30_000], timeout: Number(process.env.PULSATE_PBPK_TIMEOUT_MS ?? 600_000) - 240_000 }).not.toBe('running')
    await page.goto(`/?research=${session.session_identifier}`)
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
  if (process.env.PULSATE_PBPK_REASSEMBLY_ACCEPTANCE) {
    expect(Buffer.byteLength(JSON.stringify(visualization))).toBeLessThan(4 * 1024 * 1024)
    expect(Buffer.byteLength(JSON.stringify(session.scientist_result))).toBeLessThan(4 * 1024 * 1024)
    expect(session.scientist_result.scientific_result).toContain('recorded exposure curves')
    expect(session.scientist_result.scientific_result).toContain('Observed scenario/protocol delta range')
  }
  await writeFile(info.outputPath('visualization.json'), JSON.stringify(visualization, null, 2))
  expect(visualization.virtual_organism).toBeTruthy()
  expect(visualization.virtual_organism.computation_selection.quantum_selected).toBe(false)
  if (process.env.PULSATE_PBPK_EXPECTED_DURATION_H) {
    const horizon = Number(process.env.PULSATE_PBPK_EXPECTED_DURATION_H)
    expect(visualization.virtual_organism.request.duration_h).toBe(horizon)
    const scenarios = visualization.virtual_organism.drug_parameter_uncertainty.scenarios
    expect(scenarios.length).toBeGreaterThan(0)
    for (const scenario of scenarios) {
      for (const curve of scenario.population.series) {
        expect(curve.times_h[0]).toBe(0)
        expect(curve.times_h.at(-1)).toBe(horizon)
        expect(curve.times_h.length).toBeGreaterThan(2)
      }
    }
    const declaredHorizon = session.scientist_result.scientific_result.match(/simulated through ([0-9.]+) h/)
    expect(declaredHorizon, 'Answer must state the completed PBPK observation horizon').toBeTruthy()
    expect(Number(declaredHorizon![1])).toBe(horizon)
  }
  if (process.env.PULSATE_FUNCTIONAL_ACCEPTANCE) {
    const functional = visualization.virtual_organism.functional_exposure
    expect(functional).toBeTruthy()
    expect(functional.verification.passed).toBe(true)
    expect(functional.functional_activity_prediction.coverage.universe_targets).toBeGreaterThan(0)
    if (process.env.PULSATE_FUNCTIONAL_MANIFEST_SHA256) {
      expect(functional.functional_activity_prediction.manifest_sha256).toBe(process.env.PULSATE_FUNCTIONAL_MANIFEST_SHA256)
    }
    expect(functional.quantitative_activity.some((a: { functional_assay_sha256?: string }) => a.functional_assay_sha256)).toBe(true)
    expect(functional.functional_transfer_refusals.length).toBeGreaterThan(0)
    const computed = functional.functional_models.filter((m: { status: string }) => m.status === 'computed')
    expect(computed.length).toBeGreaterThan(0)
    const completeAnswer = page.getByText(session.scientist_result.scientific_result, {exact:true})
    if (!(await completeAnswer.isVisible())) {
      await page.getByText('Scientific answer, assumptions, and methods', {exact:true}).click({timeout:10_000})
    }
    await expect(completeAnswer).toBeVisible()
    for (const model of computed) {
      expect(model.result.verification.passed).toBe(true)
      expect(model.result.perturbation.transfer_receipt.qualification_state).toBe('exploratory_only')
      expect(model.result.perturbation.transfer_receipt.candidate_decision_authority).toBe(false)
      expect(Object.values(model.result.response).length).toBeGreaterThan(0)
      expect(model.exposure_case_identifier).toBeTruthy()
      expect(model.result.curves).toBeUndefined()
      expect(model.result.waveform_evidence.full_response_sha256).toBe(model.result.verification.report_sha256)
      for (const curve of model.result.waveform_evidence.curves) {
        expect(curve.sample_count).toBeGreaterThan(0)
        expect(curve.waveform_sha256).toMatch(/^[a-f0-9]{64}$/)
        if (process.env.PULSATE_PBPK_REASSEMBLY_ACCEPTANCE) {
          expect(curve.display_projection.values.length).toBeLessThan(256)
          expect(curve.display_projection.values.length).toBeLessThan(curve.sample_count)
          await expect(page.getByRole('img', {name:`${curve.condition} physiological waveform display projection`}).first()).toBeVisible()
        }
      }
      for (const response of Object.values(model.result.response) as Array<{baseline_final:number;perturbed_final:number;final_difference:number}>) {
        for (const value of [response.baseline_final, response.perturbed_final, response.final_difference]) {
          expect(session.scientist_result.scientific_result).toContain(String(value))
        }
      }
    }
    expect(session.scientist_result.scientific_result).toContain('Functional panel:')
    expect(session.scientist_result.scientific_result).toContain('Native physiological numerical response')
    for (const category of ['EXPOSURE:', 'FUNCTIONAL PHARMACOLOGY:', 'EXPOSURE RELEVANCE:', 'PHYSIOLOGY:', 'CLINICAL INFERENCE: not established']) {
      expect(session.scientist_result.scientific_result).toContain(category)
    }
    expect(session.scientist_result.scientific_result).toContain('no independent candidate-decision authority')
    expect(session.scientist_result.scientific_result).toContain('separate exploratory single-cell simulations')
    await expect(page.getByRole('region', { name: 'Native functional exposure result', exact: true })).toBeVisible()
    await expect(page.getByRole('note', { name: 'Exploratory physiology limitation', exact: true }).first()).toBeVisible()
    await expect(page.getByRole('region', { name: 'Raw waveform receipt', exact: true }).first()).toBeVisible()
    await page.getByRole('region', { name: 'Native functional exposure result', exact: true }).screenshot({ path: info.outputPath('functional-pharmacology.png') })
    if (process.env.PULSATE_PBPK_REASSEMBLY_ACCEPTANCE) {
      await page.getByRole('region', {name:'Functional models',exact:true}).locator(':scope > div').first()
        .screenshot({path:info.outputPath('physiology-first-case.png')})
    }
  }
  if (process.env.PULSATE_SPONSOR_ACCEPTANCE) {
    const organism = visualization.virtual_organism
    expect(organism.sponsor_dossiers).toHaveLength(1)
    expect(organism.sponsor_dossiers[0].experiment_provenance.historical_private_data_claim).toBe(false)
    expect(organism.runs).toHaveLength(0) // no nominal input set chosen
    expect(organism.parameters.length).toBeGreaterThan(0)
    expect(organism.evidence_quality.assumed).toBeGreaterThan(0)
    const ranges = organism.parameters.filter((p: { interval?: number[] | null }) => p.interval)
    expect(ranges.length).toBeGreaterThan(0)
    expect(ranges.every((p: { value: number | null }) => p.value === null)).toBe(true)
    const scenarios = organism.drug_parameter_uncertainty.scenarios
    expect(scenarios.length).toBeGreaterThan(1)
    for (const scenario of scenarios) {
      expect(scenario.scenario_kind).toBe('sponsor_input_sensitivity')
      expect(scenario.population.series.length).toBeGreaterThan(0)
      expect(scenario.population.series.every((c: { subject_count: number }) => c.subject_count === 2)).toBe(true)
      for (const organ of ['Heart', 'Liver', 'Kidney', 'Brain']) {
        expect(scenario.population.series.some((c: { organ: string }) => c.organ === organ)).toBe(true)
      }
      expect(scenario.population.series.some((c: { compartment: string }) => c.compartment === 'Plasma Unbound (Peripheral Venous Blood)')).toBe(true)
    }
    await expect(page.getByRole('region', { name: 'Sponsor-side input provenance', exact: true })).toBeVisible()
    await page.getByText('Parameter provenance, assumptions and limitations', { exact: true }).click()
    await expect(page.getByText(/no nominal selected/).first()).toBeVisible()
    const curves = scenarios[0].population.series
    for (const match of [(c: { compartment: string }) => c.compartment === 'Plasma Unbound (Peripheral Venous Blood)',
      (c: { compartment: string }) => /blood/i.test(c.compartment) && !/plasma/i.test(c.compartment),
      (c: { organ: string; compartment: string }) => c.organ === 'Heart' && c.compartment === 'Interstitial Unbound',
      (c: { organ: string; compartment: string }) => c.organ === 'Liver' && c.compartment === 'Tissue']) {
      const curve = curves.find(match)
      expect(curve, 'Required native curve present').toBeTruthy()
      await page.getByLabel('Inspect scenario compartment', { exact: true }).selectOption(curve.path)
      await expect(page.getByRole('img', { name: `Human ${curve.organ} ${curve.compartment} concentration time course`, exact: true })).toBeVisible()
      await page.screenshot({path: info.outputPath(`sponsor-${curve.organ}-${curve.compartment.replaceAll(/[^a-zA-Z]/g, '-')}.png`), fullPage: true})
    }
    await page.getByLabel('Inspect independently simulated scenario').selectOption('1')
    await expect(page.getByText(`Conditional scenario ${scenarios[1].scenario_identifier} — not a nominal prediction.`, {exact:true})).toBeVisible()
    await page.screenshot({path:info.outputPath('sponsor-second-conditional-case.png'),fullPage:true})
  }
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
    if (process.env.PULSATE_FUNCTIONAL_ACCEPTANCE && reference.artifact_type === 'native_functional_exposure') {
      const raw = JSON.parse(bytes.toString('utf8'))
      const projected = visualization.virtual_organism.functional_exposure
      for (const model of raw.functional_models.filter((m: {status:string})=>m.status==='computed')) {
        const matches = projected.functional_models.filter((m: {activity_sha256:string;exposure_case_identifier:string;exposure_sha256:string})=>
          m.activity_sha256===model.activity_sha256 && m.exposure_case_identifier===model.exposure_case_identifier
          && m.exposure_sha256===model.exposure_sha256)
        expect(matches, 'Each assay/scenario/subject exposure must retain its own response').toHaveLength(1)
        const projection = matches[0]
        expect(projection.result.response).toEqual(model.result.response)
        for (const [index,curve] of model.result.curves.entries()) {
          expect(projection.result.waveform_evidence.curves[index].sample_count).toBe(curve.values.length)
          // Exact canonical waveform receipts are replayed server-side before
          // projection; compare raw samples/endpoints here without changing
          // Python floating-point serialization through JavaScript JSON.
          expect(projection.result.waveform_evidence.curves[index].condition).toBe(curve.condition)
        }
      }
    }
    manifest.push(reference)
    if (process.env.PULSATE_PBPK_REASSEMBLY_ACCEPTANCE && reference.artifact_type === 'scientific_evidence_manifest') {
      const sourceManifest = JSON.parse(bytes.toString('utf8'))
      expect(bytes.length).toBeLessThan(4 * 1024 * 1024)
      expect(sourceManifest.externalized.artifact_references.length).toBeGreaterThan(0)
      let references = sourceManifest.artifact_references
      if (sourceManifest.pages) {
        references = []
        for (const pageRef of sourceManifest.pages) {
          const indexDownload = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${pageRef.artifact_identifier}`, {headers:{Authorization:`Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}`}})
          expect(indexDownload.ok()).toBeTruthy()
          const indexBytes = await indexDownload.body()
          expect(indexBytes.length).toBe(pageRef.byte_size)
          expect(indexBytes.length).toBeLessThan(4 * 1024 * 1024)
          expect(createHash('sha256').update(indexBytes).digest('hex')).toBe(pageRef.content_sha256)
          references.push(...JSON.parse(indexBytes.toString('utf8')).artifact_references)
        }
      }
      expect(references.length).toBe(sourceManifest.artifact_count)
      // Prove manifest-only references remain resolvable via the SAME owned session.
      const external = references.filter((r: {artifact_identifier:string}) =>
        sourceManifest.externalized.artifact_references.includes(r.artifact_identifier))
      for (const ref of [external[0], external.at(-1)]) {
        const download = await page.request.get(`/api/v1/research/sessions/${session.session_identifier}/artifacts/${ref.artifact_identifier}`, {headers:{Authorization:`Bearer ${process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN}`}})
        expect(download.ok()).toBeTruthy()
        const original = await download.body()
        expect(original.length).toBe(ref.byte_size)
        expect(createHash('sha256').update(original).digest('hex')).toBe(ref.content_sha256)
      }
    }
  }
  await writeFile(info.outputPath('manifest.json'), JSON.stringify(manifest, null, 2))
  await page.screenshot({ path: info.outputPath('virtual-organism.png'), fullPage: true })
  await page.reload()
  await expect(page.getByRole('article', { name: 'Virtual Organism result' })).toBeVisible({ timeout: 30_000 })
  if (process.env.PULSATE_FUNCTIONAL_ACCEPTANCE) {
    await expect(page.getByRole('region', { name: 'Native functional exposure result', exact: true })).toBeVisible()
    await expect(page.getByRole('note', { name: 'Exploratory physiology limitation', exact: true }).first()).toBeVisible()
  }
  if (process.env.PULSATE_SPONSOR_ACCEPTANCE) {
    await expect(page.getByRole('region', {name:'Sponsor-side input provenance',exact:true})).toBeVisible()
    await expect(page.getByRole('img', {name:/concentration time course/}).first()).toBeVisible()
  }
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
