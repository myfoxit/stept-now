import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { emptyStep, toStepDraft, type StepDraft } from '../lib'
import { makeStep, resetAuth, seedAuth } from '../test-utils'
import { StepEditor } from './StepEditor'

function Harness({ initial }: { initial: StepDraft[] }) {
  const [steps, setSteps] = useState<StepDraft[]>(initial)
  return (
    <div>
      <StepEditor steps={steps} onChange={setSteps} />
      <pre data-testid="order">{steps.map((s) => s.selector).join(',')}</pre>
      <pre data-testid="fallbacks">{steps[0]?.fallbackSelectors.join(',')}</pre>
      <pre data-testid="media">{steps[0]?.mediaUrl}</pre>
      <pre data-testid="cta">{`${steps[0]?.ctaLabel ?? ''}|${steps[0]?.secondaryCtaUrl ?? ''}`}</pre>
    </div>
  )
}

function twoSteps(): StepDraft[] {
  return [
    { ...emptyStep(), key: 's1', selector: '#one', title: 'One' },
    { ...emptyStep(), key: 's2', selector: '#two', title: 'Two' },
  ]
}

/** Reveal the advanced disclosure on the nth card (0-based). */
async function openAdvanced(nth = 0) {
  const card = screen.getAllByTestId('tour-step')[nth]!
  await userEvent.click(within(card).getByRole('button', { name: /advanced/i }))
  return within(card)
}

