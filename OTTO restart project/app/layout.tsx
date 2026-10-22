import type { Metadata } from "next";
import type { ReactNode } from "react";
import { RestartShell } from "@/components/restart-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "OttoRestart",
    template: "%s · OttoRestart",
  },
  description: "Restart 3/12M operations for OTTO Market",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <RestartShell>{children}</RestartShell>
      </body>
    </html>
  );
}
