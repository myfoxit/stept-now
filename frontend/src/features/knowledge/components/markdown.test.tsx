import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Markdown } from './markdown'

describe('Markdown', () => {
  it('renders headings, lists, bold text and links', () => {
    render(
      <Markdown
        content={'# Title\n\nSome **bold** text.\n\n- first\n- second\n\n[docs](https://stept.dev)'}
      />
    )
    expect(screen.getByText('Title')).toBeInTheDocument()
    expect(screen.getByText('bold')).toBeInTheDocument()
    expect(screen.getByText('first')).toBeInTheDocument()
    expect(screen.getByText('second')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'docs' })
    expect(link).toHaveAttribute('href', 'https://stept.dev')
  })

  it('renders fenced code blocks', () => {
    render(<Markdown content={'```\nconst x = 1\n```'} />)
    expect(screen.getByText('const x = 1')).toBeInTheDocument()
  })
})
