import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { emptyStep, type StepDraft } from '../lib'
import { resetAuth, seedAuth } from '../test-utils'
import { SandboxPlayer } from './SandboxPlayer'

const SNAPSHOT = {
  version: 1,
  html: '<html><body><h1>Billing</h1></body></html>',
  css: ['.a{color:red}'],
  url: 'https://app.example.com/billing',
  title: 'Billing',
}

const BOXED_TARGET = { bbox: { x: 320, y: 200, w: 128, h: 40, viewport: { w: 1280, h: 800 } } }

function step(over: Partial<StepDraft> = {}): StepDraft {
  return { ...emptyStep(), title: 'Open billing', body: 'Click **Billing**.', ...over }
}

function render(steps: StepDraft[]) {
  return renderApp(<SandboxPlayer steps={steps} accent="#6366f1" workspaceId="w1" />)
}

beforeEach(() => seedAuth())
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('SandboxPlayer', () => {
  it('prompts for steps when the tour is empty', () => {
    render([])
    expect(screen.getByText(/add a step to preview/i)).toBeInTheDocument()
  })

  it('replays a captured screenshot when no replica exists', () => {
    render([step({ key: 's1', screenshotKey: 'public/w1/shot.png' })])

    expect(screen.getByText('Screenshot')).toBeInTheDocument()
    expect(screen.getByAltText('Captured screen for step 1')).toHaveAttribute(
      'src',
      '/api/widget/media/w1/public/w1/shot.png'
    )
    expect(screen.queryByTestId('sandbox-frame')).not.toBeInTheDocument()
    // …and nudges the author toward the higher-fidelity option.
    expect(screen.getByText(/capture screens for sandbox/i)).toBeInTheDocument()
  })

  it('mounts a replica in a fully locked-down iframe', async () => {
    mockFetch({
      'GET /api/widget/media/w1/public/w1/snap.json': () => ({ status: 200, body: SNAPSHOT }),
    })
    render([step({ key: 's1', sandboxKey: 'public/w1/snap.json' })])

    const frame = await screen.findByTestId('sandbox-frame')
    // The security contract: sandbox present, and NEITHER escape hatch enabled.
    expect(frame).toHaveAttribute('sandbox', '')
    const sandboxValue = frame.getAttribute('sandbox') ?? ''
    expect(sandboxValue).not.toContain('allow-scripts')
    expect(sandboxValue).not.toContain('allow-same-origin')

    expect(frame.getAttribute('srcdoc')).toContain('<h1>Billing</h1>')
    expect(frame.getAttribute('srcdoc')).toContain("script-src 'none'")
    expect(screen.getByText('Interactive replica')).toBeInTheDocument()
  })

  it('shows the captured page URL in the fake browser chrome', async () => {
    mockFetch({
      'GET /api/widget/media/w1/public/w1/snap.json': () => ({ status: 200, body: SNAPSHOT }),
    })
    render([step({ key: 's1', sandboxKey: 'public/w1/snap.json' })])
    expect(await screen.findByText('https://app.example.com/billing')).toBeInTheDocument()
  })

  it('falls back to the screenshot when the replica cannot be fetched', async () => {
    mockFetch({
      'GET /api/widget/media/w1/public/w1/snap.json': () => ({ status: 404, body: {} }),
    })
    render([
      step({ key: 's1', sandboxKey: 'public/w1/snap.json', screenshotKey: 'public/w1/shot.png' }),
    ])

    await waitFor(() =>
      expect(screen.getByText(/could not be loaded, so this step fell back/i)).toBeInTheDocument()
    )
    expect(screen.getByAltText('Captured screen for step 1')).toBeInTheDocument()
  })

  it('warns when the capture missed stylesheets', async () => {
    mockFetch({
      'GET /api/widget/media/w1/public/w1/snap.json': () => ({
        status: 200,
        body: { ...SNAPSHOT, blockedStyles: ['https://cdn/a.css'] },
      }),
    })
    render([step({ key: 's1', sandboxKey: 'public/w1/snap.json' })])
    expect(await screen.findByText(/1 stylesheet could not be read/i)).toBeInTheDocument()
  })

  it('boxes the recorded element over the screen', () => {
    render([step({ key: 's1', screenshotKey: 'k.png', target: BOXED_TARGET })])
    const highlight = screen.getByTestId('sandbox-highlight')
    expect(highlight).toHaveStyle({ left: '25%', top: '25%', width: '10%', height: '5%' })
  })

  it('renders the guide card with the step copy and CTA label', () => {
    render([step({ key: 's1', screenshotKey: 'k.png', ctaLabel: 'Take me there' })])
    const card = screen.getByTestId('sandbox-card')
    expect(card).toHaveTextContent('Open billing')
    expect(card).toHaveTextContent('Click Billing.')
    expect(card).toHaveTextContent('Take me there')
  })

  it('labels the last step’s button Got it', () => {
    render([step({ key: 's1', screenshotKey: 'k.png' })])
    expect(screen.getByTestId('sandbox-card')).toHaveTextContent('Got it')
  })

  it('walks forward and back through the steps', async () => {
    render([
      step({ key: 's1', title: 'First', screenshotKey: 'a.png' }),
      step({ key: 's2', title: 'Second', screenshotKey: 'b.png' }),
    ])

    expect(screen.getByText('Step 1 of 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /previous step/i })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /next step/i }))
    expect(screen.getByText('Step 2 of 2')).toBeInTheDocument()
    expect(screen.getByTestId('sandbox-card')).toHaveTextContent('Second')
    expect(screen.getByRole('button', { name: /next step/i })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /previous step/i }))
    expect(screen.getByTestId('sandbox-card')).toHaveTextContent('First')
  })

  it('explains a hand-authored step that has no screen at all', () => {
    render([step({ key: 's1' })])
    expect(screen.getByText('No screen captured')).toBeInTheDocument()
    expect(screen.getByText(/authored by hand/i)).toBeInTheDocument()
  })
})
