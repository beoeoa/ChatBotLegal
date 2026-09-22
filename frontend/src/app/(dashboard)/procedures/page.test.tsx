import { describe, expect, it } from 'vitest'

// Pure source assertions guard the interaction contract without coupling to
// the dashboard providers used by the full page.
const source = await import('./page?raw').then((module) => module.default)

describe('FAQ two-column page contract', () => {
  it('keeps FAQ selection in-page and limits official forms to three', () => {
    expect(source).toContain("setSelectedId(faq.id)")
    expect(source).toContain("(selected.forms || []).slice(0, 3)")
    expect(source).toContain("selected.requires_forms")
  })

  it('shows the approved-form empty state and preserves search/domain on Back', () => {
    expect(source).toContain('forms_unavailable')
    expect(source).toContain('buildSearchBackHref')
    expect(source).toContain('href={backHref}')
  })

  it('loads the active release catalog and presents at most ten procedures per page', () => {
    expect(source).toContain('const PAGE_SIZE = 10')
    expect(source).toContain("'/procedures/forms-catalog/public-catalog?audience=citizen'")
    expect(source).toContain("'/faq?review_status=approved&limit=500'")
    expect(source).toContain('const totalPages')
    expect(source).toContain('setPage((current) => Math.min(totalPages, current + 1))')
    expect(source).toContain('Trang {page}/{totalPages}')
  })

  it('keeps the route read-only and lets the active release own public forms', () => {
    expect(source).toContain('export default function ProceduresPage()')
    expect(source).toContain('return <ProcedureCatalogPage />')
    expect(source).toContain('releaseResponse.data.items')
    expect(source).toContain('forms: OfficialForm[] = (published.forms || [])')
    expect(source).not.toContain('guidance?.forms?.length ? guidance.forms')
    expect(source).toContain('question: guidance?.question || published.name')
    expect(source).toContain('documents_required: guidance?.documents_required?.length')
    expect(source).toContain('Làm mới danh mục')
  })
})
