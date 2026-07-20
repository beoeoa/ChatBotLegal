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
})
