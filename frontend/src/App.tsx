import { useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent, KeyboardEvent } from 'react'
import './App.css'

const CATEGORIES = ['누락', '불일치', '형식수정', '확인 필요', '정상'] as const
const INITIAL_STAGE = '원고를 업로드해 검사를 시작하세요.'
type Category = (typeof CATEGORIES)[number]
type CategoryTab = '전체' | Category
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
  original_memo_text: string
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

type SseEvent = { id: number | null; event: string; data: Record<string, unknown> }
type FailedCheck = {
  code: string
  stage: string
  retryable: boolean
  message: string
}

const MAX_STREAM_RECONNECTS = 4

class CheckFailedError extends Error {
  failure: FailedCheck

  constructor(failure: FailedCheck) {
    super(failure.message)
    this.failure = failure
  }
}

function App() {
  const inputRef = useRef<HTMLInputElement>(null)
  const resultsListRef = useRef<HTMLDivElement>(null)
  const requestControllerRef = useRef<AbortController | null>(null)
  const eventReaderRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)
  const lastFileRef = useRef<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(0)
  const [stage, setStage] = useState(INITIAL_STAGE)
  const [error, setError] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [copied, setCopied] = useState<Set<string>>(new Set())
  const [check, setCheck] = useState<CreatedCheck | null>(null)
  const [saving, setSaving] = useState<Set<string>>(new Set())
  const [dirty, setDirty] = useState<Set<string>>(new Set())
  const [reconnecting, setReconnecting] = useState(false)
  const [failure, setFailure] = useState<FailedCheck | null>(null)
  const [copyFallback, setCopyFallback] = useState<{ label: string; text: string } | null>(null)
  const [activeCategory, setActiveCategory] = useState<CategoryTab>('전체')
  const [reviewed, setReviewed] = useState<Set<string>>(new Set())

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
    setFailure(null)
    setCopyFallback(null)
    if (!/\.(hwp|hwpx)$/i.test(file.name)) {
      setError('HWP 또는 HWPX 파일만 업로드할 수 있습니다.')
      return
    }
    if (file.size > 30 * 1024 * 1024) {
      setError('파일 크기는 30MB 이하여야 합니다.')
      return
    }
    setBusy(true)
    lastFileRef.current = file
    setResults([])
    setCopied(new Set())
    setDirty(new Set())
    setSaving(new Set())
    setReviewed(new Set())
    setActiveCategory('전체')
    setCheck(null)
    setProgress(2)
    setStage('파일을 업로드하고 있습니다.')
    const controller = new AbortController()
    requestControllerRef.current = controller
    try {
      const form = new FormData()
      form.append('files', file)
      const response = await fetch('/api/v1/checks', {
        method: 'POST',
        body: form,
        signal: controller.signal,
      })
      if (!response.ok) throw new Error(await apiError(response))
      const check = (await response.json()) as CreatedCheck
      setCheck(check)
      await consumeEvents(check, controller.signal)
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      if (reason instanceof CheckFailedError) {
        setFailure(reason.failure)
        setError(reason.failure.message)
        setStage('검사에 실패했습니다.')
      } else {
        setError(reason instanceof Error ? reason.message : '검사를 시작하지 못했습니다.')
        setStage('검사가 중단되었습니다.')
      }
    } finally {
      if (requestControllerRef.current === controller) {
        requestControllerRef.current = null
        setBusy(false)
      }
    }
  }

  async function consumeEvents(check: CreatedCheck, signal: AbortSignal) {
    const authorization = { Authorization: `Bearer ${check.access_token}` }
    let lastEventId = 0
    let completed = false
    for (let attempt = 0; attempt <= MAX_STREAM_RECONNECTS && !completed; attempt += 1) {
      try {
        const headers: Record<string, string> = { ...authorization }
        if (lastEventId > 0) headers['Last-Event-ID'] = String(lastEventId)
        const response = await fetch(check.events_url, { headers, signal })
        if (!response.ok || !response.body) throw new Error(await apiError(response))
        const reader = response.body.getReader()
        eventReaderRef.current = reader
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { value, done } = await reader.read()
          buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
          const frames = buffer.split('\n\n')
          buffer = frames.pop() ?? ''
          for (const frame of frames) {
            const message = parseEvent(frame)
            if (message.id !== null) lastEventId = Math.max(lastEventId, message.id)
            const outcome = handleEvent(message)
            if (outcome === 'completed') completed = true
            if (outcome && outcome !== 'completed') throw new CheckFailedError(outcome)
          }
          if (done || completed) break
        }
        if (eventReaderRef.current === reader) eventReaderRef.current = null
        if (!completed && attempt < MAX_STREAM_RECONNECTS) {
          setReconnecting(true)
          setStage('연결이 끊겨 검사 진행 상태를 다시 연결하고 있습니다.')
          await waitForReconnect(attempt, signal)
        }
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
        eventReaderRef.current = null
        if (reason instanceof CheckFailedError) throw reason
        if (attempt >= MAX_STREAM_RECONNECTS) throw reason
        setReconnecting(true)
        setStage('연결이 끊겨 검사 진행 상태를 다시 연결하고 있습니다.')
        await waitForReconnect(attempt, signal)
      }
    }
    setReconnecting(false)
    if (!completed) throw new Error('검사 진행 연결을 복구하지 못했습니다.')
    const resultResponse = await fetch(`/api/v1/checks/${check.id}/results`, {
      headers: authorization,
      signal,
    })
    if (!resultResponse.ok) throw new Error(await apiError(resultResponse))
    setResults((await resultResponse.json()) as Result[])
  }

  function handleEvent(message: SseEvent): 'completed' | FailedCheck | null {
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
      setReconnecting(false)
      return 'completed'
    } else if (message.event === 'failed') {
      const failedCheck: FailedCheck = {
        code: String(message.data.code ?? 'CHECK_FAILED'),
        stage: String(message.data.stage ?? 'unknown'),
        retryable: Boolean(message.data.retryable),
        message: String(message.data.message ?? '검사 중 오류가 발생했습니다.'),
      }
      return failedCheck
    }
    return null
  }

  async function copyMemo(result: Result) {
    if (await copyText(result.memo_text, locationText(result.location))) {
      setCopied((current) => new Set(current).add(result.id))
    }
  }

  async function copyText(text: string, label: string): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(text)
      setCopyFallback(null)
      return true
    } catch {
      setCopyFallback({ label, text })
      return false
    }
  }

  function editMemo(resultId: string, memoText: string) {
      setResults((current) =>
        current.map((result) =>
          result.id === resultId
            ? { ...result, memo_text: memoText }
            : result,
        ),
      )
      setCopied((current) => {
        const next = new Set(current)
        next.delete(resultId)
        return next
      })
      setDirty((current) => new Set(current).add(resultId))
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
        setDirty((current) => {
          const next = new Set(current)
          next.delete(result.id)
          return next
        })
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
      if (!confirmFinalization()) return
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

  async function copyApproved(items: Result[], label: string) {
      if (!confirmFinalization()) return
      const text = items
        .filter((result) => result.decision === 'approved')
        .map((result) => result.memo_text)
        .join('\n\n')
      if (text) await copyText(text, label)
  }

  function confirmFinalization(): boolean {
      const count = results.filter(
        (result) => result.category === '확인 필요' && result.decision === 'pending',
      ).length
      return count === 0 || window.confirm(
        `확인 필요 항목 ${count}건이 아직 검토되지 않았습니다. 계속하시겠습니까?`,
      )
  }

  function selectCategory(category: CategoryTab) {
      setActiveCategory(category)
      window.requestAnimationFrame(() => {
        resultsListRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
  }

  function onCategoryKeyDown(event: KeyboardEvent<HTMLButtonElement>, category: CategoryTab) {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault()
      const tabs: CategoryTab[] = [
        '전체',
        ...CATEGORIES.filter((item) => grouped[item].length > 0),
      ]
      const direction = event.key === 'ArrowRight' ? 1 : -1
      const index = tabs.indexOf(category)
      const next = tabs[(index + direction + tabs.length) % tabs.length]
      selectCategory(next)
      document.getElementById(`category-tab-${next}`)?.focus()
  }

  function toggleReviewed(resultId: string) {
      setReviewed((current) => {
        const next = new Set(current)
        if (next.has(resultId)) next.delete(resultId)
        else next.add(resultId)
        return next
      })
  }

  async function cancelCheck() {
      if (!check || !busy || !window.confirm('진행 중인 검사를 취소하시겠습니까?')) return
      try {
        const response = await fetch(`/api/v1/checks/${check.id}/cancel`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${check.access_token}` },
        })
        if (!response.ok) throw new Error(await apiError(response))
        requestControllerRef.current?.abort()
        await eventReaderRef.current?.cancel().catch(() => undefined)
        eventReaderRef.current = null
        setBusy(false)
        setProgress(0)
        setStage('검사가 취소되었습니다.')
        setError('')
        setFailure(null)
        setReconnecting(false)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '검사를 취소하지 못했습니다.')
      }
  }

  async function retryLastCheck() {
      const file = lastFileRef.current
      if (!file) {
        inputRef.current?.click()
        return
      }
      requestControllerRef.current?.abort()
      await eventReaderRef.current?.cancel().catch(() => undefined)
      eventReaderRef.current = null
      void startCheck(file)
  }

  async function resetToUpload(): Promise<boolean> {
      const hasEditorWork = results.some(
        (result) =>
          result.decision !== 'pending'
          || result.memo_text !== result.original_memo_text,
      )
      if (
        hasEditorWork
        && !window.confirm('현재 검사 결과가 사라집니다. 계속하시겠습니까?')
      ) {
        return false
      }
      requestControllerRef.current?.abort()
      requestControllerRef.current = null
      if (eventReaderRef.current) {
        await eventReaderRef.current.cancel().catch(() => undefined)
        eventReaderRef.current = null
      }
      setResults([])
      setCheck(null)
      setBusy(false)
      setError('')
      setProgress(0)
      setCopied(new Set())
      setSaving(new Set())
      setDirty(new Set())
      setReconnecting(false)
      setFailure(null)
      setCopyFallback(null)
      setActiveCategory('전체')
      setReviewed(new Set())
      setStage(INITIAL_STAGE)
      setDragging(false)
      lastFileRef.current = null
      if (inputRef.current) inputRef.current.value = ''
      window.scrollTo({ top: 0, behavior: 'smooth' })
      return true
  }

  async function startAnotherFile(file: File) {
      if ((check || results.length > 0) && !await resetToUpload()) return
      await startCheck(file)
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (file) void startAnotherFile(file)
  }

  function onSelect(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (file) void startAnotherFile(file)
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
        {error && (
          <div className="error-actions">
            <p className="error" role="alert">{error}</p>
            {failure && (
              <p className="failure-detail">
                실패 단계: {failure.stage} · 오류 코드: {failure.code} ·
                {failure.retryable ? ' 재시도 가능' : ' 다른 파일 확인 필요'}
              </p>
            )}
            {(!failure || failure.retryable) && (
              <button type="button" disabled={busy} onClick={() => void retryLastCheck()}>
                다시 시도
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              aria-label="새 원고 검사"
              onClick={() => void resetToUpload()}
            >
              새 원고 검사
            </button>
          </div>
        )}
      </section>

      {(busy || progress > 0) && (
        <section className="panel progress-panel" aria-live="polite">
          <div className="progress-label">
            <strong>{stage}</strong><span>{progress}%</span>
          </div>
          <progress max="100" value={progress} />
          {reconnecting && <p className="reconnecting">연결 재시도 중…</p>}
          {busy && check && (
            <button className="cancel-button" type="button" onClick={() => void cancelCheck()}>
              검사 취소
            </button>
          )}
        </section>
      )}

      <p className="visually-hidden" aria-live="polite">
        {reconnecting ? '검사 진행 연결을 다시 시도하고 있습니다.' : stage}
      </p>

      {results.length > 0 && (
        <section className="results" aria-labelledby="results-title">
          <div className="results-heading">
            <div><p className="eyebrow">검사 결과</p><h2 id="results-title">{results.length}개 항목</h2></div>
            <div className="result-actions">
              <p>메모를 검토한 뒤 승인·수정·제외할 수 있습니다.</p>
              {dirty.size > 0 && <p>저장하지 않은 편집본 {dirty.size}건 저장 필요</p>}
              <div>
                <button
                  type="button"
                  disabled={
                    dirty.size > 0
                    || !results.some((result) => result.decision === 'approved')
                  }
                  onClick={() => void copyApproved(results, '전체 승인 항목')}
                >
                  전체 승인 항목 복사
                </button>
                <button
                  type="button"
                  disabled={
                    dirty.size > 0
                    || !results.some((result) => result.decision === 'approved')
                  }
                  onClick={() => void downloadApproved()}
                >
                  승인 항목 다운로드
                </button>
                <button
                  type="button"
                  disabled={busy}
                  aria-label="새 원고 검사"
                  onClick={() => void resetToUpload()}
                >
                  새 원고 검사
                </button>
              </div>
            </div>
          </div>
          <div
            ref={resultsListRef}
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
          >
          <div className="result-progress" aria-live="polite">
            전체 {results.length}건 중 {reviewed.size}건 확인
          </div>
          <div className="category-tabs" role="tablist" aria-label="결과 카테고리">
            {(['전체', ...CATEGORIES.filter((category) => grouped[category].length > 0)] as CategoryTab[])
              .map((category) => {
                const items = category === '전체' ? results : grouped[category]
                const reviewedCount = items.filter((item) => reviewed.has(item.id)).length
                return (
                  <button
                    id={`category-tab-${category}`}
                    key={category}
                    type="button"
                    role="tab"
                    aria-selected={activeCategory === category}
                    tabIndex={activeCategory === category ? 0 : -1}
                    onClick={() => selectCategory(category)}
                    onKeyDown={(event) => onCategoryKeyDown(event, category)}
                  >
                    {category} {items.length}
                    {reviewedCount > 0 && <span> · 확인 {reviewedCount}</span>}
                  </button>
                )
              })}
          </div>
          {copyFallback && (
            <div className="copy-fallback" role="alert">
              <strong>{copyFallback.label} 자동 복사 실패</strong>
              <span>아래 문구를 선택해 직접 복사하세요.</span>
              <textarea
                readOnly
                aria-label="직접 복사할 수정 요청 문구"
                value={copyFallback.text}
                onFocus={(event) => event.currentTarget.select()}
              />
            </div>
          )}
          {CATEGORIES.filter(
            (category) => grouped[category].length > 0
              && (activeCategory === '전체' || activeCategory === category),
          ).map((category) => (
            <details className="category" key={category} open={grouped[category].length > 0}>
              <summary>
                <span>{category}</span>
                <span
                  className="badge"
                  aria-label={`${grouped[category].length}건 중 ${
                    grouped[category].filter((result) => result.decision === 'approved').length
                  }건 승인`}
                >
                  승인 {grouped[category].filter(
                    (result) => result.decision === 'approved',
                  ).length} / {grouped[category].length}
                </span>
              </summary>
              {grouped[category].some((result) => result.decision === 'approved') && (
                <div className="category-actions">
                  <button
                    type="button"
                    disabled={dirty.size > 0}
                    onClick={() => void copyApproved(grouped[category], `${category} 승인 항목`)}
                  >
                    {category} 승인 {grouped[category].filter(
                      (result) => result.decision === 'approved',
                    ).length}건 복사
                  </button>
                </div>
              )}
              <div className="cards">
                {grouped[category].length === 0 ? (
                  <p className="empty">해당 항목이 없습니다.</p>
                ) : grouped[category].map((result) => (
                  <article
                    className={[
                      'result-card',
                      copied.has(result.id) ? 'copied' : '',
                      reviewed.has(result.id) ? 'reviewed' : '',
                    ].filter(Boolean).join(' ')}
                    key={result.id}
                  >
                   <label className="reviewed-check">
                     <input
                       type="checkbox"
                       checked={reviewed.has(result.id)}
                       aria-label={`확인함: ${locationText(result.location)}`}
                       onChange={() => toggleReviewed(result.id)}
                     />
                     확인함
                     <span>표시만 변경되며 복사·다운로드 내용에는 영향 없음</span>
                   </label>
                   <div className="card-top">
                      <div className="labels">
                        <span className="severity">
                          {severityIcon(result.severity)} {result.severity}
                        </span>
                        <span className={result.ai_assisted ? 'ai-label' : 'rule-label'}>
                          {result.ai_assisted ? 'AI 보조' : '규칙 판정'}
                        </span>
                        <span className="confidence">
                          확신도 {confidenceText(result.confidence)} ·
                          {' '}{Math.round(result.confidence * 100)}%
                        </span>
                        <span className={`decision decision-${result.decision}`}>
                          {decisionText(result.decision)}
                        </span>
                      </div>
                      <button
                        className="copy-button"
                        type="button"
                        disabled={busy}
                        onClick={() => void copyMemo(result)}
                      >
                        {copied.has(result.id) ? '✓ 복사됨' : '⧉ 복사'}
                      </button>
                    </div>
                    <dl>
                      <div><dt>원문 위치</dt><dd>{locationText(result.location)}</dd></div>
                      <div><dt>발견 내용</dt><dd>{result.finding}</dd></div>
                      <div>
                        <dt>근거 규정</dt>
                        <dd>
                          <details className="rule-detail">
                            <summary>
                              {result.rule_source.document_name}
                              {' '}{result.rule_source.clause_number ?? result.rule_source.section_title}
                            </summary>
                            <p>
                              규칙 {result.rule_id} · {result.rule_source.section_title}
                            </p>
                          </details>
                        </dd>
                      </div>
                    </dl>
                    <label className="memo-label" htmlFor={`memo-${result.id}`}>저자용 수정 요청 문구</label>
                    <textarea
                      id={`memo-${result.id}`}
                      className="memo"
                      maxLength={500}
                      disabled={busy || saving.has(result.id)}
                      value={result.memo_text}
                      onChange={(event) => editMemo(result.id, event.target.value)}
                    />
                    {result.memo_text !== result.original_memo_text && (
                      <p className="edit-label">
                        {dirty.has(result.id) ? '저장 전 편집본' : '편집자 수정본'}
                        {' '}· {result.ai_assisted ? 'AI 초안에서 수정' : '규칙 초안에서 수정'}
                      </p>
                    )}
                    <div className="review-actions">
                      <button
                        type="button"
                        disabled={busy || saving.has(result.id)}
                        onClick={() => void patchResult(result, { memo_text: result.memo_text })}
                      >
                        수정 저장
                      </button>
                      <button
                        className="approve"
                        type="button"
                        disabled={busy || saving.has(result.id)}
                        onClick={() => void patchResult(result, { memo_text: result.memo_text, decision: 'approved' })}
                      >
                        승인
                      </button>
                      <button
                        className="exclude"
                        type="button"
                        disabled={busy || saving.has(result.id)}
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
          </div>
          <div className="results-footer-actions">
            <button
              type="button"
              disabled={busy}
              aria-label="새 원고 검사"
              onClick={() => void resetToUpload()}
            >
              새 원고 검사
            </button>
            <span>다른 HWP/HWPX 파일을 이 결과 영역에 놓아도 새 검사를 시작합니다.</span>
          </div>
        </section>
      )}
      <footer>AI가 보조한 결과는 최종 편집 판단을 대신하지 않습니다.</footer>
    </main>
  )
}

function parseEvent(frame: string): SseEvent {
  let id: number | null = null
  let event = 'message'
  const data: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('id:')) {
      const parsed = Number(line.slice(3).trim())
      if (Number.isInteger(parsed) && parsed >= 0) id = parsed
    }
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trim())
  }
  return { id, event, data: JSON.parse(data.join('\n') || '{}') as Record<string, unknown> }
}

function waitForReconnect(attempt: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, Math.min(1000 * 2 ** attempt, 8000))
    signal.addEventListener('abort', onAbort, { once: true })
  })
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

function confidenceText(confidence: number): string {
  if (confidence >= 0.8) return '높음'
  if (confidence >= 0.55) return '보통'
  return '낮음'
}

function severityIcon(severity: string): string {
  return {
    오류: '✕',
    경고: '!',
    '확인 필요': '?',
    정보: 'ⓘ',
  }[severity] ?? '•'
}

export default App
