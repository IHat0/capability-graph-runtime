import { expect, test } from '@playwright/test'
import path from 'node:path'
import { writeFile } from 'node:fs/promises'

test.skip(!process.env.PULSATE_CANDIDATE_BATCH_ACCEPTANCE, 'Requires isolated real model and computation runtime.')
test.setTimeout(300_000)
const questions = [
  'Evaluate every supplied compound against this protein target, rank them from strongest to weakest under the computational evidence available, explain the major trade-offs between the candidates, and tell me which two deserve further computational investigation. Do not discard any supplied candidate.',
  'Which candidate looks strongest in this docking screen, how confident should I be in that ranking, and what assumptions or limitations of the calculations could realistically change the ordering? Tell me what the current evidence supports and what it does not.',
  'Compare all of these compounds against this target and tell me which is the strongest candidate.',
]
for (const [index, question] of questions.entries()) {
  test('candidate evidence question ' + (index + 1), async ({ page }, testInfo) => {
    await page.addInitScript(token => { window.pulsateAccessTokenProvider = async () => token }, process.env.PULSATE_BROWSER_ACCEPTANCE_TOKEN!)
    let executions = 0
    page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/execute')) executions++ })
    await page.goto('/')
    await page.getByLabel('Scientific question', { exact: true }).fill(question)
    const root = process.env.PULSATE_DRUG_ACCEPTANCE_ROOT!
    await page.locator('input[type=file]').nth(1).setInputFiles(path.join(root, '3MXF.pdb'))
    await page.locator('input[type=file]').nth(2).setInputFiles(['JQ1', 'EAM', 'EST', 'OHT'].map(name => path.join(root, name + '.sdf')))
    await page.getByRole('button', { name: 'Start research session' }).click()
    await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeVisible({ timeout: 150_000 })
    const pending = page.waitForResponse(response => response.url().endsWith('/execute'), { timeout: 200_000 })
    await page.getByRole('button', { name: 'Run research', exact: true }).click()
    const result = await (await pending).json()
    await writeFile(testInfo.outputPath('answer.json'), JSON.stringify({ question, result }, null, 2))
    expect(result.status).toBe('completed')
    expect(result.scientist_result.verification_status).toBe('passed')
    expect(result.execution_steps.every((step: { status: string }) => step.status === 'succeeded')).toBe(true)
    expect(result.scientist_result.candidate_ranking).toHaveLength(4)
    const answer = JSON.stringify(result.scientist_result)
    for (const name of ['JQ1', 'EAM', 'EST', 'OHT']) expect(answer).toContain(name)
    expect(answer).toContain('No calibrated ranking confidence')
    if (index === 0) {
      expect(answer).toContain('EAM')
      expect(result.scientist_result.candidate_ranking.filter((line: string) => line.includes('(selected)'))).toHaveLength(2)
    }
    if (index === 2) {
      let firstReview: any
      for (const [turn, reply] of [
        'Why did the third-ranked compound lose to the first?',
        'Now ignore drug-likeness and compare those two only using the docking evidence. Does your conclusion change?',
      ].entries()) {
        await page.getByLabel('Your reply', { exact: true }).fill(reply)
        const response = page.waitForResponse(item => item.url().endsWith('/reply'), { timeout: 150_000 })
        await page.getByRole('button', { name: 'Ask about evidence', exact: true }).click()
        const latest = await (await response).json()
        await writeFile(testInfo.outputPath('follow-up-' + turn + '.json'), JSON.stringify(latest, null, 2))
        expect(latest.status).toBe('completed')
        expect(latest.scientist_result).toEqual(result.scientist_result)
        expect(latest.compilation).toEqual(result.compilation)
        const review = latest.evidence_reviews.at(-1)
        expect(review.new_scientific_computation).toBe(false)
        expect(review.candidate_identifiers).toHaveLength(2)
        expect(review.response).toContain('EAM')
        expect(review.response).toContain('JQ1')
        if (turn === 0) firstReview = review
        else {
          expect(review.candidate_identifiers).toEqual(firstReview.candidate_identifiers)
          expect(review.metric_identifiers).toEqual(['vina_pose_score'])
          expect(review.conclusion_changed).toBe(false)
        }
      }
      expect(executions).toBe(1)
    }
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible({ timeout: 30_000 })
    if (index === 2) await expect(page.getByText('Now ignore drug-likeness and compare those two only using the docking evidence. Does your conclusion change?', { exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('answer.png'), fullPage: true })
  })
}
