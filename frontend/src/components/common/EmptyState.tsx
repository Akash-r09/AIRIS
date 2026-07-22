import { Inbox } from "lucide-react";

interface EmptyStateProps {
  message?: string;
  className?: string;
}

export function EmptyState({ message = "No data available yet.", className = "" }: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center gap-3 py-12 text-center ${className}`}>
      <Inbox className="text-white/30" size={22} aria-hidden="true" />
      <p className="max-w-xs text-sm text-white/50">{message}</p>
    </div>
  );
}
