import "./loading.css";

export default function Loading() {
  return (
    <div className="restart-route-loading" role="status" aria-live="polite">
      <span className="restart-route-loading-spinner" aria-hidden="true" />
      <span>Загружаем раздел OttoRestart…</span>
    </div>
  );
}
