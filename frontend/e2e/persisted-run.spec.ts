import { expect, test } from '@playwright/test'

const runIdentifier = `run-${'1'.repeat(32)}`
const experimentIdentifier = `experiment-${'2'.repeat(32)}`
const experimentFingerprint = 'a'.repeat(64)
const structureSha256 = 'b'.repeat(64)
const hamiltonianSha256 = 'c'.repeat(64)
const exactResultSha256 = 'd'.repeat(64)
const vqeResultSha256 = 'e'.repeat(64)
const outcomeSha256 = 'f'.repeat(64)
const environmentIdentity = '1'.repeat(64)
const receiptSha256 = '2'.repeat(64)

const identity = {
  run_identifier: runIdentifier,
  source_type: 'dynamic_experiment',
  source_identifier: experimentIdentifier,
  preset_identifier: null,
  experiment_identifier: experimentIdentifier,
  experiment_fingerprint: experimentFingerprint,
  expected_experiment_sha256: experimentFingerprint,
  structure_identifier: 'molecular_structure',
}

const ibmExecution = {
  hardware_role: 'final_energy_evaluation_at_locally_optimized_parameters',
  submission_status: 'completed',
  job_identifier: 'ibm-job-e2e-rejected',
  backend_name: 'ibm_backend_e2e',
  execution_integrity_passed: true,
  scientific_quality_passed: false,
  ibm_total_energy_hartree: -0.9869148266351646,
  local_exact_total_energy_hartree: -1.1205602812999886,
  local_vqe_total_energy_hartree: -1.1205602812661797,
  returned_standard_error: 0.004646588601020327,
}

const run = {
  ...identity,
  execution_target: 'ibm_quantum',
  status: 'rejected',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T01:00:00Z',
  status_url: `/api/v1/runs/${runIdentifier}`,
  structure_sha256: structureSha256,
  hamiltonian_sha256: hamiltonianSha256,
  receipt_sha256: receiptSha256,
  execution_environment_identity: environmentIdentity,
  ibm_job_identifier: ibmExecution.job_identifier,
  ibm_backend_name: ibmExecution.backend_name,
}

const scene = {
  scene_identifier: `scene.${experimentIdentifier}`,
  scene_stage: 'declared',
  experiment_identifier: experimentIdentifier,
  experiment_fingerprint: experimentFingerprint,
  expected_experiment_sha256: experimentFingerprint,
  structure_identifier: 'molecular_structure',
  structure_hash: structureSha256,
  coordinate_unit: 'angstrom',
  atoms: [
    {
      atom_identifier: 'hydrogen-1',
      element: 'H',
      coordinates: [0, 0, 0],
    },
    {
      atom_identifier: 'hydrogen-2',
      element: 'H',
      coordinates: [0, 0, 0.9],
    },
  ],
  bonds: [
    {
      bond_identifier: 'bond.h1-h2',
      atom_identifiers: ['hydrogen-1', 'hydrogen-2'],
      order: 1,
      declared_distance: 0.9,
      derived_distance: 0.9,
    },
  ],
  quantum_region: {
    selection_identifier: 'selection.full',
    atom_identifiers: ['hydrogen-1', 'hydrogen-2'],
  },
  scientific_model: {
    charge: 0,
    spin_multiplicity: 1,
    basis_set: 'sto-3g',
    reference_method: 'restricted_hartree_fock',
    active_electron_count: 2,
    active_spatial_orbital_count: 2,
    mapper: 'jordan_wigner',
    ansatz: 'uccsd',
  },
}

const results = {
  ...identity,
  structure_sha256: structureSha256,
  hamiltonian_sha256: hamiltonianSha256,
  exact_scientific_result_sha256: exactResultSha256,
  vqe_scientific_result_sha256: vqeResultSha256,
  scientific_outcome_sha256: outcomeSha256,
  exact_total_energy_hartree: -1.1205602812999886,
  vqe_total_energy_hartree: -1.1205602812661797,
  absolute_difference_hartree: 3.380895563509068e-11,
  tolerance_hartree: 0.00001,
  energy_unit: 'hartree',
  exact_solver_metadata: {},
  vqe_solver_metadata: {},
  optimizer_evaluations: 12,
  converged: true,
  compatibility_warnings: [],
  execution_environment_identity: environmentIdentity,
  receipt_sha256: receiptSha256,
  ibm_execution: ibmExecution,
}

