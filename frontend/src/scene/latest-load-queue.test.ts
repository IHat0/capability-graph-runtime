import { describe, expect, it } from 'vitest'
import { LatestLoadQueue } from './latest-load-queue'

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((next) => { resolve = next })
  return { promise, resolve }
}

describe('latest scene load queue', () => {
  it('serializes mutations and leaves the latest accepted scene visible', async () => {
    const queue = new LatestLoadQueue()
    const firstGate = deferred()
    const firstStarted = deferred()
    const mutations: string[] = []
    queue.enqueue(async (isLatest) => {
      mutations.push('first:start')
      firstStarted.resolve()
      await firstGate.promise
      if (isLatest()) mutations.push('first:visible')
    }, () => undefined, () => undefined)

    await firstStarted.promise
    queue.enqueue(async (isLatest) => {
      mutations.push('second:start')
      if (isLatest()) mutations.push('second:visible')
    }, () => undefined, () => undefined)
    firstGate.resolve()
    await queue.whenIdle()

    expect(mutations).toEqual(['first:start', 'second:start', 'second:visible'])
  })
})
