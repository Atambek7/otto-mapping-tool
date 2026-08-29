"use client";

import { useEffect } from "react";
import { cleanSessionCache } from "@/lib/cache";

export function CacheCleaner() {
  useEffect(() => {
    cleanSessionCache().catch(console.error);
  }, []);

  return null;
}
