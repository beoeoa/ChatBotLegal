import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CrawlerSourceManager } from './CrawlerSourceManager'

vi.mock('@/lib/hooks/use-settings', () => ({ useSettings: () => ({ data: { organization_units: [] } }) }))

describe('CrawlerSourceManager', () => {
  it('accepts a verified source with no current keyword match', async () => {
    const onPreview = vi.fn().mockResolvedValue({ status: 'ok', mutation_performed: false, base_url: 'https://vbpl.vn/van-ban/trung-uong', website_type: 'legal_documents', discovered_count: 0, preview_count: 0, truncated: false, candidates: [], listing_verified: true, filtered_out_count: 10, notice: 'Nguồn đọc được nhưng chưa có nội dung khớp điều kiện.' })
    render(<CrawlerSourceManager sources={[]} scanningSourceId={null} onRefresh={vi.fn()} onScan={vi.fn()} onCreate={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} onPreview={onPreview} />)
    fireEvent.change(screen.getByLabelText('Tên dễ nhận biết'), { target: { value: 'Nguồn theo từ khóa' } })
    fireEvent.change(screen.getByLabelText('Đường dẫn trang danh sách'), { target: { value: 'https://vbpl.vn/van-ban/trung-uong' } })
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra nguồn — không lưu dữ liệu' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Thêm nguồn' })).toBeEnabled())
    expect(screen.queryByText(/Nguồn chưa thể được lưu/)).not.toBeInTheDocument()
    expect(screen.getAllByText('Nguồn đọc được nhưng chưa có nội dung khớp điều kiện.').length).toBeGreaterThan(0)
  })
  const sharedSource = {
    id: 'legal_crawl_source:shared', name: 'Nguồn dùng chung', source_type: 'official_listing',
    sitemap_scope: 'central', base_url: 'https://vbpl.vn/van-ban/trung-uong',
    enabled: false, interval_minutes: 10080, lookback_days: 30, max_documents_per_run: 30,
    source_kind: 'web_crawler' as const, unassigned_policy: 'shared' as const, can_delete: true,
  }

  it('shows shared routing when editing and sends an explicit null department', async () => {
    const onUpdate = vi.fn().mockResolvedValue(undefined)
    render(<CrawlerSourceManager sources={[sharedSource]} scanningSourceId={null}
      onRefresh={vi.fn()} onScan={vi.fn()} onCreate={vi.fn()} onUpdate={onUpdate} onDelete={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sửa' }))
    expect(screen.getByRole('combobox', { name: 'Phòng ban nhận đề xuất từ Nguồn dùng chung' })).toHaveTextContent('Dùng chung toàn hệ thống')
    fireEvent.click(screen.getByRole('button', { name: /^Lưu$/ }))
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(sharedSource.id, expect.objectContaining({
      default_organization_unit_id: null, unassigned_policy: 'shared',
    })))
  })

  it('locks repeated enable clicks until the request completes and allows retry', async () => {
    let finish!: () => void
    const onUpdate = vi.fn().mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve }))
    render(<CrawlerSourceManager sources={[sharedSource]} scanningSourceId={null}
      onRefresh={vi.fn()} onScan={vi.fn()} onCreate={vi.fn()} onUpdate={onUpdate} onDelete={vi.fn()} />)
    const button = screen.getByRole('button', { name: 'Bật kiểm tra' })
    fireEvent.click(button)
    fireEvent.click(button)
    fireEvent.click(button)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(button).toBeDisabled()
    finish()
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    expect(onUpdate).toHaveBeenCalledTimes(2)
  })

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

  it('shows degraded crawl evidence instead of reporting a generic success', () => {
    render(
      <CrawlerSourceManager
        sources={[{
          id: 'legal_crawl_source:warning',
          name: 'Nguồn Hải Phòng',
          source_type: 'official_listing',
          sitemap_scope: 'haiphong',
          base_url: 'https://haiphong.gov.vn/van-ban',
          enabled: true,
          interval_minutes: 1440,
          lookback_days: 30,
          max_documents_per_run: 30,
          source_kind: 'web_crawler',
          can_scan: true,
          last_status: 'completed_with_warnings',
          last_error: 'Có 1 bản ghi không xử lý được.',
          last_run_stats: { listing_pages: 2, discovered: 10, created: 3, duplicates: 6, item_errors: 1, pagination_unavailable: 1 },
        }]}
        scanningSourceId={null}
        onRefresh={vi.fn()}
        onScan={vi.fn()}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    expect(screen.getByText('Hoạt động có cảnh báo')).toBeInTheDocument()
    expect(screen.getByText(/2 trang · 10 bản ghi thấy được · 3 mới · 6 trùng đã bỏ qua · 1 lỗi bản ghi · chỉ quét được trang hiện tại/)).toBeInTheDocument()
    expect(screen.getByText(/Có 1 bản ghi không xử lý được/)).toBeInTheDocument()
  })

  it('keeps retired sitemap sources visible but cannot enable or scan them', () => {
    render(
      <CrawlerSourceManager
        sources={[{
          id: 'legal_crawl_source:legacy',
          name: 'VBPL sitemap cũ',
          source_type: 'vbpl_sitemap',
          sitemap_scope: 'central',
          base_url: 'https://vbpl.vn/sitemap.xml',
          enabled: false,
          interval_minutes: 10080,
          lookback_days: 30,
          max_documents_per_run: 30,
          source_kind: 'web_crawler',
          can_scan: false,
          last_status: 'retired',
        }]}
        scanningSourceId={null}
        onRefresh={vi.fn()}
        onScan={vi.fn()}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    expect(screen.getByText('Đã ngừng nguồn cũ')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Quét nguồn này' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Bật kiểm tra' })).not.toBeInTheDocument()
  })

  it('allows an installation-template web source to be removed from operation', () => {
    const onDelete = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(
      <CrawlerSourceManager
        sources={[{
          id: 'legal_crawl_source:default',
          name: 'VBPL Trung ương',
          source_type: 'vbpl_listing',
          sitemap_scope: 'central',
          base_url: 'https://vbpl.vn/van-ban/trung-uong',
          enabled: true,
          interval_minutes: 10080,
          lookback_days: 30,
          max_documents_per_run: 30,
          source_kind: 'web_crawler',
          is_default: true,
          can_delete: true,
          can_scan: true,
        }]}
        scanningSourceId={null}
        onRefresh={vi.fn()}
        onScan={vi.fn()}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={onDelete}
      />,
    )

    expect(screen.getByText(/quản trị viên vẫn có thể xóa khỏi vận hành/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Xóa' }))
    expect(onDelete).toHaveBeenCalledWith('legal_crawl_source:default')
  })

  it('previews a new source without saving it', async () => {
    const onCreate = vi.fn()
    const onPreview = vi.fn().mockResolvedValue({
      status: 'ok',
      mutation_performed: false,
      base_url: 'https://haiphong.gov.vn/van-ban-moi',
      website_type: 'legal_documents',
      discovered_count: 1,
      preview_count: 1,
      truncated: false,
      candidates: [{
        url: 'https://haiphong.gov.vn/van-ban-moi/1',
        title: 'Nghị quyết thử nghiệm',
        context: 'Nội dung',
        source_type: 'document',
      }],
    })
    render(
      <CrawlerSourceManager
        sources={[]}
        scanningSourceId={null}
        onRefresh={vi.fn()}
        onScan={vi.fn()}
        onCreate={onCreate}
        onPreview={onPreview}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText('Tên dễ nhận biết'), { target: { value: 'Nguồn thử nghiệm' } })
    fireEvent.change(screen.getByLabelText('Đường dẫn trang danh sách'), { target: { value: 'https://haiphong.gov.vn/van-ban-moi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra nguồn — không lưu dữ liệu' }))

    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
    expect(onCreate).not.toHaveBeenCalled()
    expect(await screen.findByText('Kết quả quét thử: Nguồn thử nghiệm')).toBeInTheDocument()
    expect(screen.getByText('Nghị quyết thử nghiệm')).toBeInTheDocument()
  })
})
