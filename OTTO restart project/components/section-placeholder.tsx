import { ArrowUpRight, Construction } from "lucide-react";

export function SectionPlaceholder({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <section className="page-section">
      <div className="page-heading">
        <div>
          <div className="eyebrow">{eyebrow}</div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        <span className="phase-chip"><span /> Фаза 1 · интерфейс</span>
      </div>
      <div className="empty-panel">
        <div className="empty-icon"><Construction size={22} /></div>
        <div>
          <h2>Раздел подготовлен</h2>
          <p>Подключение данных и действия появятся на следующих этапах. Сейчас этот экран ничего не меняет в OTTO.</p>
        </div>
        <ArrowUpRight className="empty-arrow" size={18} />
      </div>
    </section>
  );
}
