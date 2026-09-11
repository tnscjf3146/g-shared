'use client'

import { useSyncExternalStore } from 'react'
import { chapterStore } from './chapter-store'
import styles from './chapterSidebar.module.css'
import { CHAPTER_LABELS } from './motion/chapters'

// 오른쪽 챕터 목록 — React Bits LineSidebar 의 구조(번호·라벨·마커 선·사이 눈금)를 우리 토큰으로.
// 강조는 두 가지만: 현재 챕터(chapterStore, 상시)와 커서가 올라간 항목 하나(:hover). 이웃으로 퍼지는 근접 효과와 rAF 보간은 뺐다 —
// 3D 캔버스가 메인 스레드를 쓰는 화면에서 프레임마다 스타일을 8개씩 갱신하면 버벅였다(2026-09-10). 전환은 CSS transition 이 맡는다.
// 클릭하면 그 챕터로 점프(landingScene 이 등록한 jumper). 인트로 중(index -1)엔 숨김
export function ChapterSidebar() {
  const active = useSyncExternalStore(chapterStore.subscribe, chapterStore.get, () => -1)
  return (
    <nav aria-label="챕터" className={`${styles.root} ${active < 0 ? styles.hidden : ''}`}>
      <ul className={styles.list}>
        {CHAPTER_LABELS.map((label, i) => (
          <li key={label} className="contents">
            <button
              type="button"
              className={`${styles.item} ${active === i ? styles.active : ''}`}
              aria-current={active === i ? 'step' : undefined}
              onClick={() => chapterStore.jump(i)}
            >
              <span className={styles.label}>
                <span className={styles.index}>{String(i + 1).padStart(2, '0')}</span>
                <span>{label}</span>
              </span>
              <span className={styles.marker} aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}
