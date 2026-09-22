import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ askQuickChat: vi.fn() }))
vi.mock('@/lib/api/public-quick-chat', () => ({ askQuickChat: api.askQuickChat }))

import QuickChatPage from './page'

describe('QuickChatPage', () => {
  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn()
    api.askQuickChat.mockReset()
    api.askQuickChat.mockResolvedValue({
      answer: 'Chuẩn bị giấy tờ theo bản thủ tục đã công bố.',
      question: 'Đăng ký kết hôn cần giấy tờ gì?',
      answer_mode: 'intent_fast_path',
      quick_facts: [
        { id: 'submission_place', label: 'Nơi nộp', value: 'Bộ phận Một cửa' },
        { id: 'duration', label: 'Thời hạn', value: 'Theo phiếu hẹn' },
      ],
      answer_sections: [{ id: 'documents', title: 'Hồ sơ cần chuẩn bị', kind: 'checklist', items: ['Giấy A', 'Giấy B'] }],
      citations: [{ document_title: 'Thủ tục đăng ký kết hôn', source_url: 'https://dichvucong.gov.vn/example' }],
      suggested_questions: ['Đăng ký kết hôn có mất phí không?'],
      action_chips: [{ id: 'summary', label: 'Tóm tắt lại', question: 'Tóm tắt lại' }],
      llm_used: false,
    })
  })

  it('renders sourced facts, collapsible checklist and keyboard-operable followups', async () => {
    render(<QuickChatPage />)
    const input = screen.getByLabelText('Câu hỏi của bạn')
    expect(input).toHaveAttribute('maxLength', '800')
    fireEvent.change(input, { target: { value: 'Đăng ký kết hôn cần giấy tờ gì?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Gửi câu hỏi' }))

    expect(await screen.findByText('Chuẩn bị giấy tờ theo bản thủ tục đã công bố.')).toBeInTheDocument()
    expect(screen.getByText('Bộ phận Một cửa')).toBeInTheDocument()
    expect(screen.getByText('Hồ sơ cần chuẩn bị')).toBeInTheDocument()
    expect(screen.getByText('Giấy A')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Thủ tục đăng ký kết hôn' })).toHaveAttribute('href', 'https://dichvucong.gov.vn/example')
    fireEvent.click(screen.getByRole('button', { name: 'Đăng ký kết hôn có mất phí không?' }))
    await waitFor(() => expect(api.askQuickChat).toHaveBeenCalledTimes(2))
    expect(api.askQuickChat.mock.calls[1][0]).toMatchObject({
      question: 'Đăng ký kết hôn có mất phí không?',
      context: [{ question: 'Đăng ký kết hôn cần giấy tờ gì?' }],
    })
  })
})
