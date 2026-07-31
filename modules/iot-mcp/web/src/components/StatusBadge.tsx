import { humanize } from "./format";

export function StatusBadge({ value }: { value: string }) {
  const tone =
    ["healthy", "active", "fresh", "succeeded", "approved", "configured"].includes(value)
      ? "ok"
      : ["degraded", "stale", "pending_confirmation", "medium"].includes(value)
        ? "warning"
        : ["failed", "rejected", "missing", "expired", "high"].includes(value)
          ? "danger"
          : "neutral";
  return (
    <span className={`status-label status-label--${tone}`}>
      <span className="status-label__mark" aria-hidden="true" />
      {humanize(value)}
    </span>
  );
}
