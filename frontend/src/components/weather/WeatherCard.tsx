import type { LucideIcon } from "lucide-react";

interface WeatherCardProps {
  icon: LucideIcon;
  label: string;
  value: string;
}

export function WeatherCard({ icon: Icon, label, value }: WeatherCardProps) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-border-subtle bg-white/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-2.5 text-white/60">
        <Icon size={15} aria-hidden="true" />
        <span className="text-sm">{label}</span>
      </div>
      <span className="text-sm font-medium text-white">{value}</span>
    </div>
  );
}
