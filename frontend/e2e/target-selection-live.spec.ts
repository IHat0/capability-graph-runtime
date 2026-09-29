import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

test.skip(!process.env.PULSATE_TARGET_SELECTION_ACCEPTANCE, 'Requires isolated live model and scientific runtime.')
test.setTimeout(600_000)
const questions = [
  'Compare JQ1 and I-BET762 against human BRD4 bromodomain 1. I have no preferred PDB structure. Select a suitable public experimental structure using defensible scientific evidence, explain the choice and execute the comparison.',
  'Compare erlotinib and gefitinib against the human EGFR kinase domain. Acquire public structures, select a scientifically appropriate experimental receptor, disclose important mutations, missing binding-site residues and other relevant limitations, and execute the comparison.',
  'Compare JQ1 and I-BET762 against BRD4 using a public experimental structure.',
]

for (const [index, question] of questions.entries()) {
  test('target selection scenario ' + (index + 1), async ({ page }, testInfo) => {
    await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
    await page.goto('/')
    await page.getByLabel('Scientific question', { exact: true }).fill(question)
    const initial = page.waitForResponse(r => r.url().endsWith('/research/sessions') && r.request().method() === 'POST', { timeout: 300_000 })
    await page.getByRole('button', { name: 'Start research session' }).click()
    const response = await initial
    expect(response.ok()).toBeTruthy()
    let session = await response.json()
    await writeFile(testInfo.outputPath('initial-session.json'), JSON.stringify({ question, session }, null, 2))
    expect(response.request().postDataJSON().input_references ?? []).toHaveLength(0)
    if (index === 2) {
      expect(session.status).toBe('awaiting_clarification')
      const identifier = session.session_identifier
      await page.getByLabel('Your reply', { exact: true }).fill('Use human BRD4 BD1 and continue with defensible experimental structure selection and the comparison.')
      const reply = page.waitForResponse(r => r.url().endsWith('/reply') && r.request().method() === 'POST', { timeout: 300_000 })
      await page.getByRole('button', { name: 'Continue', exact: true }).click()
      session = await (await reply).json()
      expect(session.session_identifier).toBe(identifier)
      await writeFile(testInfo.outputPath('scoped-reply.json'), JSON.stringify(session, null, 2))
    }
    if (session.status === 'planned') {
      const execution = page.waitForResponse(r => r.url().endsWith('/execute'), { timeout: 300_000 })
      await page.getByRole('button', { name: 'Run research', exact: true }).click()
      session = await (await execution).json()
      await writeFile(testInfo.outputPath('execution.json'), JSON.stringify(session, null, 2))
    }
    await page.screenshot({ path: testInfo.outputPath('result.png'), fullPage: true })
    console.log(JSON.stringify({ scenario: index + 1, session: session.session_identifier, status: session.status, questions: session.next_questions, summary: session.scientist_summary }))
    if (process.env.PULSATE_TARGET_SELECTION_REQUIRE_SUCCESS) {
      expect(session.status).toBe('completed')
      expect(session.scientist_result.verification_status).toBe('passed')
      expect(session.execution_steps.every((step: { status: string }) => step.status === 'succeeded')).toBe(true)
      const reports = session.artifact_references.filter((a: { artifact_type: string }) => a.artifact_type === 'target_selection_report')
      expect(reports).toHaveLength(1)
      for (const artifact of reports) {
        const evidence = await page.request.get('/api/v1/research/sessions/' + session.session_identifier + '/artifacts/' + artifact.artifact_identifier,
          { headers: { Authorization: 'Bearer ' + process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN } })
        expect(evidence.ok()).toBeTruthy()
        const bytes = await evidence.body()
        expect(createHash('sha256').update(bytes).digest('hex')).toBe(artifact.content_sha256)
        await writeFile(testInfo.outputPath('selection-evidence.json'), bytes)
        const selection = JSON.parse(bytes.toString('utf8'))
        expect(selection.selected.site_identity_evidence.match_policy).toBe('full standard InChIKey equality')
        expect(selection.selected.reference_residue.slice(0, 3).trim()).toBe(selection.selected.site_identity_evidence.chemical_component_id)
        expect(selection.selected.site_identity_evidence.inchikey).toMatch(/^[A-Z]{14}-[A-Z]{10}-[A-Z]$/)
      }
      await page.getByRole('button', { name: 'Focus in 3D', exact: true }).first().click()
      await expect(page.locator('.loaded-workspace')).toBeVisible({ timeout: 45_000 })
      await expect(page.locator('.viewer-loading')).toHaveCount(0, { timeout: 45_000 })
      await expect(page.locator('.viewer-render-error')).toHaveCount(0)
      await page.reload()
      await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible({ timeout: 30_000 })
      await page.screenshot({ path: testInfo.outputPath('verified-reopened-result.png'), fullPage: true })
    }
  })
}
