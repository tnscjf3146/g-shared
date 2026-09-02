import { delay, http, HttpResponse } from 'msw'
import { API_BASE_URL } from '@/lib/fetcher'
import { jobs } from './data'

export const jobHandlers = [
  http.get(`${API_BASE_URL}/jobs`, async () => {
    await delay()
    return HttpResponse.json(jobs)
  }),

  http.get(`${API_BASE_URL}/jobs/:jobId`, async ({ params }) => {
    await delay()
    const job = jobs.find((j) => j.id === params.jobId)
    if (!job) {
      return HttpResponse.json(
        { code: 'JOB_NOT_FOUND', message: `Job ${params.jobId} not found` },
        { status: 404 },
      )
    }
    return HttpResponse.json(job)
  }),
]
