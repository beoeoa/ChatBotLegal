import { describe, expect, it } from 'vitest'
import { remarkLegalBreaks } from './remark-legal-breaks'
import type { Root } from 'mdast'

describe('legal Markdown line breaks', () => {
  it('renders a line break in a table without enabling HTML or changing code', () => {
    const tree: Root = { type: 'root', children: [{ type: 'table', children: [
      { type: 'tableRow', children: [{ type: 'tableCell', children: [
        { type: 'text', value: 'Hồ sơ' }, { type: 'html', value: '<br />' },
        { type: 'text', value: 'Nguồn' }, { type: 'html', value: '<img src=x onerror=alert(1)>' },
        { type: 'inlineCode', value: '<br>' },
      ] }] },
    ] }] }
    remarkLegalBreaks()(tree)
    const serialized = JSON.stringify(tree)
    expect(serialized).toContain('"type":"break"')
    expect(serialized).toContain('"type":"html","value":"<img')
    expect(serialized).toContain('"type":"inlineCode","value":"<br>"')
  })
})