const verification = {
  ...identity,
  structure_sha256: structureSha256,
  verification_completed: true,
  verification_passed: false,
  authorization_state: 'rejected',
  blocking_findings: ['IBM scientific tolerance exceeded.'],
  nonblocking_findings: [],
  tolerance_check: {
    passed: false,
  },
  scientific_identity_checks: [],
  artifact_integrity_checks: [],
  checks: [],
  compatibility_warnings: [],
  ibm_execution: ibmExecution,
}

const receipt = {
  ...identity,
  schema_version: 'cgr.quantum-preflight-receipt/2.0.0',
  execution_identifier: 'execution-e2e-rejected',
  structure_sha256: structureSha256,
  hamiltonian_sha256: hamiltonianSha256,
  exact_scientific_result_sha256: exactResultSha256,
  vqe_scientific_result_sha256: vqeResultSha256,
  scientific_outcome_sha256: outcomeSha256,
  execution_environment_identity: environmentIdentity,
  receipt_sha256: receiptSha256,
  verification_passed: false,
  authorization_state: 'rejected',
  authorized: false,
  artifacts: [],
  ibm_execution: ibmExecution,
}

test('opens rejected IBM evidence read-only and renders its Mol* scene', async ({
  page,
}, testInfo) => {
  const browserErrors: string[] = []
  const browserWarnings: string[] = []
  const apiRequests: Array<{ method: string; path: string }> = []
  const persistedRunRequests: Array<{ method: string; path: string }> = []

  page.on('pageerror', (error) => {
    browserErrors.push(`pageerror: ${error.message}`)
  })

  page.on('console', (message) => {
    const entry = `${message.type()}: ${message.text()}`

    if (message.type() === 'error') {
      browserErrors.push(entry)
    } else if (message.type() === 'warning') {
      browserWarnings.push(entry)
    }
  })

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const observed = {
      method: request.method(),
      path,
    }

    apiRequests.push(observed)

    if (path.startsWith(`/api/v1/runs/${runIdentifier}`)) {
      persistedRunRequests.push(observed)
    }

    const responses = new Map<string, unknown>([
      [
        '/api/v1/health',
        {
          service: 'pulsate-api-e2e',
          status: 'healthy',
          version: '0.2.0',
        },
      ],
      [
        '/api/v1/experiments/presets',
        {
          presets: [],
          count: 0,
        },
      ],
      [
        '/api/v1/runs/capability',
        {
          available: false,
          execution_targets: [],
          reason: 'Execution is disabled in the browser test.',
          maximum_run_seconds: null,
        },
      ],
      [`/api/v1/runs/${runIdentifier}`, run],
      [`/api/v1/runs/${runIdentifier}/scene`, scene],
      [`/api/v1/runs/${runIdentifier}/results`, results],
      [`/api/v1/runs/${runIdentifier}/verification`, verification],
      [`/api/v1/runs/${runIdentifier}/receipt`, receipt],
    ])

    if (responses.has(path)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(responses.get(path)),
      })
      return
    }

    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: {
          code: 'test_route_not_found',
          message: `No E2E fixture exists for ${path}.`,
        },
      }),
    })
  })

  await page.goto('/')

  const runInput = page.getByLabel('Run identifier')
  await expect(runInput).toBeVisible()

  await runInput.fill(runIdentifier)
  await page.getByRole('button', { name: 'Open run' }).click()

  await expect(
    page.getByText('IBM execution verified', { exact: true }),
  ).toBeVisible()

  await expect(
    page.getByText('Scientific result rejected', { exact: true }),
  ).toBeVisible()

  await expect(
    page.getByText('Read-only persisted run', { exact: true }),
  ).toBeVisible()

  await expect(
    page.getByText(
      'This workspace loaded existing evidence only. It did not create, resume, or execute a run.',
      { exact: true },
    ),
  ).toBeVisible()

  await expect(
    page.getByText(runIdentifier, { exact: true }),
  ).toBeVisible()

  await expect(
    page.getByText('2 atoms · 1 bonds', { exact: true }),
  ).toBeVisible()

  await expect(
    page.getByRole('button', { name: 'Run experiment' }),
  ).toHaveCount(0)

  await expect(
    page.getByText('Local simulator', { exact: true }),
  ).toHaveCount(0)

  await expect(
    page.getByText('Not executed', { exact: true }),
  ).toHaveCount(0)

  const canvas = page.locator('.molstar-host canvas')
  await expect(canvas).toBeVisible()

  await page.waitForTimeout(3_000)

  const geometry = await canvas.evaluate((element) => {
    const rectangle = element.getBoundingClientRect()

    return {
      width: element.width,
      height: element.height,
      clientWidth: rectangle.width,
      clientHeight: rectangle.height,
      display: getComputedStyle(element).display,
      visibility: getComputedStyle(element).visibility,
    }
  })

  expect(geometry.width).toBeGreaterThan(0)
  expect(geometry.height).toBeGreaterThan(0)
  expect(geometry.clientWidth).toBeGreaterThan(0)
  expect(geometry.clientHeight).toBeGreaterThan(0)
  expect(geometry.display).not.toBe('none')
  expect(geometry.visibility).not.toBe('hidden')

  const renderer = await page.evaluate(() => {
    const probe = document.createElement('canvas')
    const context = probe.getContext('webgl2')

    if (!context) {
      return null
    }

    const extension = context.getExtension('WEBGL_debug_renderer_info')

    return extension
      ? String(context.getParameter(extension.UNMASKED_RENDERER_WEBGL))
      : String(context.getParameter(context.RENDERER))
  })

  expect(renderer).not.toBeNull()
  expect(renderer).toContain('SwiftShader')

  const canvasScreenshot = testInfo.outputPath('molstar-canvas.png')
  const canvasImage = await canvas.screenshot({
    path: canvasScreenshot,
  })

  const canvasAnalysis = await page.evaluate(async (encodedPng) => {
    const image = new Image()
    image.src = `data:image/png;base64,${encodedPng}`
    await image.decode()

    const probe = document.createElement('canvas')
    probe.width = image.naturalWidth
    probe.height = image.naturalHeight

    const context = probe.getContext('2d', {
      willReadFrequently: true,
    })

    if (!context) {
      return null
    }

    context.drawImage(image, 0, 0)

    const pixels = context.getImageData(
      0,
      0,
      probe.width,
      probe.height,
    ).data

    const background = pixels.slice(0, 4)
    const colours = new Set<string>()
    let nonBackgroundPixels = 0

    for (let index = 0; index < pixels.length; index += 4) {
      const red = pixels[index]
      const green = pixels[index + 1]
      const blue = pixels[index + 2]
      const alpha = pixels[index + 3]

      colours.add(`${red},${green},${blue},${alpha}`)

      if (
        red !== background[0]
        || green !== background[1]
        || blue !== background[2]
        || alpha !== background[3]
      ) {
        nonBackgroundPixels += 1
      }
    }

    const totalPixels = probe.width * probe.height

    return {
      width: probe.width,
      height: probe.height,
      uniqueColours: colours.size,
      nonBackgroundPixels,
      nonBackgroundRatio: nonBackgroundPixels / totalPixels,
    }
  }, canvasImage.toString('base64'))

  expect(canvasAnalysis).not.toBeNull()

  if (!canvasAnalysis) {
    throw new Error('Mol* canvas analysis was unavailable.')
  }

  expect(canvasAnalysis.width).toBeGreaterThan(400)
  expect(canvasAnalysis.height).toBeGreaterThan(300)
  expect(canvasAnalysis.uniqueColours).toBeGreaterThan(64)
  expect(canvasAnalysis.nonBackgroundPixels).toBeGreaterThan(1_000)
  expect(canvasAnalysis.nonBackgroundRatio).toBeGreaterThan(0.005)

  expect(
    persistedRunRequests.map((request) => request.path).sort(),
  ).toEqual([
    `/api/v1/runs/${runIdentifier}`,
    `/api/v1/runs/${runIdentifier}/receipt`,
    `/api/v1/runs/${runIdentifier}/results`,
    `/api/v1/runs/${runIdentifier}/scene`,
    `/api/v1/runs/${runIdentifier}/verification`,
  ].sort())

  expect(
    persistedRunRequests.every((request) => request.method === 'GET'),
  ).toBe(true)

  expect(
    apiRequests.every((request) => request.method === 'GET'),
  ).toBe(true)

  expect(browserErrors).toEqual([])
  expect(browserWarnings).toEqual([])
})
