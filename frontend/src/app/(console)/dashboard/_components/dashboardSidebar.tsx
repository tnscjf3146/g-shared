'use client'

import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState, useSyncExternalStore } from 'react'
import { logout } from '@/features/auth/api'

// 왼쪽 사이드바 — 접힌 아이콘 레일(56px) ↔ 펼침(기본 220px, 오른쪽 가장자리를 끌어 조절, 최대 화면 폭의 1/4). 상태·폭은 localStorage. 메뉴는 실제 있는 화면만(작업 › 워크로드 등록 · 공급 › GPU 등록하기 · 지갑 · 홈)
const KEY = 'gshared.sidebar'
const WIDTH_KEY = 'gshared.sidebar.width'
const RAIL = 56
const DEFAULT_WIDTH = 220
const MIN_WIDTH = 180
const maxWidth = () => Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 4)) // 리미트: 화면 폭의 1/4
const clampWidth = (w: number) => Math.min(maxWidth(), Math.max(MIN_WIDTH, Math.round(w)))
const ICON = 'width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'
// 접힘 상태는 localStorage — useSyncExternalStore 로 읽어 effect 안 setState 없이 하이드레이션(서버 false → 클라이언트 값)
const subscribe = (cb: () => void) => {
  window.addEventListener('storage', cb)
  window.addEventListener('resize', cb) // 창이 줄면 1/4 리미트도 줄어든다
  return () => {
    window.removeEventListener('storage', cb)
    window.removeEventListener('resize', cb)
  }
}
const readOpen = () => {
  try {
    return localStorage.getItem(KEY) === 'open'
  } catch {
    return false
  }
}
const readWidth = () => {
  try {
    const n = Number(localStorage.getItem(WIDTH_KEY))
    return clampWidth(n > 0 ? n : DEFAULT_WIDTH)
  } catch {
    return DEFAULT_WIDTH
  }
}
const writeWidth = (w: number) => {
  try {
    localStorage.setItem(WIDTH_KEY, String(clampWidth(w)))
  } catch {
    /* 저장소 불가 환경이면 폭 유지 */
  }
  window.dispatchEvent(new Event('storage'))
}
// 상위 항목을 누르면 그 화면으로 가면서 하위 메뉴가 펼쳐진다(현재 위치가 그 구역이면 펼침 — 별도 상태 없음)
type Item = { href: string; label: string; d: string; children?: Item[] }
const ITEMS: Item[] = [
  { href: '/dashboard/jobs', label: '작업', d: 'M3 3h8v8H3zM13 3h8v5h-8zM13 12h8v9h-8zM3 15h8v6H3z', children: [{ href: '/jobs/new', label: '워크로드 등록', d: 'M12 5v14M5 12h14' }] }, // 내가 맡긴 학습 작업
  { href: '/dashboard/workers', label: '공급', d: 'M3 5h18v11H3zM8 20h8M12 16v4', children: [{ href: '/workers/new', label: 'GPU 등록하기', d: 'M12 3v12M6 9l6 6 6-6M4 21h16' }] }, // 내 PC(워커)
  { href: '/wallet', label: '지갑', d: 'M3 7h18v12H3zM3 11h18M16 15h2' },
]

