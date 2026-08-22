import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const result = {
  id: 'result-1',
  category: '확인 필요',
  severity: '확인 필요',
  location: {
    section_label: 'Section0',
    section_index: 0,
    paragraph_index: 3,
    reference_index: null,
    display_hint: '본문 인용 (가상저자, 2020) · 1번째 출현',
  },
  finding: '합성 확인 항목',
  memo_text: '합성 확인 필요',
  original_memo_text: '합성 확인 필요',
  decision: 'pending',
  ai_assisted: true,
  confidence: 0.72,
  rule_id: 'CR-03',
  rule_source: {
    document_name: '문편협 공통기준',
    clause_number: 'Ⅰ-1)',
    section_title: '본문 인용',
  },
} as const

function eventStream(frames: string): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(frames))
      controller.close()
    },
  })
}

function uploadFile() {
  const input = document.querySelector('input[type="file"]')
  if (!(input instanceof HTMLInputElement)) throw new Error('file input missing')
  fireEvent.change(input, {
    target: { files: [new File(['synthetic'], 'synthetic.hwp')] },
  })
}

beforeEach(() => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    callback(0)
    return 1
  })
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('참고문헌 검증 화면', () => {
  it('업로드 핵심 UI를 표시한다', () => {
    render(<App />)
    expect(screen.getByRole('heading', { level: 1, name: '참고문헌 검증' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '파일 선택' })).toBeInTheDocument()
    expect(screen.getByText(/HWP 또는 HWPX 파일을 여기에/)).toBeInTheDocument()
  })

  it('SSE를 마지막 이벤트 ID로 재연결하고 결과를 중복 없이 표시한다', async () => {
    let streamCount = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/v1/checks' && init?.method === 'POST') {
        return Response.json({
          id: 'check-1',
          access_token: 'token',
          events_url: '/events',
        })
      }
      if (url === '/events') {
        streamCount += 1
        if (streamCount === 1) {
          return new Response(eventStream(
            'id: 1\nevent: stage_changed\ndata: {"progress":25,"message":"추출 중"}\n\n',
          ))
        }
        expect(new Headers(init?.headers).get('Last-Event-ID')).toBe('1')
        return new Response(eventStream(
          `id: 2\nevent: result_added\ndata: ${JSON.stringify({ result })}\n\n`
          + `id: 3\nevent: result_added\ndata: ${JSON.stringify({ result })}\n\n`
          + 'id: 4\nevent: completed\ndata: {}\n\n',
        ))
      }
      if (url.endsWith('/results')) return Response.json([result])
      throw new Error(`unexpected fetch: ${url}`)
    })

    render(<App />)
    uploadFile()

    expect(await screen.findByText('합성 확인 항목', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getAllByText('합성 확인 항목')).toHaveLength(1)
    expect(screen.getByText('AI 보조')).toBeInTheDocument()
    expect(screen.getByText(/확신도 보통 · 72%/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalled()
  })

  it('승인 저장 후 항목 복사를 수행하고 배지를 즉시 갱신한다', async () => {
    const clipboard = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: clipboard },
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/v1/checks') {
        return Response.json({ id: 'check-1', access_token: 'token', events_url: '/events' })
      }
      if (url === '/events') {
        return new Response(eventStream(
          `id: 1\nevent: result_added\ndata: ${JSON.stringify({ result })}\n\n`
          + 'id: 2\nevent: completed\ndata: {}\n\n',
        ))
      }
      if (url.endsWith('/results') && init?.method !== 'PATCH') return Response.json([result])
      if (url.includes('/results/result-1') && init?.method === 'PATCH') {
        return Response.json({ ...result, decision: 'approved' })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    render(<App />)
    uploadFile()
    const approve = await screen.findByRole('button', { name: '승인' })
    await waitFor(() => expect(approve).toBeEnabled())
    fireEvent.click(approve)

    await waitFor(() => expect(screen.getByLabelText('1건 중 1건 승인')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '⧉ 복사' }))
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith('합성 확인 필요'))
    expect(screen.getByRole('button', { name: '✓ 복사됨' })).toBeInTheDocument()
  })

  it('클립보드 거부 시 직접 복사 textarea를 표시하고 완료로 표시하지 않는다', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/v1/checks') {
        return Response.json({ id: 'check-1', access_token: 'token', events_url: '/events' })
      }
      if (url === '/events') {
        return new Response(eventStream(
          `id: 1\nevent: result_added\ndata: ${JSON.stringify({ result })}\n\n`
          + 'id: 2\nevent: completed\ndata: {}\n\n',
        ))
      }
      if (url.endsWith('/results')) return Response.json([result])
      throw new Error(`unexpected fetch: ${url}`)
    })

    render(<App />)
    uploadFile()
    const copy = await screen.findByRole('button', { name: '⧉ 복사' })
    await waitFor(() => expect(copy).toBeEnabled())
    fireEvent.click(copy)

    expect(await screen.findByRole('textbox', { name: '직접 복사할 수정 요청 문구' }))
      .toHaveValue('합성 확인 필요')
    expect(screen.getByRole('button', { name: '⧉ 복사' })).toBeInTheDocument()
  })

  it('카테고리 탭과 확인함 상태를 유지하고 새 원고에서 초기화한다', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/v1/checks') {
        return Response.json({ id: 'check-1', access_token: 'token', events_url: '/events' })
      }
      if (url === '/events') {
        return new Response(eventStream(
          `id: 1\nevent: result_added\ndata: ${JSON.stringify({ result })}\n\n`
          + 'id: 2\nevent: completed\ndata: {}\n\n',
        ))
      }
      if (url.endsWith('/results')) return Response.json([result])
      throw new Error(`unexpected fetch: ${url}`)
    })

    render(<App />)
    uploadFile()
    const categoryTab = await screen.findByRole('tab', { name: '확인 필요 1' })
    const allTab = screen.getByRole('tab', { name: '전체 1' })
    fireEvent.keyDown(allTab, { key: 'ArrowRight' })
    expect(categoryTab).toHaveAttribute('aria-selected', 'true')
    const reviewedCheck = screen.getByRole('checkbox', { name: /확인함:/ })
    fireEvent.click(reviewedCheck)

    expect(reviewedCheck).toBeChecked()
    fireEvent.click(reviewedCheck)
    expect(reviewedCheck).not.toBeChecked()
    fireEvent.click(reviewedCheck)
    expect(screen.getByText('전체 1건 중 1건 확인')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /전체 1/ }))
    fireEvent.click(screen.getByRole('tab', { name: /확인 필요 1/ }))
    expect(screen.getByRole('checkbox', { name: /확인함:/ })).toBeChecked()

    fireEvent.click(screen.getAllByRole('button', { name: '새 원고 검사' })[0])
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: /확인함:/ })).not.toBeInTheDocument())
    expect(window.confirm).not.toHaveBeenCalled()
  })

  it('처리 중 취소 API를 호출하고 진행 상태를 정리한다', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/v1/checks') {
        return Response.json({ id: 'check-1', access_token: 'token', events_url: '/events' })
      }
      if (url === '/events') {
        return new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(
              'id: 1\nevent: stage_changed\ndata: {"progress":25,"message":"추출 중"}\n\n',
            ))
          },
          cancel() {},
        }))
      }
      if (url.endsWith('/cancel') && init?.method === 'POST') {
        return Response.json({ status: 'cancelled' })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    render(<App />)
    uploadFile()
    fireEvent.click(await screen.findByRole('button', { name: '검사 취소' }))

    await waitFor(() => expect(screen.getByText('검사가 취소되었습니다.')).toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/checks/check-1/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('실패 단계와 재시도 가능 여부를 표시한다', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/v1/checks') {
        return Response.json({ id: 'check-1', access_token: 'token', events_url: '/events' })
      }
      if (url === '/events') {
        return new Response(eventStream(
          'id: 1\nevent: failed\ndata: '
          + '{"code":"TEMPORARY_IO","stage":"extracting","retryable":true,'
          + '"message":"임시 읽기 실패"}\n\n',
        ))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    render(<App />)
    uploadFile()

    expect(await screen.findByText(/실패 단계: extracting/)).toHaveTextContent('재시도 가능')
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })
})
