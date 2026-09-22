import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatModelSelector, type ChatModelOption } from './ChatModelSelector'

const options: ChatModelOption[] = [
  {
    option_id: 'deepseek-default',
    display_name: 'DeepSeek',
    audiences: ['citizen', 'officer'],
    is_default: true,
    is_active: true,
  },
  {
    option_id: 'qwen-local',
    display_name: 'Qwen local',
    audiences: ['officer'],
    is_default: false,
    is_active: true,
  },
]

describe('ChatModelSelector', () => {
  it('renders only the Admin-projected options beside the composer', () => {
    render(
      <ChatModelSelector
        options={options}
        value="deepseek-default"
        onValueChange={vi.fn()}
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Chọn mô hình cho lượt hỏi' })
    expect(trigger).toHaveTextContent('DeepSeek')
    fireEvent.keyDown(trigger, { key: 'Enter' })
    expect(screen.getByRole('menuitemradio', { name: /DeepSeek/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitemradio', { name: 'Qwen local' })).toBeInTheDocument()
  })

  it('allows changing the model between completed turns', () => {
    const onValueChange = vi.fn()
    const onDepthChange = vi.fn()
    render(
      <ChatModelSelector
        options={options}
        value="qwen-local"
        onValueChange={onValueChange}
        onDepthChange={onDepthChange}
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Chọn mô hình cho lượt hỏi' })
    expect(trigger).toBeEnabled()
    fireEvent.keyDown(trigger, { key: 'Enter' })
    fireEvent.click(screen.getByRole('menuitemradio', { name: /DeepSeek/ }))
    expect(onValueChange).toHaveBeenCalledWith('deepseek-default')

    fireEvent.keyDown(trigger, { key: 'Enter' })
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Chuyên sâu/ }))
    expect(onDepthChange).toHaveBeenCalledWith('deep')
  })

  it('shows why selection is unavailable when Admin has not allowed any model', () => {
    render(
      <ChatModelSelector options={[]} value="" onValueChange={vi.fn()} />,
    )
    const trigger = screen.getByRole('button', { name: 'Chọn mô hình cho lượt hỏi' })
    expect(trigger).toBeDisabled()
    expect(trigger).toHaveTextContent('Chưa được cấp mô hình')
  })
})
