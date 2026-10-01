import { describe, expect, it } from 'vitest'
import { formatDuration } from './format'

describe('formatDuration', () => {
  it('초 단위만 있으면 초로 말한다', () => {
    expect(formatDuration(30)).toBe('30초')
  })

  it('분 단위는 나머지 초를 버린다', () => {
    expect(formatDuration(45 * 60 + 20)).toBe('45분')
  })

  it('시간과 분을 함께 말하고, 분이 0 이면 시간만 말한다', () => {
    expect(formatDuration(3600 + 20 * 60)).toBe('1시간 20분')
    expect(formatDuration(2 * 3600)).toBe('2시간')
  })
})
