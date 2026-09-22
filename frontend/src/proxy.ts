import { NextResponse } from 'next/server'

export function proxy() {
  // Root landing is handled by app/page.tsx (role-based home).
  // Do not force a universal redirect to /notebooks or /legal-import.
  return NextResponse.next()
}

export const config = {
  matcher: [
    '/((?!api|_next/static|_next/image|favicon.ico).*)',
  ],
}
