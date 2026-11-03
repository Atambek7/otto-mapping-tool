import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Montserrat } from "next/font/google";
import { RestartShell } from "@/components/restart-shell";
import "./tokens.css";
import "./globals.css";
import "@/components/catalogs/catalogs.css";

const montserrat = Montserrat({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  variable: "--font-montserrat",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: {
    default: "OttoRestart 3/12M",
    template: "%s — OttoRestart 3/12M",
  },
  description: "Инструмент подготовки и повторного запуска в продажу товаров OTTO 3 и 12 месяцев.",
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
