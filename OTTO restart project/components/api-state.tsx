import { AlertCircle, CircleSlash, LoaderCircle, RefreshCw, ShieldAlert } from "lucide-react";

type ApiStateProps =
  | { kind: "loading"; label?: string }
  | { kind: "unavailable"; title: string; description: string }
  | { kind: "empty"; title: string; description?: string }
  | { kind: "error"; title?: string; description: string; onRetry?: () => void }
  | { kind: "permission-denied"; title?: string; description?: string };

export function ApiState(props: ApiStateProps) {
  if (props.kind === "loading") {
    return (
      <div className="api-state" role="status" aria-live="polite">
        <LoaderCircle className="api-state-icon is-spinning" size={20} />
        <span>{props.label ?? "Загружаем данные OttoRestart…"}</span>
      </div>
    );
  }

  if (props.kind === "permission-denied") {
    return (
      <div className="api-state api-state-warning" role="alert">
        <ShieldAlert className="api-state-icon" size={20} />
        <div><strong>{props.title ?? "Нет доступа"}</strong><span>{props.description ?? "Для выбранного аккаунта нет разрешения."}</span></div>
      </div>
    );
  }

  if (props.kind === "unavailable") {
    return (
      <div className="api-state" role="status">
        <CircleSlash className="api-state-icon" size={20} />
        <div><strong>{props.title}</strong><span>{props.description}</span></div>
      </div>
    );
  }

  if (props.kind === "error") {
    return (
      <div className="api-state api-state-error" role="alert">
        <AlertCircle className="api-state-icon" size={20} />
        <div><strong>{props.title ?? "Не удалось загрузить данные"}</strong><span>{props.description}</span></div>
        {props.onRetry ? <button className="api-state-retry" onClick={props.onRetry} type="button"><RefreshCw size={14} /> Повторить</button> : null}
      </div>
    );
  }

  return (
    <div className="api-state" role="status">
      <CircleSlash className="api-state-icon" size={20} />
      <div><strong>{props.title}</strong>{props.description ? <span>{props.description}</span> : null}</div>
    </div>
  );
}
