"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function LegacyFactoryRedirectPage() {
  const params = useParams();
  const router = useRouter();

  useEffect(() => {
    const factoryId = params?.factory_id;
    if (factoryId) {
      router.replace(`/dashboard/factories/${factoryId}`);
    }
  }, [params, router]);

  return (
    <div className="p-12 flex flex-col items-center justify-center text-center space-y-3">
      <div className="w-8 h-8 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin" />
      <p className="text-sm font-medium text-slate-600 dark:text-slate-300">
        Navigating to factory workspace...
      </p>
    </div>
  );
}
