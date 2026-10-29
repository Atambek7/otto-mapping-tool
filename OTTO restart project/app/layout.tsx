import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Montserrat } from "next/font/google";
import { RestartShell } from "@/components/restart-shell";
import "./globals.css";
import "@/components/catalogs/catalogs.css";

const montserrat = Montserrat({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  variable: "--font-montserrat",
  weight: ["300", "400", "500", "600", "700", "800"],
});

export const metadata: Metadata = {
  title: {
    default: "OttoRestart",
    template: "%s · OttoRestart",
  },
  description: "Restart 3/12M operations for OTTO Market",
  icons: {
    icon: [
      { url: "/restart/favicon.ico", sizes: "any" },
      { url: "/restart/icon.png", type: "image/png" },
      { url: "/restart/favicon.png", type: "image/png", sizes: "32x32" },
    ],
    shortcut: "/restart/favicon.ico",
    apple: "/restart/apple-icon.png",
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru" className={montserrat.variable}>
      <body className={montserrat.className}>
        <RestartShell>{children}</RestartShell>
      </body>
    </html>
  );
}
