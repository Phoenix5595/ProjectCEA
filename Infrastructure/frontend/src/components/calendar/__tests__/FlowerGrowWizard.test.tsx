import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import FlowerGrowWizard from '../FlowerGrowWizard'

describe('FlowerGrowWizard bed-week choices', () => {
  it('offers only two or three weeks for a new plan', () => {
    render(<FlowerGrowWizard open onClose={() => undefined} onCreated={() => undefined} />)

    const bedWeeks = screen.getByLabelText('Bed (w)') as HTMLSelectElement
    expect([...bedWeeks.options].map(option => option.value)).toEqual(['2', '3'])
  })
})
