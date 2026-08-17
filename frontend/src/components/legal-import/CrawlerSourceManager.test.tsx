import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CrawlerSourceManager } from './CrawlerSourceManager'

describe('CrawlerSourceManager', () => {
  it('labels internal queues, prevents scanning and allows rename or removal', () => {
    const onDelete = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(
      <CrawlerSourceManager
        sources={[
          {
            id: 'legal_crawl_source:internal',
            name: 'Đề xuất văn bản từ cán bộ',
            source_type: 'officer_proposal',
            sitemap_scope: 'internal',
            base_url: 'local://officer-proposals',
            enabled: true,
            interval_minutes: 1440,
            lookback_days: 30,
            max_documents_per_run: 30,
            source_kind: 'internal_queue',
            is_default: true,
            can_delete: true,
          },
        ]}
        scanningSourceId={null}
        onRefresh={vi.fn()}
        onScan={vi.fn()}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={onDelete}
      />,
    )

    expect(screen.getByText('Nguồn nội bộ')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Quét nguồn này' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Sửa' }))
    expect(screen.getByLabelText('Tên hiển thị nội bộ')).toHaveValue('Đề xuất văn bản từ cán bộ')
    expect(screen.getByText('Loại và địa chỉ nội bộ được khóa để bảo toàn luồng dữ liệu.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Xóa' }))
    expect(onDelete).toHaveBeenCalledWith('legal_crawl_source:internal')
  })
})
