import { Sparkline } from "./Sparkline";

export function KpiCard({
  label,
  value,
  unit,
  sub,
  tone = "default",
  spark,
}: {
  label: string;
  value: string;
  unit?: string;
  sub?: React.ReactNode;
  tone?: "default" | "ok" | "warn" | "crit";
  /** 7–14 значений: мини-график под цифрой */
  spark?: number[];
}) {
  const toneClass = {
    default: "text-ink-100",
    ok: "text-ok",
    warn: "text-warn",
    crit: "text-crit",
  }[tone];

  return (
    <div className="panel p-4">
      <div className="panel-title">{label}</div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className={`num text-3xl font-semibold ${toneClass}`}>{value}</span>
        {unit && <span className="text-sm text-ink-300">{unit}</span>}
      </div>
      {sub && <div className="mt-1.5 text-xs text-ink-300">{sub}</div>}
      {spark && spark.length > 2 && <Sparkline values={spark} label={`${label}: динамика за период`} />}
    </div>
  );
}
