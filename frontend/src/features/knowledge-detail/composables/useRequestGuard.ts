export interface RequestGuard {
  next: () => number
  current: (sequence: number) => boolean
  invalidate: () => void
}

export function useRequestGuard(): RequestGuard {
  let sequence = 0
  return {
    next: () => ++sequence,
    current: (candidate) => candidate === sequence,
    invalidate: () => { sequence += 1 },
  }
}
