import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'

describe('test infrastructure', () => {
  it('renders jsdom', () => {
    render(<div data-testid="test">Hello</div>)
    expect(screen.getByTestId('test')).toHaveTextContent('Hello')
  })
})
