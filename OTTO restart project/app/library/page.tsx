import type { Metadata } from "next";
import { OttoLibraryWorkspace } from "@/components/otto-library-workspace";

export const metadata: Metadata = { title: "OTTO Library" };
export default function LibraryPage() { return <OttoLibraryWorkspace />; }
