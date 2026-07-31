import { formatDateTime, humanize } from "./format";

export function SignalTrail({
  status,
  timestamp,
  provider,
  compact = false,
}: {
  status: string;
  timestamp: string;
  provider: string;
  compact?: boolean;
}) {
  const tone =
    ["active", "healthy", "fresh", "succeeded"].includes(status)
      ? "ok"
      : ["missing", "failed", "unknown"].includes(status)
        ? "danger"
        : "warning";
  return (
    <div className={`signal-trail signal-trail--${tone}${compact ? " signal-trail--compact" : ""}`}>
      <span className="signal-trail__dot" aria-hidden="true" />
      <span className="signal-trail__state">{humanize(status)}</span>
      <span className="signal-trail__line" aria-hidden="true" />
      <time dateTime={timestamp}>{formatDateTime(timestamp)}</time>
      <span className="signal-trail__line" aria-hidden="true" />
      <span className="mono signal-trail__provider">{provider}</span>
    </div>
  );
}
