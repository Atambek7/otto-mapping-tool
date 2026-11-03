"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/api";
import { useAccount } from "@/lib/useAccount";

type Factory = {
  id: string;
  name: string;
  submission_items: number;
  last_attempt_at: string | null;
  initial_availability: { total: number; confirmed: number; pending: number; needs_review: number };
};
type Result = {
  account: string;
  count: number;
  factories: Factory[];
  history_complete: boolean;
  evidence_note: string;
};

export default function DripFactoriesPage() {
  const { account } = useAccount();
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(false);
    fetchWithAuth(`/pipeline/drip-factories?account_name=${encodeURIComponent(account)}`, { signal: controller.signal }, 0)
      .then(async response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const value: Result = await response.json();
        if (!controller.signal.aborted && value.account === account) setData(value);
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [account]);

  return <main className="p-6 space-y-4">
    <h1 className="text-2xl font-semibold">Фабрики Drip Feed · {account}</h1>
    {error && <p role="alert">Не удалось загрузить фабрики.</p>}
    {!data && !error && <p>Загрузка…</p>}
    {data && <>
      <p>Фабрик с сохранёнными записями Drip Feed: {data.count}</p>
      <p className="text-sm text-slate-500">Попытка отправки не доказывает обработку OTTO или статус ONLINE. {data.evidence_note}</p>
      {!data.history_complete && <p className="text-sm text-slate-500">История до включения этих записей неполная; старые публикации могут отсутствовать.</p>}
      <ul className="space-y-2">{data.factories.map(factory =>
        <li key={factory.id} className="rounded border border-slate-200 p-3 dark:border-slate-700">
          <Link className="text-indigo-500 hover:underline" href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}>{factory.name}</Link>
          <p className="text-sm text-slate-500">Отмечено попыток отправки товаров: {factory.submission_items}</p>
          {factory.last_attempt_at && <p className="text-sm text-slate-500">Последняя отметка: {new Date(factory.last_attempt_at).toLocaleString("ru-RU")}</p>}
          {factory.initial_availability.total > 0 && <p className="text-sm text-slate-500">Разовая настройка после публикации: подтверждено {factory.initial_availability.confirmed} из {factory.initial_availability.total}; ожидают обработки {factory.initial_availability.pending}; требуют сверки {factory.initial_availability.needs_review}</p>}
        </li>
      )}</ul>
    </>}
  </main>;
}
