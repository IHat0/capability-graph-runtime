export function humanize(value?: string | null): string {
  if (!value) return 'No experiment selected'
  return value.replace(/[._-]+/g, ' ').replace(/\bv(\d+)\b/gi, 'v$1').replace(/\b\w/g, (letter) => letter.toUpperCase())
}
