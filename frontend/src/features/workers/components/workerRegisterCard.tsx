import { WORKER_DOWNLOAD_URL } from '../api'

// 웹이 맡는 "워커 등록" = 안내(worker.md §0·§4). 실제 등록은 G-shared 앱이 GPU 를 읽고 앱 안에서 로그인해 수행한다.
// 서버 컴포넌트로도 쓸 수 있게 상태 없음. 목록이 있을 땐 <details> 안에 접어 재사용
const STEPS = [
  { title: 'G-shared 다운로드', body: 'Windows 용 실행 파일 하나입니다. 설치 과정은 없습니다.' },
  { title: '실행해서 GPU 점검', body: '그래픽카드와 드라이버를 확인하고 실행 환경을 준비합니다. 첫 실행은 몇 분 걸릴 수 있고 관리자 권한을 요청합니다.' },
  { title: '앱 창에서 이 계정으로 로그인', body: '로그인하면 이 PC 가 내 워커로 등록되고, 이 페이지에 자동으로 나타납니다.' },
]

export function WorkerRegisterCard({ compact = false }: { compact?: boolean }) {
  return (
    <section className={`card animate-enter flex w-full flex-col gap-6 ${compact ? '' : 'max-w-[640px] p-10'}`}>
      {!compact && (
        <div className="flex flex-col gap-2">
          <h2 className="text-h2 font-bold tracking-heading text-dark">아직 등록된 워커가 없습니다</h2>
          <p className="text-[15px] text-text-muted">PC 에 G-shared 를 받아 실행하고 로그인하면 그 PC 가 이 계정의 워커로 등록됩니다.</p>
        </div>
      )}

      <ol className="flex flex-col gap-4">
        {STEPS.map((s, i) => (
          <li key={s.title} className="flex gap-4">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-pill bg-primary/10 text-[13px] font-bold tabular-nums text-primary">{i + 1}</span>
            <div className="flex flex-col gap-0.5">
              <span className="text-[15px] font-semibold text-dark">{s.title}</span>
              <span className="text-[14px] text-text-muted">{s.body}</span>
            </div>
          </li>
        ))}
      </ol>

      {/* exe 는 브라우저가 알아서 저장 — download 속성 불필요. 외부 오리진일 수 있어 noreferrer */}
      <a href={WORKER_DOWNLOAD_URL} rel="noreferrer" className="btn btn-lg btn-primary w-full">
        G-shared 다운로드 (Windows)
      </a>

      <p className="text-[12px] text-text-muted">요구사항: Windows 10/11 · NVIDIA GPU(드라이버 설치됨) · 인터넷 연결. 등록이 끝나면 새로고침 없이 15초 안에 여기 나타납니다.</p>
    </section>
  )
}
