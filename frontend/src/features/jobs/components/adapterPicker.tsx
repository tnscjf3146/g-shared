'use client'

import { useEffect, useState } from 'react'
import { Select } from '@/components/ui/select'
import { getJobs } from '../api'
import { adapterLabel, formatDateTime } from '../format'
import type { Catalog, JobSummary } from '../types'
import type { ParentAdapter } from './workloadWizard'

// 2단계 "이어서 학습" 선택기 — 내 완료된 학습(TRAIN_LORA) 작업 목록에서 어댑터 하나를 고른다.
// 어댑터 파일은 서버에 이미 있으므로 사용자가 다시 올릴 필요가 없다(옛 파일 드롭은 서버로 가지도 않았다 — job.md §4-2).
// ponytail: 한 페이지(50건)만 본다. 완료 작업이 그보다 많아지면 검색을 붙일 것
const PAGE_SIZE = 50

type Props = { catalog: Catalog; value: ParentAdapter | null; onChange: (p: ParentAdapter | null) => void; describedBy?: string }

export function AdapterPicker({ catalog, value, onChange, describedBy }: Props) {
  const [rows, setRows] = useState<JobSummary[] | 'loading' | 'error'>('loading')

  useEffect(() => {
    let alive = true
    const trainIds = new Set(catalog.workloads.filter((w) => w.kind === 'TRAIN_LORA').map((w) => w.id))
    getJobs({ status: 'COMPLETED', size: PAGE_SIZE })
      .then((page) => alive && setRows(page.content.filter((j) => trainIds.has(j.workloadId))))
      .catch(() => alive && setRows('error'))
    return () => {
      alive = false
    }
  }, [catalog])

  if (rows === 'loading') return <div aria-busy aria-label="완료된 학습 작업 불러오는 중" className="h-10 animate-pulse rounded-md bg-neutral-tint" />
  if (rows === 'error')
    return (
      <p role="alert" className="text-caption text-danger">
        완료된 학습 작업을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.
      </p>
    )
  if (rows.length === 0) return <p className="text-caption text-text-muted">완료된 학습 작업이 아직 없습니다. 처음부터 학습하면 v1 어댑터가 만들어집니다.</p>

  return (
    <Select
      aria-label="이어서 학습할 어댑터"
      aria-describedby={describedBy}
      value={value ? String(value.jobId) : ''}
      onChange={(e) => {
        const row = rows.find((j) => String(j.jobId) === e.target.value)
        onChange(row ? { jobId: row.jobId, name: row.adapterName ?? null, version: row.adapterVersion ?? null } : null)
      }}
    >
      <option value="">어댑터를 선택해 주세요</option>
      {rows.map((j) => (
        <option key={j.jobId} value={j.jobId}>
          {adapterLabel(j.jobId, j.adapterName, j.adapterVersion)} · #{j.jobId} · {formatDateTime(j.createdAt)}
        </option>
      ))}
    </Select>
  )
}
