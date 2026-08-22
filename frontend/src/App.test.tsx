import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('참고문헌 검증 화면', () => {
  it('업로드 핵심 UI를 표시한다', () => {
    render(<App />)
    expect(screen.getByRole('heading', { level: 1, name: '참고문헌 검증' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '파일 선택' })).toBeInTheDocument()
    expect(screen.getByText(/HWP 또는 HWPX 파일을 여기에/)).toBeInTheDocument()
  })
})
