// 파일을 지정한 이름으로 내려받는다. 외부 스토리지(크로스 오리진)면 <a download> 파일명이 무시되므로 blob 으로 받아 이름을 붙인다.
// CORS 로 blob 을 못 받으면 새 탭으로 여는 것으로 물러선다 — 그 경우 이름은 서버 Content-Disposition 이 정해야 한다(job.md §11-2)
export async function downloadFile(url: string, filename: string): Promise<void> {
  try {
    const res = await fetch(url)
    if (!res.ok) throw new Error('download failed')
    const objectUrl = URL.createObjectURL(await res.blob())
    try {
      const a = document.createElement('a')
      a.href = objectUrl
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
    } finally {
      URL.revokeObjectURL(objectUrl) // 성공·실패 어느 쪽이든 반드시 해제
    }
  } catch {
    window.open(url, '_blank', 'noopener')
  }
}
