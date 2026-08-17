import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatInterface } from './ChatInterface'

describe('ChatInterface', () => {
  it('keeps the new-chat action and composer outside the scrollable viewport', () => {
    const { container } = render(
      <ChatInterface title="Trợ lý pháp luật" description="Hỏi đáp có căn cứ" onNewChat={() => undefined} composer={<textarea aria-label="Nhập câu hỏi" />}>
        <div>100 tin nhắn</div>
      </ChatInterface>,
    )

    const viewport = screen.getByTestId('chat-message-viewport')
    const composer = screen.getByTestId('chat-composer')
    const newChat = screen.getByRole('button', { name: /Cuộc trò chuyện mới|Mới/ })
    expect(viewport).toHaveClass('overflow-y-auto')
    expect(viewport.contains(composer)).toBe(false)
    expect(viewport.contains(newChat)).toBe(false)
    expect(container.querySelector('[data-testid="chat-interface"]')).toHaveClass('overflow-hidden')
  })

  it('exposes explicit older-message pagination and does not load without a user action', () => {
    const loadOlder = vi.fn()
    render(
      <ChatInterface title="Tra cứu nghiệp vụ" onNewChat={() => undefined} composer={<div>Composer</div>} hasOlder onLoadOlder={loadOlder}>
        <div>Tin gần nhất</div>
      </ChatInterface>,
    )

    expect(loadOlder).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Tải tin nhắn cũ hơn' }))
    expect(loadOlder).toHaveBeenCalledTimes(1)
  })

  it('keeps keyboard access on the message viewport and new chat', () => {
    const onNewChat = vi.fn()
    render(
      <ChatInterface title="Trợ lý pháp luật" onNewChat={onNewChat} composer={<button>Gửi</button>}>
        <div>Tin nhắn</div>
      </ChatInterface>,
    )

    expect(screen.getByTestId('chat-message-viewport')).toHaveAttribute('tabindex', '0')
    fireEvent.click(screen.getByRole('button', { name: /Cuộc trò chuyện mới|Mới/ }))
    expect(onNewChat).toHaveBeenCalledTimes(1)
  })
})