export function DashboardSidebar() {
  const pathname = usePathname()
  const router = useRouter()
  // 현재 항목: 작업 = /dashboard/jobs + 작업 상세(/jobs/:id), 나머지는 경로 일치
  const isCurrent = (href: string) => (href === '/dashboard/jobs' ? pathname === href || /^\/jobs\/\d+/.test(pathname) : pathname === href)
  const open = useSyncExternalStore(subscribe, readOpen, () => false)
  const width = useSyncExternalStore(subscribe, readWidth, () => DEFAULT_WIDTH)
  const [pending, setPending] = useState(false)
  const [dragging, setDragging] = useState(false) // 끄는 동안은 width 전환 애니메이션을 끈다
  // 오른쪽 가장자리 드래그 — 사이드바는 x=0 에서 시작하므로 clientX 가 곧 폭
  const startDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
  }
  const onDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragging) writeWidth(e.clientX)
  }
  const endDrag = () => setDragging(false)
  const toggle = () => {
    try {
      localStorage.setItem(KEY, open ? 'closed' : 'open')
    } catch {
      /* 저장소 불가 환경이면 접힌 상태 유지 */
    }
    window.dispatchEvent(new Event('storage')) // 같은 탭은 storage 이벤트가 안 나므로 직접 알림
  }
  const label = (text: string) => <span className={`whitespace-nowrap text-[16px] transition-opacity ${open ? 'opacity-100 delay-100' : 'pointer-events-none opacity-0'}`}>{text}</span>
  const item = 'flex h-10 items-center gap-3 rounded-md px-[9px] text-dark transition-colors hover:bg-black/5'

  return (
    <nav aria-label="주 메뉴" style={{ width: open ? width : RAIL }} className={`relative sticky top-0 flex h-dvh flex-none flex-col overflow-hidden border-r border-hairline bg-surface-1 ${dragging ? '' : 'transition-[width] duration-250 ease-out-expo'}`}>
      {/* 크기 조절 핸들 — 펼침 상태에서만. 더블클릭하면 기본 폭 */}
      {open && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="사이드바 폭 조절"
          aria-valuemin={MIN_WIDTH}
          aria-valuenow={width}
          title="끌어서 폭 조절 · 더블클릭으로 기본 폭"
          onPointerDown={startDrag}
          onPointerMove={onDrag}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={() => writeWidth(DEFAULT_WIDTH)}
          className={`absolute inset-y-0 right-0 z-10 w-1.5 cursor-col-resize touch-none transition-colors hover:bg-primary/40 ${dragging ? 'bg-primary/60' : ''}`}
        />
      )}
      {/* 로고 줄 — 펼침: 로고 + 우측 ◀(접기) / 접힘: 화살표만 가운데 ▶(펴기) */}
      <div className="flex h-[60px] flex-none items-center gap-3 border-b border-hairline pr-3 pl-[17px]">
        {open && (
          <Link href="/" aria-label="G-shared 홈" className="flex min-w-0 items-center">
            <Image src="/logo-wordmark.png" alt="G-shared" width={104} height={18} unoptimized className="h-[18px] w-auto" />
          </Link>
        )}
        <button
          type="button"
          onClick={toggle}
          title={open ? '메뉴 접기' : '메뉴 펼치기'}
          aria-expanded={open}
          className={`grid size-8 flex-none place-items-center rounded-md text-text-muted transition-colors hover:bg-black/5 ${open ? 'ml-auto' : 'mx-auto'}`}
        >
          {/* 사이드바.png 예시 — 패널 아이콘(둥근 사각 + 세로선). 접힘 땐 왼쪽 칸을 채워 '펼치기'로 구분 */}
          <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2.5" />
            <path d="M9 4v16" />
            {!open && <rect x="3" y="4" width="6" height="16" rx="2.5" fill="currentColor" stroke="none" />}
          </svg>
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-0.5 px-2 py-2">
        {ITEMS.map((it) => {
          const current = isCurrent(it.href)
          const expanded = !!it.children && (current || it.children.some((c) => isCurrent(c.href)))
          return (
            <div key={it.href} className="flex flex-col gap-0.5">
              <Link href={it.href} title={it.label} aria-current={current ? 'page' : undefined} aria-expanded={it.children ? expanded : undefined} className={`${item} ${current ? 'bg-primary/10 font-semibold text-dark' : ''}`}>
                <span className={`grid size-5 flex-none place-items-center ${current ? 'text-primary' : 'text-text-muted'}`} dangerouslySetInnerHTML={{ __html: `<svg ${ICON}><path d="${it.d}"></path></svg>` }} />
                {label(it.label)}
                {it.children && open && (
                  <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`ml-auto flex-none text-text-muted transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}>
                    <path d="M9 6l6 6-6 6" />
                  </svg>
                )}
              </Link>
              {/* 하위 메뉴 — grid 행 0fr↔1fr 로 펼침·접힘 모션 */}
              {it.children && (
                <div className={`grid transition-[grid-template-rows] duration-250 ease-out-expo motion-reduce:transition-none ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                  <div className="flex min-h-0 flex-col gap-0.5 overflow-hidden">
                    {it.children.map((c) => {
                      const cur = isCurrent(c.href)
                      return (
                        <Link key={c.href} href={c.href} title={c.label} aria-current={cur ? 'page' : undefined} tabIndex={expanded ? undefined : -1} className={`${item} ${open ? 'pl-7' : ''} ${cur ? 'bg-primary/10 font-semibold text-dark' : ''}`}>
                          <span className={`grid size-5 flex-none place-items-center ${cur ? 'text-primary' : 'text-text-muted'}`} dangerouslySetInnerHTML={{ __html: `<svg ${ICON}><path d="${c.d}"></path></svg>` }} />
                          {label(c.label)}
                        </Link>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="flex flex-col gap-0.5 border-t border-hairline px-2 py-2">
        <Link href="/account" title="내 계정" aria-current={pathname === '/account' ? 'page' : undefined} className={`${item} ${pathname === '/account' ? 'bg-primary/10 font-semibold text-dark' : ''}`}>
          <span className="grid size-5 flex-none place-items-center">
            <Image src="/logo-icon.png" alt="" width={20} height={20} unoptimized className="size-5 object-contain" />
          </span>
          {label('내 계정')}
        </Link>
        <button
          type="button"
          title="로그아웃"
          disabled={pending}
          onClick={async () => {
            setPending(true)
            await logout()
            router.replace('/')
          }}
          className={`${item} w-full text-left disabled:opacity-60`}
        >
          <span className="grid size-5 flex-none place-items-center text-text-muted" dangerouslySetInnerHTML={{ __html: `<svg ${ICON}><path d="M15 4H6a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h9"></path><path d="M10 12h11M17.5 8.5 21 12l-3.5 3.5"></path></svg>` }} />
          {label(pending ? '로그아웃 중…' : '로그아웃')}
        </button>
      </div>
    </nav>
  )
}
