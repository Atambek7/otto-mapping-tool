import Link from "next/link";
import { Check, ClipboardCheck, Layers3, PackageSearch, Workflow } from "lucide-react";

export type RestartStage = "candidates" | "batches" | "quality" | "queue";
const stages = [
  { id: "candidates", label: "Кандидаты", href: "/review", icon: PackageSearch },
  { id: "batches", label: "Партия Restart", href: "/batches", icon: Layers3 },
  { id: "quality", label: "Quality Control", href: "/quality-control", icon: ClipboardCheck },
  { id: "queue", label: "Очередь", href: "/queue", icon: Workflow },
] as const;

export function WorkflowStepper({ active }: { active: RestartStage }) {
  return <nav className="workflow-stepper" aria-label="Этапы Restart">
    {stages.map(({ id, label, href, icon: Icon }, index) => {
      const current = id === active;
      const previous = stages.findIndex((stage) => stage.id === active) > index;
      return <Link className={`workflow-step${current ? " current" : ""}${previous ? " complete" : ""}`} href={href} aria-current={current ? "step" : undefined} key={id}>
        <span className="workflow-step-icon">{previous ? <Check size={15} /> : <Icon size={15} />}</span><span>{label}</span>
      </Link>;
    })}
  </nav>;
}
