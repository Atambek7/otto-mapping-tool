import type { Metadata } from "next";
import { WelcomePage } from "@/components/welcome/welcome-page";

export const metadata: Metadata = { title: "Добро пожаловать · OttoRestart" };

export default function HomePage() {
  return <WelcomePage />;
}
