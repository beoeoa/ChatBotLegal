import type { Root, RootContent } from 'mdast'

/** Interpret only Markdown's common <br> line separator, never arbitrary HTML. */
export function remarkLegalBreaks() {
  return (tree: Root) => {
    const visit = (parent: { children: RootContent[] }) => {
      parent.children = parent.children.map((node) => {
        if (node.type === 'html' && /^\s*<br\s*\/?>(?:\s*<br\s*\/?>)*\s*$/i.test(node.value)) {
          return { type: 'break' } as RootContent
        }
        if ('children' in node) visit(node as { children: RootContent[] })
        return node
      })
    }
    visit(tree)
  }
}
