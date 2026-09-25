import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

test.skip(!process.env.PULSATE_PUBLIC_ACQUISITION_ACCEPTANCE, 'Requires isolated real model and public-source acquisition.')
test.setTimeout(300_000)
const questions = [
  'Using the deposited protein structure PDB 3MXF, evaluate JQ1 against the target and tell me how it performs in the current docking workflow. I have not uploaded any structure files, so obtain the required public molecular data yourself and tell me exactly what records you used.',
  'Compare JQ1 and I-BET762 against BRD4 and tell me which appears more promising under the computational screening evidence available. Resolve the target and ligand structures yourself from trusted public sources, and disclose which structures and identifiers you chose.',
  'I want to compare JQ1 and I-BET762 against BRD4, but I do not have a preferred experimental structure. Choose an appropriate public BRD4 structure for this docking study if the available evidence supports a defensible choice. Explain why you selected it. If there is not enough information to choose responsibly, ask me the specific scientific clarification you need instead of choosing arbitrarily.',
]

for (const [index, question] of questions.entries()) {
  test('public acquisition question ' + (index + 1), async ({ page }, testInfo) => {
    await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
    let uploadCount = 0
    page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/uploads')) uploadCount++ })
    await page.goto('/')
    await page.getByLabel('Scientific question', { exact: true }).fill(question)
    const pending = page.waitForResponse(response => response.url().endsWith('/research/sessions') && response.request().method() === 'POST', { timeout: 240_000 })
    await page.getByRole('button', { name: 'Start research session' }).click()
    const response = await pending
    const submitted = response.request().postDataJSON()
    expect(submitted.input_references ?? []).toHaveLength(0)
    expect(submitted.artifact_references ?? []).toHaveLength(0)
    const session = await response.json()
    await writeFile(testInfo.outputPath('initial-session.json'), JSON.stringify({ question, session }, null, 2))
    expect(uploadCount).toBe(0)
    const ligands = session.artifact_references.filter((item: { artifact_type: string }) => item.artifact_type === 'ligand_structure')
    expect(ligands).toHaveLength(index === 0 ? 1 : 2)
    for (const item of session.artifact_references) {
      expect(item.metadata.acquisition_source_url).toMatch(/^https:\/\//)
      expect(item.content_sha256).toMatch(/^[a-f0-9]{64}$/)
      expect(item.metadata.acquisition_content_sha256).toBe(item.content_sha256)
    }
    if (index === 0) {
      expect(session.status).toBe('planned')
      const resultPending = page.waitForResponse(item => item.url().endsWith('/execute'), { timeout: 200_000 })
      await page.getByRole('button', { name: 'Run research', exact: true }).click()
      const result = await (await resultPending).json()
      await writeFile(testInfo.outputPath('verified-answer.json'), JSON.stringify(result, null, 2))
      expect(result.status).toBe('completed')
      expect(result.scientist_result.verification_status).toBe('passed')
      expect(result.execution_steps.every((step: { status: string }) => step.status === 'succeeded')).toBe(true)
      expect(result.scientist_result.scientific_result).toContain('46907787')
      expect(result.scientist_result.scientific_result).toContain('3MXF')
      await page.getByRole('button', { name: 'Focus in 3D', exact: true }).first().click()
      await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
      await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
      await expect(page.locator('.viewer-render-error')).toHaveCount(0)
    } else {
      expect(session.status).toBe('awaiting_clarification')
      expect(session.scientist_result).toBeNull()
      expect(session.artifact_references.some((item: { artifact_type: string }) => item.artifact_type === 'protein_structure')).toBe(false)
      expect(ligands.map((item: { metadata: { display_name: string } }) => item.metadata.display_name).sort()).toEqual(['I-BET762', 'JQ1'])
      expect(session.evidence_proposal.entity_candidates.length).toBeGreaterThan(1)
      expect(JSON.stringify(session.next_questions)).toContain('organism and protein domain')
      await expect(page.getByRole('button', { name: 'Run research', exact: true })).toHaveCount(0)
    }
    await page.reload()
    if (index === 0) await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible({ timeout: 30_000 })
    else await expect(page.getByLabel('Your reply', { exact: true })).toBeVisible({ timeout: 30_000 })
    await page.screenshot({ path: testInfo.outputPath('public-acquisition.png'), fullPage: true })
  })
}
