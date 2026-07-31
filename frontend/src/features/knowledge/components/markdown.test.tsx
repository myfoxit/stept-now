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

  it('renders images with their alt text', () => {
    render(<Markdown content={'![Architecture](https://cdn.stept.io/arch.png)'} />)
    const image = screen.getByRole('img', { name: 'Architecture' })
    expect(image).toHaveAttribute('src', 'https://cdn.stept.io/arch.png')
  })

  it('renders same-origin upload paths as images', () => {
    render(<Markdown content={'![Shot](/api/v1/w/w1/files/abc.png)'} />)
    expect(screen.getByRole('img', { name: 'Shot' })).toHaveAttribute(
      'src',
      '/api/v1/w/w1/files/abc.png'
    )
  })

  it('rejects javascript: image sources and keeps only the alt text', () => {
    render(<Markdown content={'![boom](javascript:alert(1))'} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText(/boom/)).toBeInTheDocument()
  })

  it('rejects javascript: links and renders the label as plain text', () => {
    render(<Markdown content={'[click](javascript:alert(1))'} />)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText(/click/)).toBeInTheDocument()
  })

  it('renders GFM pipe tables with formatted cells', () => {
    render(
      <Markdown content={'| Plan | Price |\n| --- | --- |\n| Free | $0 |\n| **Pro** | $20 |'} />
    )
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Plan' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Price' })).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText('Free')).toBeInTheDocument()
    expect(screen.getByText('Pro').tagName).toBe('STRONG')
  })

  it('leaves a lone horizontal-rule-looking line out of table parsing', () => {
    render(<Markdown content={'before\n\n---\n\nafter'} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText('before')).toBeInTheDocument()
    expect(screen.getByText('after')).toBeInTheDocument()
  })
})
