'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getMe, updateNickname } from '@/features/auth/api'
import { messageFor } from '@/features/auth/errors'
import { nicknameSchema } from '@/features/auth/schema'
import { getWallet } from '@/features/wallet/api'
import type { Me } from '@/features/auth/types'
import type { Wallet } from '@/features/wallet/types'

// 내 계정 — 프로필 카드(닉네임 인라인 수정) + 지갑 연결 상태 카드. 데이터는 GET /users/me · GET /users/me/wallet
type Loaded<T> = { status: 'loading' } | { status: 'error' } | { status: 'ready'; data: T }

export function AccountView() {
  const [me, setMe] = useState<Loaded<Me>>({ status: 'loading' })
  const [wallet, setWallet] = useState<Loaded<Wallet>>({ status: 'loading' })

  const load = useCallback(() => {
    getMe()
      .then((m) => setMe({ status: 'ready', data: m }))
      .catch(() => setMe({ status: 'error' }))
    getWallet()
      .then((w) => setWallet({ status: 'ready', data: w }))
      .catch(() => setWallet({ status: 'error' }))
  }, [])
  useEffect(() => void load(), [load])

  return (
    <>
      <h1 className="text-h1 font-bold tracking-heading text-dark">내 계정</h1>

      <section className="card animate-enter flex flex-col gap-6 p-8">
        <h2 className="text-[17px] font-bold tracking-heading text-dark">프로필</h2>
        {me.status === 'loading' && <div className="h-32 animate-pulse rounded-md bg-neutral-tint" />}
        {me.status === 'error' && (
          <p className="text-[15px] text-dark">
            프로필을 불러오지 못했습니다.{' '}
            <button type="button" onClick={() => { setMe({ status: 'loading' }); load() }} className="text-primary hover:underline">다시 시도 →</button>
          </p>
        )}
        {me.status === 'ready' && (
          <dl className="flex flex-col gap-5">
            {/* 백엔드에 별도 아이디 개념이 없다 — 이메일이 곧 로그인 아이디 */}
            <Row label="아이디 (이메일)">
              <span className="text-[16px] text-dark">{me.data.email}</span>
            </Row>
            <Row label="닉네임">
              <NicknameEditor initial={me.data.nickname} onSaved={load} />
            </Row>
            {/* 지갑은 상태 한 줄만 — 연결/해제 관리는 지갑 페이지 담당 */}
            <Row label="지갑">
              <div className="flex min-w-0 flex-wrap items-center gap-3">
                {wallet.status === 'loading' && <span className="h-5 w-40 animate-pulse rounded-xs bg-neutral-tint" />}
                {wallet.status === 'error' && (
                  <span className="text-[15px] text-text-muted">
                    지갑 정보를 불러오지 못했습니다.{' '}
                    <button type="button" onClick={() => { setWallet({ status: 'loading' }); load() }} className="text-primary hover:underline">다시 시도 →</button>
                  </span>
                )}
                {wallet.status === 'ready' &&
                  (wallet.data.walletAddress ? (
                    <span className="min-w-0 truncate font-mono text-[15px] text-dark" title={wallet.data.walletAddress}>{wallet.data.walletAddress}</span>
                  ) : (
                    <span className="text-[15px] text-text-muted">연결 안 됨 — 예치·정산에 필요합니다</span>
                  ))}
                <Link href="/wallet" className="flex-none text-[14px] text-primary hover:underline">지갑 관리 →</Link>
              </div>
            </Row>
          </dl>
        )}
      </section>
    </>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)] items-center gap-4">
      <dt className="text-[14px] font-semibold text-text-muted">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  )
}

// 닉네임 인라인 수정 — 보기 상태(값 + 변경 버튼) ↔ 편집 상태(입력 + 저장/취소). 검증은 회원가입과 같은 nicknameSchema
function NicknameEditor({ initial, onSaved }: { initial: string; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(initial)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!editing) {
    return (
      <div className="flex items-center gap-3">
        <span className="text-[16px] text-dark">{initial}</span>
        <button type="button" onClick={() => { setValue(initial); setError(null); setEditing(true) }} className="text-[14px] text-primary hover:underline">
          변경
        </button>
      </div>
    )
  }
  const save = async () => {
    const r = nicknameSchema.safeParse(value.trim())
    if (!r.success) return setError(r.error.issues[0].message)
    setPending(true)
    try {
      await updateNickname(r.data)
      setEditing(false)
      onSaved()
    } catch (e) {
      setError(messageFor(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <form
      className="flex flex-col gap-1.5"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <div className="flex items-center gap-2">
        <Input value={value} onChange={(e) => setValue(e.target.value)} aria-label="새 닉네임" aria-invalid={error ? true : undefined} maxLength={15} className="max-w-[220px]" autoFocus />
        <Button type="submit" size="sm" disabled={pending}>{pending ? '저장 중…' : '저장'}</Button>
        <Button variant="secondary" size="sm" disabled={pending} onClick={() => setEditing(false)}>취소</Button>
      </div>
      {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
    </form>
  )
}
