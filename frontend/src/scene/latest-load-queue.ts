export class LatestLoadQueue {
  private sequence = 0
  private queue: Promise<void> = Promise.resolve()

  enqueue(
    run: (isLatest: () => boolean) => Promise<void>,
    onSuccess: () => void,
    onError: (error: unknown) => void,
  ): void {
    const token = ++this.sequence
    const isLatest = () => token === this.sequence
    this.queue = this.queue.catch(() => undefined).then(async () => {
      if (!isLatest()) return
      try {
        await run(isLatest)
        if (isLatest()) onSuccess()
      } catch (error) {
        if (isLatest()) onError(error)
      }
    })
  }

  invalidate(): void {
    this.sequence += 1
  }

  whenIdle(): Promise<void> {
    return this.queue
  }
}
