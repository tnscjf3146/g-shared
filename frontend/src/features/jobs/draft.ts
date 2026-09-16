import type { WizardState } from './components/workloadWizard'

// 워크로드 등록 위저드 초안 — 새로고침해도 하던 단계·선택·파일이 남아 이어서 작성한다.
// File 은 JSON 으로 못 담아 sessionStorage 대신 IndexedDB(structured clone) 에 통째로 넣는다. 등록이 끝나면 지운다.
// ponytail: 브라우저 단위 한 칸 — 같은 PC 에서 다른 계정으로 들어오면 초안이 보인다. 문제되면 KEY 에 userId 를 붙일 것
const DB = 'gshared'
const STORE = 'drafts'
const KEY = 'workload-wizard'

export type Draft = { state: WizardState; step: number; maxReached: number }

function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const open = indexedDB.open(DB, 1)
    // 버전을 올릴 때도 다시 불린다 — 이미 있는 스토어를 다시 만들면 ConstraintError
    open.onupgradeneeded = () => {
      if (!open.result.objectStoreNames.contains(STORE)) open.result.createObjectStore(STORE)
    }
    open.onerror = () => reject(open.error)
    open.onsuccess = () => {
      const db = open.result
      const t = db.transaction(STORE, mode)
      t.oncomplete = t.onabort = () => db.close()
      const req = run(t.objectStore(STORE))
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    }
  })
}

// 초안은 있으면 좋은 것 — 실패해도 등록 흐름은 막지 않는다. 대신 사유는 콘솔에 남겨 디버깅은 되게(사설 창·용량 초과·스토리지 차단 등)
const quiet = (what: string) => (e: unknown) => {
  console.warn(`[draft] ${what} 실패`, e)
  return undefined
}
export const loadDraft = (): Promise<Draft | undefined> => tx<Draft | undefined>('readonly', (s) => s.get(KEY)).catch(quiet('불러오기'))
export const saveDraft = (d: Draft): Promise<void> => tx('readwrite', (s) => s.put(d, KEY)).then(() => undefined, quiet('저장'))
export const clearDraft = (): Promise<void> => tx('readwrite', (s) => s.delete(KEY)).then(() => undefined, quiet('삭제'))
