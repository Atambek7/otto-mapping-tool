import { Database } from "lucide-react";

export function ApiConnectionStatus() {
  return (
    <div
      className="connection-state"
      title="Рабочая среда OttoRestart 3/12M"
    >
      <Database size={13} />
      <span>Локальный режим</span>
    </div>
  );
}
