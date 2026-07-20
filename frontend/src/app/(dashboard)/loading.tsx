export default function DashboardLoading() {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6" role="status" aria-live="polite">
      <div className="space-y-2">
        <div className="h-7 w-56 animate-pulse rounded bg-muted" />
        <div className="h-4 w-80 max-w-full animate-pulse rounded bg-muted" />
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {[0, 1, 2].map((item) => (
          <div key={item} className="h-28 animate-pulse rounded-xl border bg-muted/60" />
        ))}
      </div>
      <div className="h-80 animate-pulse rounded-xl border bg-muted/60" />
      <span className="sr-only">Đang tải nội dung...</span>
    </div>
  )
}
