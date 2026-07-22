import { AlertTriangle } from "lucide-react";
import { Button } from "../ui/Button";

interface ErrorStateProps {
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({ message = "Something went wrong loading this data.", onRetry, className = "" }: ErrorStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center gap-3 py-12 text-center ${className}`} role="alert">
      <AlertTriangle className="text-danger" size={22} aria-hidden="true" />
      <p className="max-w-xs text-sm text-white/60">{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
