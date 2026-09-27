import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'

import type { ActiveAlarmResponse } from '../../../services/api/alarms'
import { DashboardAlarmButton, DashboardAlarmSummary } from '../DashboardAlarmSummary'

const alarm: ActiveAlarmResponse = {
  location: 'Veg Room',
  cluster: 'main',
  alarm_name: 'temperature_high',
  severity: 'critical',
  message: 'Temperature above cooling limit',
  active: true,
  acknowledged: false,
  opened_at: '2026-09-23T15:00:00Z',
  acknowledged_at: null,
  acknowledged_by: null,
}

function Harness({ acknowledged = false }: { acknowledged?: boolean }) {
  const [open, setOpen] = useState(false)
  const acknowledge = vi.fn().mockResolvedValue(true)
  const current = { ...alarm, acknowledged }
  return (
    <>
      <DashboardAlarmButton alarms={[current]} onOpen={() => setOpen(true)} />
      <DashboardAlarmSummary
        alarms={[current]}
        open={open}
        onOpenChange={setOpen}
        serviceError={null}
        acknowledgingKey={null}
        acknowledgementErrors={{}}
        acknowledge={acknowledge}
      />
    </>
  )
}

describe('DashboardAlarmSummary', () => {
  it('shows the banner and keeps the active row after opening the dialog', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    expect(screen.getByRole('alert')).toHaveTextContent('Veg Room / main')
    expect(screen.getByRole('button', { name: /1\/1 alarms/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /1\/1 alarms/i }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Temperature above cooling limit')
    expect(screen.getByRole('dialog')).toHaveTextContent('Acknowledge')
  })

  it('removes prominence for acknowledged active alarms', () => {
    render(<Harness acknowledged />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /0\/1 alarms/i })).toBeInTheDocument()
  })
})
