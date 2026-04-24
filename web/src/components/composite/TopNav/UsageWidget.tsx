export function UsageWidget() {
  return (
    <div
      className="flex shrink-0 items-center gap-1 text-sm text-muted-foreground"
      aria-label="本月用量"
      title="本月用量（尚未啟用）"
    >
      <span aria-hidden>📊</span>
      <span className="tabular-nums">--</span>
    </div>
  )
}
