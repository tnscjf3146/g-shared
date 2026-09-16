import { Button } from '@/components/ui/button'
import { formatCost, formatDuration } from '../format'
import type { Estimate } from '../types'

// 견적 결과(job.md §4). stale = 견적 뒤 입력이 바뀜 → 회색 처리, 생성 불가
// onCreate 가 없으면 버튼 없이 견적만 보여 준다(4단계). 5단계에서 onCreate 로 등록 버튼을 붙인다
export function EstimateCard({ estimate, stale, pending = false, onCreate, onEditConfig, createLabel = '작업 만들기' }: { estimate: Estimate; stale: boolean; pending?: boolean; onCreate?: () => void; onEditConfig?: () => void; createLabel?: string }) {
  const c = estimate.trainingConfig
  return (
    <section className={`card card-featured animate-enter flex flex-col gap-5 ${stale ? 'opacity-60' : ''}`} aria-live="polite">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[16px] font-bold text-dark">견적</h3>
        {stale && <span className="text-[13px] text-warning">입력이 바뀌었습니다. 견적을 다시 받아 주세요.</span>}
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          ['에폭', c.epochs],
          ['배치 크기', c.batchSize],
          ['학습률', c.learningRate],
          ['최대 길이', c.maxLength],
        ].map(([k, v]) => (
          <div key={String(k)} className="flex flex-col gap-0.5">
            <span className="text-[12px] text-text-muted">{k}</span>
            <span className="text-[15px] font-semibold tabular-nums text-dark">{String(v)}</span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-end justify-between gap-4 border-t border-hairline pt-4">
        <div className="flex gap-8">
          <div className="flex flex-col gap-0.5">
            <span className="text-[12px] text-text-muted">예상 소요</span>
            <span className="text-h2 font-bold tabular-nums tracking-heading text-dark">{formatDuration(estimate.predictedRuntimeSec)}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-[12px] text-text-muted">예상 비용</span>
            <span className="text-h2 font-bold tabular-nums tracking-heading text-primary">{formatCost(estimate.estimatedCost)}</span>
          </div>
        </div>
        {(onCreate || onEditConfig) && (
          <div className="flex gap-2">
            {onEditConfig && (
              <Button variant="secondary" disabled={stale} onClick={onEditConfig}>
                이 설정 직접 고치기
              </Button>
            )}
            {onCreate && (
              <Button size="lg" disabled={stale || pending} onClick={onCreate}>
                {pending && <span aria-hidden className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
                {pending ? '업로드·등록 중…' : createLabel}
              </Button>
            )}
          </div>
        )}
      </div>
      <p className="text-[12px] text-text-muted">견적은 데이터셋 분석(근사 토큰 수) 기준이며 실행 시 서버가 다시 계산합니다. 비용은 예치 시점에 GSC 로 결제되고 실제 소요에 따라 정산됩니다.</p>
    </section>
  )
}
