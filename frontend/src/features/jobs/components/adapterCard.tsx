'use client'

import Link from 'next/link'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { renameAdapter } from '../api'
import { jobMessageFor } from '../errors'
import { adapterFileName, adapterLabel } from '../format'
import { ADAPTER_NAME_MAX, adapterNameSchema } from '../schema'
import type { JobDetail } from '../types'

// 상세의 [어댑터] 카드 — 학습 결과물의 이름·버전, 이름 변경, 다운로드, 해시 복사, 이어받은 작업 링크.
// 이름은 그대로 다운로드 파일 이름이 되므로(adapterFileName) 바꾸면 받는 파일 이름도 바뀐다.
// ponytail: 같은 오리진일 때만 <a download> 가 먹는다 — 외부 스토리지면 서버가 Content-Disposition 을 붙여야 한다(job.md §11)
/** 결과물 없이 끝난 상태 — 이때는 "끝나면 받을 수 있습니다" 안내가 거짓이 된다 */
const ENDED: ReadonlySet<string> = new Set(['FAILED', 'CANCELLED'])

type Props = { job: JobDetail; onRenamed: (job: JobDetail) => void }

export function AdapterCard({ job, onRenamed }: Props) {
  const [editing, setEditing] = useState<string | null>(null) // null 이면 보기 모드, 문자열이면 편집 중인 값
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const parsed = editing === null ? null : adapterNameSchema.safeParse(editing.trim())
  const invalid = parsed && !parsed.success ? (parsed.error.issues[0]?.message ?? '어댑터 이름을 확인해 주세요.') : null

  const save = async () => {
    if (editing === null || invalid) return
    setPending(true)
    setError(null)
    try {
      onRenamed(await renameAdapter(job.jobId, editing.trim()))
      setEditing(null)
    } catch (e) {
      setError(jobMessageFor(e))
    } finally {
      setPending(false)
    }
  }

  // 다운로드 파일 이름을 어댑터 이름으로 강제한다. 크로스 오리진 스토리지에선 <a download> 의 파일명이 무시되므로(→ URL 끝 이름) blob 으로 받아 이름을 붙인다.
  // CORS 로 blob 을 못 받으면 새 탭으로 여는 것으로 물러선다 — 그 경우 이름은 서버 Content-Disposition 이 정해야 한다(job.md §11)
  const [downloading, setDownloading] = useState(false)
  const download = async () => {
    if (!job.resultUrl) return
    const name = adapterFileName(job.jobId, job.adapterName, job.adapterVersion, job.resultUrl)
    setDownloading(true)
    try {
      const res = await fetch(job.resultUrl)
      if (!res.ok) throw new Error('download failed')
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = name
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      window.open(job.resultUrl, '_blank', 'noopener')
    } finally {
      setDownloading(false)
    }
  }

  return (
    <section className="card flex flex-col gap-3 px-6 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-[17px] font-bold tracking-heading text-dark">어댑터</h2>
        {job.parentJobId != null && (
          <Link href={`/jobs/${job.parentJobId}`} className="text-[13px] text-primary hover:underline">
            작업 #{job.parentJobId} 에서 이어서 학습 →
          </Link>
        )}
      </div>

      {editing === null ? (
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-[12px] font-medium tracking-eyebrow text-text-muted">어댑터 이름</span>
            <span className="text-[17px] font-semibold break-words text-dark">{adapterLabel(job.jobId, job.adapterName, job.adapterVersion)}</span>
          </div>
          <button type="button" onClick={() => setEditing(job.adapterName ?? '')} className="text-[13px] text-primary hover:underline">
            이름 변경
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="어댑터 이름"
              autoFocus
              value={editing}
              maxLength={ADAPTER_NAME_MAX}
              disabled={pending}
              onChange={(e) => setEditing(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void save()
                if (e.key === 'Escape') setEditing(null)
              }}
              aria-invalid={!!invalid}
              aria-describedby={invalid ? 'adapter-rename-error' : undefined}
              className="max-w-[320px]"
            />
            <Button onClick={() => void save()} disabled={pending || !!invalid || editing.trim().length === 0}>
              {pending ? '저장 중…' : '저장'}
            </Button>
            <Button variant="secondary" onClick={() => setEditing(null)} disabled={pending}>
              취소
            </Button>
          </div>
          {invalid && (
            <p id="adapter-rename-error" role="alert" className="text-[13px] text-danger">
              {invalid}
            </p>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[14px] text-danger">
          {error}
        </p>
      )}

      {job.status === 'COMPLETED' && (job.resultUrl || job.resultHash) ? (
        <div className="flex flex-wrap items-center gap-3">
          {job.resultUrl && (
            <button type="button" onClick={() => void download()} disabled={downloading} className="btn btn-md btn-primary">
              {downloading ? '내려받는 중…' : '어댑터 다운로드'}
            </button>
          )}
          {job.resultHash && (
            <button type="button" title={job.resultHash} onClick={() => navigator.clipboard?.writeText(job.resultHash!).catch(() => {})} className="btn btn-md btn-secondary tabular-nums">
              해시 {job.resultHash.slice(0, 10)}… 복사
            </button>
          )}
        </div>
      ) : (
        <p className="text-[13px] text-text-muted">
          {ENDED.has(job.status) ? '이 작업은 어댑터를 만들지 못하고 끝났습니다.' : '학습이 끝나면 여기에서 어댑터를 내려받을 수 있습니다. 받는 파일 이름은 위 어댑터 이름을 따릅니다.'}
        </p>
      )}
    </section>
  )
}
