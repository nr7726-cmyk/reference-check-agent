import { useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import './App.css'

const CATEGORIES = ['누락', '불일치', '형식수정', '확인 필요', '정상'] as const
type Category = (typeof CATEGORIES)[number]

type Location = {
  section_label: string
  section_index: number
  paragraph_index: number
  reference_index: number | null
  display_hint: string
}

type Result = {
  id: string
  category: Category
  severity: string
  location: Location
  finding: string
  memo_text: string
  rule_id: string
  rule_source: {
    document_name: string
    clause_number: string | null
    section_title: string
  }
}

type CreatedCheck = {
  id: string
  access_token: string
  events_url: string
}

type SseEvent = { event: string; data: Record<string, unknown> }

function App() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(0)
  const [stage, setStage] = useState('원고를 업로드해 검사를 시작하세요.')
  const [error, setError] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [copied, setCopied] = useState<Set<string>>(new Set())

  const grouped = useMemo(
    () =>
      Object.fromEntries(
        CATEGORIES.map((category) => [
          category,
          results.filter((result) => result.category === category),
        ]),
      ) as Record<Category, Result[]>,
    [results],
  )

  async function startCheck(file: File) {
    setError('')
    if (!/\.(hwp|hwpx)$/i.test(file.name)) {
      setError('HWP 또는 HWPX 파일만 업로드할 수 있습니다.')
      return
    }
    if (file.size > 30 * 1024 * 1024) {
      setError('파일 크기는 30MB 이하여야 합니다.')
      return
    }
    setBusy(true)
    setResults([])
    setCopied(new Set())
    setProgress(2)
    setStage('파일을 업로드하고 있습니다.')
    try {
      const form = new FormData()
      form.append('files', file)
      const response = await fetch('/api/v1/checks', { method: 'POST', body: form })
      if (!response.ok) throw new Error(await apiError(response))
      const check = (await response.json()) as CreatedCheck
      await consumeEvents(check)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '검사를 시작하지 못했습니다.')
      setStage('검사가 중단되었습니다.')
    } finally {
      setBusy(false)
    }
  }

  async function consumeEvents(check: CreatedCheck) {
    const authorization = { Authorization: `Bearer ${check.access_token}` }
    const response = await fetch(check.events_url, { headers: authorization })
    if (!response.ok || !response.body) throw new Error(await apiError(response))
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) handleEvent(parseEvent(frame))
      if (done) break
    }
    const resultResponse = await fetch(`/api/v1/checks/${check.id}/results`, {
      headers: authorization,
    })
    if (!resultResponse.ok) throw new Error(await apiError(resultResponse))
    setResults((await resultResponse.json()) as Result[])
  }

  function handleEvent(message: SseEvent) {
    if (message.event === 'stage_changed') {
      setProgress(Number(message.data.progress ?? 0))
      setStage(String(message.data.message ?? '검사를 진행하고 있습니다.'))
    } else if (message.event === 'result_added') {
      const result = message.data.result as Result
      setResults((current) =>
        current.some((item) => item.id === result.id) ? current : [...current, result],
      )
    } else if (message.event === 'completed') {
      setProgress(100)
      setStage('검사가 완료되었습니다.')
    } else if (message.event === 'failed') {
      throw new Error(String(message.data.message ?? '검사 중 오류가 발생했습니다.'))
    }
  }

  async function copyMemo(result: Result) {
    try {
      await navigator.clipboard.writeText(result.memo_text)
      setCopied((current) => new Set(current).add(result.id))
    } catch {
      setError('복사하지 못했습니다. 아래 메모 문구를 직접 선택해 복사해 주세요.')
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (file) void startCheck(file)
  }

  function onSelect(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (file) void startCheck(file)
    event.target.value = ''
  }

  return (
    <main>
      <header>
        <p className="eyebrow">한국 학술지 편집자를 위한 도구</p>
        <h1>참고문헌 검증</h1>
        <p className="intro">
          본문 인용과 참고문헌을 대조하고, 근거가 포함된 저자용 수정 요청 문구를 만듭니다.
        </p>
      </header>

      <section className="panel upload-panel" aria-labelledby="upload-title">
        <h2 id="upload-title">원고 업로드</h2>
        <div
          className={`drop-zone ${dragging ? 'dragging' : ''}`}
          onDragEnter={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <strong>HWP 또는 HWPX 파일을 여기에 놓으세요</strong>
          <span>1회 1파일 · 최대 30쪽 · 30MB 이하</span>
          <button type="button" disabled={busy} onClick={() => inputRef.current?.click()}>
            {busy ? '검사 중…' : '파일 선택'}
          </button>
          <input
            ref={inputRef}
            className="visually-hidden"
            type="file"
            accept=".hwp,.hwpx"
            onChange={onSelect}
          />
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </section>

      {(busy || progress > 0) && (
        <section className="panel progress-panel" aria-live="polite">
          <div className="progress-label">
            <strong>{stage}</strong><span>{progress}%</span>
          </div>
          <progress max="100" value={progress} />
        </section>
      )}

      {results.length > 0 && (
        <section className="results" aria-labelledby="results-title">
          <div className="results-heading">
            <div><p className="eyebrow">검사 결과</p><h2 id="results-title">{results.length}개 항목</h2></div>
            <p>복사 버튼은 한글 메모에 붙여 넣을 문구만 복사합니다.</p>
          </div>
          {CATEGORIES.map((category) => (
            <details className="category" key={category} open={grouped[category].length > 0}>
              <summary><span>{category}</span><span className="badge">{grouped[category].length}</span></summary>
              <div className="cards">
                {grouped[category].length === 0 ? (
                  <p className="empty">해당 항목이 없습니다.</p>
                ) : grouped[category].map((result) => (
                  <article className={`result-card ${copied.has(result.id) ? 'copied' : ''}`} key={result.id}>
                    <div className="card-top">
                      <span className="severity">{result.severity}</span>
                      <button className="copy-button" type="button" onClick={() => void copyMemo(result)}>
                        {copied.has(result.id) ? '✓ 복사됨' : '⧉ 복사'}
                      </button>
                    </div>
                    <dl>
                      <div><dt>원문 위치</dt><dd>{locationText(result.location)}</dd></div>
                      <div><dt>발견 내용</dt><dd>{result.finding}</dd></div>
                      <div><dt>근거 규정</dt><dd>{result.rule_id} · {result.rule_source.document_name} {result.rule_source.clause_number ?? result.rule_source.section_title}</dd></div>
                    </dl>
                    <p className="memo" tabIndex={0}>{result.memo_text}</p>
                  </article>
                ))}
              </div>
            </details>
          ))}
        </section>
      )}
      <footer>AI가 보조한 결과는 최종 편집 판단을 대신하지 않습니다.</footer>
    </main>
  )
}

function parseEvent(frame: string): SseEvent {
  let event = 'message'
  const data: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trim())
  }
  return { event, data: JSON.parse(data.join('\n') || '{}') as Record<string, unknown> }
}

async function apiError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail ?? `요청에 실패했습니다. (${response.status})`
  } catch {
    return `요청에 실패했습니다. (${response.status})`
  }
}

function locationText(location: Location): string {
  if (location.display_hint) return location.display_hint
  const reference = location.reference_index === null ? '' : ` · 참고문헌 ${location.reference_index + 1}`
  return `${location.section_label} · ${location.paragraph_index + 1}문단${reference}`
}

export default App
