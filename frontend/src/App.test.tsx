import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App scaffold', () => {
  it('renders the Vite scaffold', () => {
    render(<App />)

    expect(screen.getByRole('heading', { level: 1, name: 'Get started' })).toBeInTheDocument()
  })
})
