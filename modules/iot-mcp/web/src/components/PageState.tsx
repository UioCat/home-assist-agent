import { ApiError } from "../api/client";

export function PageState({
  state,
  label,
  detail,
  onRetry,
}: {
  state: "loading" | "empty" | "error";
  label: string;
  detail?: string;
  onRetry?: () => void;
}) {
  if (state === "loading") {
    return (
      <div className="page-state" role="status" aria-live="polite">
        <span className="loader" aria-hidden="true" />
        <div><strong>{label}</strong><span>正在与控制平面同步</span></div>
      </div>
    );
  }
  return (
    <div className={`page-state page-state--${state}`} role={state === "error" ? "alert" : "status"}>
      <span className="state-mark" aria-hidden="true">{state === "error" ? "!" : "∅"}</span>
      <div>
        <strong>{label}</strong>
        {detail ? <span className="mono">{detail}</span> : null}
      </div>
      {onRetry ? <button className="button button--secondary" onClick={onRetry}>重试</button> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <PageState
      state="error"
      label={apiError?.message ?? (error instanceof Error ? error.message : "读取失败")}
      detail={apiError ? `${apiError.code}${apiError.requestId ? ` · ${apiError.requestId}` : ""}` : undefined}
      onRetry={onRetry}
    />
  );
}
