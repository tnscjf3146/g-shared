import { setupWorker } from 'msw/browser'
import { handlers } from './handlers'

export const worker = setupWorker(...handlers)

let started: Promise<unknown> | undefined

// React StrictMode 이중 effect·HMR에서도 start()는 한 번만 (두 번 부르면 "cannot configure an already enabled network")
export function startWorker() {
  started ??= worker.start({ onUnhandledRequest: 'bypass' })
  return started
}
