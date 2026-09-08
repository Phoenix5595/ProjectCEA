import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EventDetails } from '../components/EventDetails'

describe('EventDetails', () => {
  it('renders safe payload fields as a definition list', () => {
    render(<EventDetails payload={{ device_id: 'heater-1', state: 'on' }} expanded={true} />)
    expect(screen.getByText('device_id')).toBeInTheDocument()
    expect(screen.getByText('heater-1')).toBeInTheDocument()
    expect(screen.getByText('state')).toBeInTheDocument()
    expect(screen.getByText('on')).toBeInTheDocument()
  })

  it('renders nothing visually when not expanded', () => {
    const { container } = render(<EventDetails payload={{ device_id: 'fan-1' }} expanded={false} />)
    const region = container.querySelector('[role="region"]')
    expect(region).toHaveAttribute('hidden')
  })

  it('shows a placeholder when payload has no safe fields', () => {
    render(<EventDetails payload={{ secret: 'hidden' }} expanded={true} />)
    expect(screen.getByText('No safe details available')).toBeInTheDocument()
  })

  it('strips secret fields from display', () => {
    render(<EventDetails payload={{ device_id: 'x', password: 'secret' }} expanded={true} />)
    expect(screen.queryByText('password')).not.toBeInTheDocument()
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
  })
})