beforeEach(() => seedAuth())
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('StepEditor', () => {
  it('reorders steps when moving one down', async () => {
    renderApp(<Harness initial={twoSteps()} />)
    expect(screen.getByTestId('order').textContent).toBe('#one,#two')

    await userEvent.click(screen.getByRole('button', { name: /move step 1 down/i }))
    expect(screen.getByTestId('order').textContent).toBe('#two,#one')
  })

  it('adds and removes steps', async () => {
    renderApp(<Harness initial={twoSteps()} />)
    await userEvent.click(screen.getByRole('button', { name: /^add step$/i }))
    expect(screen.getAllByTestId('tour-step')).toHaveLength(3)

    await userEvent.click(screen.getByRole('button', { name: /remove step 3/i }))
    expect(screen.getAllByTestId('tour-step')).toHaveLength(2)
  })

  it('adds a step of the kind the quick-add button names', async () => {
    renderApp(<Harness initial={[]} />)
    await userEvent.click(screen.getByRole('button', { name: /add banner step/i }))

    const card = within(screen.getAllByTestId('tour-step')[0]!)
    expect(card.getByLabelText('Type')).toHaveValue('banner')
    // A banner has no anchor, so no selector even once advanced is open.
    await openAdvanced()
    expect(card.queryByLabelText('CSS selector')).not.toBeInTheDocument()
  })

  // --- guide first ----------------------------------------------------------

  it('leads with the guide and keeps targeting out of the way', async () => {
    renderApp(<Harness initial={[{ ...emptyStep(), key: 's1', selector: '#one' }]} />)
    const card = within(screen.getAllByTestId('tour-step')[0]!)

    // What an author writes is on screen immediately…
    expect(card.getByLabelText('Title')).toBeInTheDocument()
    expect(card.getByLabelText('Description')).toBeInTheDocument()
    // …while the plumbing is not.
    expect(card.queryByLabelText('CSS selector')).not.toBeInTheDocument()
    expect(card.queryByLabelText('Advance')).not.toBeInTheDocument()
    expect(card.queryByLabelText('Placement')).not.toBeInTheDocument()

    await openAdvanced()
    expect(card.getByLabelText('CSS selector')).toBeInTheDocument()
    expect(card.getByLabelText('Advance')).toBeInTheDocument()
    expect(card.getByLabelText('Placement')).toBeInTheDocument()
  })

  it('keeps each card’s disclosure independent', async () => {
    renderApp(<Harness initial={twoSteps()} />)
    await openAdvanced(0)

    const second = within(screen.getAllByTestId('tour-step')[1]!)
    expect(second.queryByLabelText('CSS selector')).not.toBeInTheDocument()
  })

  it('edits title and description without opening anything', async () => {
    renderApp(<Harness initial={[{ ...emptyStep(), key: 's1', selector: '#one' }]} />)
    const card = within(screen.getAllByTestId('tour-step')[0]!)

    await userEvent.type(card.getByLabelText('Title'), 'Create a project')
    await userEvent.type(card.getByLabelText('Description'), 'Click **New**.')
    expect(card.getByLabelText('Title')).toHaveValue('Create a project')
    expect(card.getByLabelText('Description')).toHaveValue('Click **New**.')
  })

  it('captures primary and secondary button copy under advanced', async () => {
    renderApp(<Harness initial={[{ ...emptyStep(), key: 's1', selector: '#one' }]} />)
    const card = await openAdvanced()

    await userEvent.type(card.getByLabelText('Primary button text'), 'Start setup')
    await userEvent.type(card.getByLabelText('Secondary button link'), 'https://docs.example.com')
    expect(screen.getByTestId('cta').textContent).toBe('Start setup|https://docs.example.com')
  })

  it('swaps the conditional fields when the step type changes', async () => {
    renderApp(<Harness initial={[{ ...emptyStep(), key: 's1', selector: '#one' }]} />)
    const card = await openAdvanced()

    // tooltip: anchored + authored content
    expect(card.getByLabelText('CSS selector')).toBeInTheDocument()
    expect(card.getByLabelText('Description')).toBeInTheDocument()
    expect(card.queryByLabelText('Action')).not.toBeInTheDocument()

    await userEvent.selectOptions(card.getByLabelText('Type'), 'action')
    expect(card.getByLabelText('Action')).toBeInTheDocument()
    expect(card.getByLabelText('CSS selector')).toBeInTheDocument()
    expect(card.queryByLabelText('Description')).not.toBeInTheDocument()

    await userEvent.selectOptions(card.getByLabelText('Action'), 'fill')
    expect(card.getByLabelText('Value to type')).toBeInTheDocument()

    await userEvent.selectOptions(card.getByLabelText('Type'), 'wait')
    expect(card.getByLabelText('Wait for')).toBeInTheDocument()
    expect(card.getByLabelText('Timeout (ms)')).toBeInTheDocument()
    expect(card.queryByLabelText('CSS selector')).not.toBeInTheDocument()

    await userEvent.selectOptions(card.getByLabelText('Wait for'), 'url')
    expect(card.getByLabelText('URL pattern')).toBeInTheDocument()

    await userEvent.selectOptions(card.getByLabelText('Type'), 'modal')
    expect(card.queryByLabelText('CSS selector')).not.toBeInTheDocument()
    expect(card.getByLabelText('Placement')).toBeInTheDocument()
  })

  it('shows the delay input only for delay advance', async () => {
    renderApp(<Harness initial={[{ ...emptyStep(), key: 's1', selector: '#one' }]} />)
    const card = await openAdvanced()

    expect(card.queryByLabelText('Delay (ms)')).not.toBeInTheDocument()
    await userEvent.selectOptions(card.getByLabelText('Advance'), 'delay')
    expect(card.getByLabelText('Delay (ms)')).toBeInTheDocument()
  })

  it('adds fallback selectors on Enter, removes them, and stops at five', async () => {
    renderApp(<Harness initial={[{ ...emptyStep(), key: 's1', selector: '#one' }]} />)
    await openAdvanced()
    const input = screen.getByLabelText('Add fallback selector for step 1')

    await userEvent.type(input, '.signup{Enter}')
    await userEvent.type(input, '.btn-primary{Enter}')
    expect(screen.getByTestId('fallbacks').textContent).toBe('.signup,.btn-primary')

    // Duplicates are ignored.
    await userEvent.type(input, '.btn-primary{Enter}')
    expect(screen.getByTestId('fallbacks').textContent).toBe('.signup,.btn-primary')

    await userEvent.type(input, '.c{Enter}')
    await userEvent.type(input, '.d{Enter}')
    await userEvent.type(input, '.e{Enter}')
    expect(screen.getByTestId('fallbacks').textContent).toBe('.signup,.btn-primary,.c,.d,.e')
    // Sixth is rejected: the input is disabled once the cap is reached.
    expect(input).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /remove fallback selector 2/i }))
    expect(screen.getByTestId('fallbacks').textContent).toBe('.signup,.c,.d,.e')
    expect(screen.getByLabelText('Add fallback selector for step 1')).toBeEnabled()
  })

  it('marks recorder-captured steps and shows their screenshot up front', () => {
    const captured = toStepDraft(
      makeStep({
        target: { selectors: [{ kind: 'css', value: '#one', score: 0.9 }] },
        fallback_selectors: ['.a', '.b'],
        screenshot_key: 'public/w1/2026/07/shot.png',
      })
    )
    renderApp(<Harness initial={[captured]} />)

    expect(screen.getByTestId('recorder-target')).toHaveTextContent(
      /Captured by recorder · 2 fallbacks/
    )
    // The shot is part of the guide, so it is visible before opening anything.
    expect(screen.getByAltText('Recorder screenshot for step 1')).toHaveAttribute(
      'src',
      '/api/widget/media/w1/public/w1/2026/07/shot.png'
    )
  })

  it('boxes the recorded element over the screenshot', () => {
    const captured = toStepDraft(
      makeStep({
        target: {
          selectors: [{ kind: 'css', value: '#one', score: 0.9 }],
          bbox: { x: 320, y: 200, w: 128, h: 40, viewport: { w: 1280, h: 800 } },
        },
        screenshot_key: 'public/w1/2026/07/shot.png',
      })
    )
    renderApp(<Harness initial={[captured]} />)

    const shot = screen.getByTestId('step-shot-0')
    // 320/1280 = 25%, 200/800 = 25%, 128/1280 = 10%, 40/800 = 5%
    const box = shot.querySelector('span[style*="width"]') as HTMLElement
    expect(box.style.left).toBe('25%')
    expect(box.style.top).toBe('25%')
    expect(box.style.width).toBe('10%')
    expect(box.style.height).toBe('5%')
  })

  it('flags a step that has a sandbox replica', () => {
    const captured = toStepDraft(
      makeStep({ screenshot_key: 'k.png', sandbox_key: 'public/w1/snap.json' })
    )
    renderApp(<Harness initial={[captured]} />)
    expect(screen.getByTestId('sandbox-ready')).toBeInTheDocument()
  })

  it('uploads media to the public namespace and fills in the url', async () => {
    const fetchMock = mockFetch({
      'POST /api/v1/w/w1/files': () => ({
        status: 201,
        body: {
          key: 'public/w1/2026/07/hero.png',
          name: 'hero.png',
          size: 12,
          content_type: 'image/png',
          url: '/api/widget/media/w1/public/w1/2026/07/hero.png',
        },
      }),
    })
    renderApp(<Harness initial={[{ ...emptyStep(), key: 's1', selector: '#one' }]} />)
    await openAdvanced()

    const file = new File(['x'], 'hero.png', { type: 'image/png' })
    await userEvent.upload(screen.getByTestId('media-file-0'), file)

    await waitFor(() =>
      expect(screen.getByTestId('media').textContent).toBe(
        '/api/widget/media/w1/public/w1/2026/07/hero.png'
      )
    )
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/files?public=true'))).toBe(
      true
    )
  })
})
