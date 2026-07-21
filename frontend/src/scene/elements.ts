export interface ElementAppearance {
  color: string
  radius: number
  covalentRadius?: number
}

const FALLBACK: ElementAppearance = { color: '#b7c2cc', radius: 0.7 }

const APPEARANCES: Record<string, ElementAppearance> = {
  H: { color: '#f4f7fa', radius: 0.46, covalentRadius: 0.31 },
  He: { color: '#d9ffff', radius: 0.49, covalentRadius: 0.28 },
  Li: { color: '#b38cff', radius: 0.92, covalentRadius: 1.28 },
  B: { color: '#ffb5a6', radius: 0.72, covalentRadius: 0.84 },
  C: { color: '#5f6975', radius: 0.67, covalentRadius: 0.76 },
  N: { color: '#4f72ff', radius: 0.64, covalentRadius: 0.71 },
  O: { color: '#ff5454', radius: 0.62, covalentRadius: 0.66 },
  F: { color: '#80e06d', radius: 0.61, covalentRadius: 0.57 },
  Na: { color: '#ad76f0', radius: 1.16, covalentRadius: 1.66 },
  Mg: { color: '#8ee46e', radius: 1.05, covalentRadius: 1.41 },
  P: { color: '#ff9d43', radius: 0.9, covalentRadius: 1.07 },
  S: { color: '#f5dc42', radius: 0.88, covalentRadius: 1.05 },
  Cl: { color: '#55d46a', radius: 0.84, covalentRadius: 1.02 },
  Fe: { color: '#d88955', radius: 0.86, covalentRadius: 1.32 },
  Zn: { color: '#92a0bd', radius: 0.88, covalentRadius: 1.22 },
  Br: { color: '#a83838', radius: 0.94, covalentRadius: 1.2 },
  I: { color: '#7951a8', radius: 1.02, covalentRadius: 1.39 },
}

export function elementAppearance(element: string): ElementAppearance {
  return APPEARANCES[element] ?? FALLBACK
}

export function knownCovalentRadius(element: string): number | undefined {
  return APPEARANCES[element]?.covalentRadius
}
