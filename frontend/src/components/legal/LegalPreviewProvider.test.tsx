import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Link from 'next/link'
import { LegalPreviewProvider, useLegalPreview } from './LegalPreviewProvider'

vi.mock('next/dynamic', () => ({ default: () => (props: {docId: string; articleParam: string; clauseParam: string}) =>
  <article data-testid="preview-target">{props.docId}/{props.articleParam}/{props.clauseParam}<a href={`/legal-documents/${props.docId}`}>Xem toàn văn</a></article> }))

function Citation() {
  const open = useLegalPreview()
  return <button onClick={() => open('/legal-documents/100703?article=2&clause=1')}>Điều 2</button>
}

describe('legal preview within chat', () => {
  it('opens the precise citation while keeping the conversation mounted, then closes', () => {
    render(<LegalPreviewProvider><p>Cuộc trò chuyện đang đọc</p><Citation /></LegalPreviewProvider>)
    fireEvent.click(screen.getByRole('button', {name:'Điều 2'}))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByTestId('preview-target')).toHaveTextContent('100703/2/1')
    expect(screen.getByText('Cuộc trò chuyện đang đọc')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: 'Xem toàn văn' }))
    expect(screen.getByTestId('preview-target')).toHaveTextContent('100703//')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('dialog'), {key:'Escape', code:'Escape'})
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'Điều 2'})).toBeInTheDocument()
  })
  it('intercepts related document links without replacing the chat page', () => {
    render(<LegalPreviewProvider><Link href="/legal-documents/abc?article=12">Nguồn</Link></LegalPreviewProvider>)
    fireEvent.click(screen.getByRole('link', {name:'Nguồn'}))
    expect(screen.getByTestId('preview-target')).toHaveTextContent('abc/12/')
  })
})
