import { useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import './App.css'

const CATEGORIES = ['누락', '불일치', '형식수정', '확인 필요', '정상'] as const
type Category = (typeof CATEGORIES)[number]
type Decision = 'pending' | 'approved' | 'edited' | 'excluded'

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
  decision: Decision
  ai_assisted: boolean
  confidence: number
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
  const [check, setCheck] = useState<CreatedCheck | null>(null)
  const [saving, setSaving] = useState<Set<string>>(new Set())

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
    setCheck(null)
    setProgress(2)
    setStage('파일을 업로드하고 있습니다.')
    try {
      const form = new FormData()
      form.append('files', file)
      const response = await fetch('/api/v1/checks', { method: 'POST', body: form })
      if (!response.ok) throw new Error(await apiError(response))
      const check = (await response.json()) as CreatedCheck
      setCheck(check)
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

  function editMemo(resultId: string, memoText: string) {
      setResults((current) =>
        current.map((result) =>
          result.id === resultId
            ? { ...result, memo_text: memoText, decision: 'edited' }
            : result,
        ),
      )
      setCopied((current) => {
        const next = new Set(current)
        next.delete(resultId)
        return next
      })
    }

  async function patchResult(
      result: Result,
      patch: { memo_text?: string; decision?: Decision },
    ) {
      if (!check) return
      setSaving((current) => new Set(current).add(result.id))
      setError('')
      try {
        const response = await fetch(
          `/api/v1/checks/${check.id}/results/${result.id}`,
          {
            method: 'PATCH',
            headers: {
              Authorization: `Bearer ${check.access_token}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify(patch),
          },
        )
        if (!response.ok) throw new Error(await apiError(response))
        const updated = (await response.json()) as Result
        setResults((current) =>
          current.map((item) => (item.id === updated.id ? updated : item)),
        )
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '결과를 저장하지 못했습니다.')
      } finally {
        setSaving((current) => {
          const next = new Set(current)
          next.delete(result.id)
          return next
        })
      }
    }

  async function downloadApproved() {
      if (!check) return
      const response = await fetch(`/api/v1/checks/${check.id}/export`, {
        headers: { Authorization: `Bearer ${check.access_token}` },
      })
      if (!response.ok) {
        setError(await apiError(response))
        return
      }
      const url = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = '수정-요청서.txt'
      anchor.click()
      URL.revokeObjectURL(url)
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
            <div className="result-actions">
              <p>메모를 검토한 뒤 승인·수정·제외할 수 있습니다.</p>
              <button
                type="button"
                disabled={!results.some((result) => result.decision === 'approved')}
                onClick={() => void downloadApproved()}
              >
                승인 항목 다운로드
              </button>
            </div>
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
                      <div className="labels">
                        <span className="severity">{result.severity}</span>
                        {result.ai_assisted && (
                          <span className="ai-label">
                            AI 보조 · 신뢰도 {Math.round(result.confidence * 100)}%
                          </span>
                        )}
                        <span className={`decision decision-${result.decision}`}>
                          {decisionText(result.decision)}
                        </span>
                      </div>
                      <button className="copy-button" type="button" onClick={() => void copyMemo(result)}>
                        {copied.has(result.id) ? '✓ 복사됨' : '⧉ 복사'}
                      </button>
                    </div>
                    <dl>
                      <div><dt>원문 위치</dt><dd>{locationText(result.location)}</dd></div>
                      <div><dt>발견 내용</dt><dd>{result.finding}</dd></div>
                      <div><dt>근거 규정</dt><dd>{result.rule_id} · {result.rule_source.document_name} {result.rule_source.clause_number ?? result.rule_source.section_title}</dd></div>
                    </dl>
                    <label className="memo-label" htmlFor={`memo-${result.id}`}>저자용 수정 요청 문구</label>
                    <textarea
                      id={`memo-${result.id}`}
                      className="memo"
                      maxLength={500}
                      value={result.memo_text}
                      onChange={(event) => editMemo(result.id, event.target.value)}
                    />
                    <div className="review-actions">
                      <button
                        type="button"
                        disabled={saving.has(result.id)}
                        onClick={() => void patchResult(result, { memo_text: result.memo_text })}
                      >
                        수정 저장
                      </button>
                      <button
                        className="approve"
                        type="button"
                        disabled={saving.has(result.id)}
                        onClick={() => void patchResult(result, { memo_text: result.memo_text, decision: 'approved' })}
                      >
                        승인
                      </button>
                      <button
                        className="exclude"
                        type="button"
                        disabled={saving.has(result.id)}
                        onClick={() => void patchResult(result, { decision: 'excluded' })}
                      >
                        제외
                      </button>
                    </div>
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
  if (location.reference_index !== null) {
    const context = location.display_hint ? ` (${location.display_hint})` : ''
    return `참고문헌 ${location.reference_index + 1}번째 항목${context}`
  }

  if (location.display_hint.startsWith('본문 인용 ')) return location.display_hint
  const context = location.display_hint ? ` · “${location.display_hint}”` : ''
  return `본문 ${location.paragraph_index + 1}번째 문단${context}`
}

function decisionText(decision: Decision): string {
  return {
    pending: '검토 전',
    approved: '승인',
    edited: '수정됨',
    excluded: '제외',
  }[decision]
}

export default App
