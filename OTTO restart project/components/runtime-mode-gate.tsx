import type { ReactNode } from "react";
import { FlaskConical } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";

export function RuntimeModeGate({
  title,
  children,
  demoEnabled = isOttoRestartDemoMode(),
  showDemoBanner = true,
}: {
  title: string;
  children?: ReactNode;
  demoEnabled?: boolean;
  showDemoBanner?: boolean;
}) {
  if (!demoEnabled) {
    return (
      <ApiState
        kind="unavailable"
        title={title}
        description={`Режим ${getOttoRestartMode()}: проверенный источник для этой страницы не подключён. Mock-данные отключены; backend может вернуть UNVERIFIED или NOT_IMPLEMENTED.`}
      />
    );
  }

  return (
    <>
      {showDemoBanner ? (
        <div className="demo-mode-banner" role="status">
          <FlaskConical size={19} />
          <div>
            <strong>DEMO · только локальная симуляция</strong>
            <span>Данные и изменения не подтверждены OTTO/Aftercool и не являются production-состоянием.</span>
          </div>
        </div>
      ) : null}
      {children}
    </>
  );
}
