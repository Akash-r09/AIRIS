import type { HTMLAttributes } from "react";

type BadgeVariant = "neutral" | "accent" | "success" | "warning" | "danger";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  neutral: "bg-white/5 text-white/70 border-border-subtle",
  accent: "bg-accent-soft text-accent border-accent/20",
  success: "bg-success-soft text-success border-success/20",
  warning: "bg-warning-soft text-warning border-warning/20",
  danger: "bg-danger-soft text-danger border-danger/20",
};

export function Badge({ variant = "neutral", className = "", children, ...rest }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1
        text-xs font-medium ${VARIANT_CLASSES[variant]} ${className}`}
      {...rest}
    >
      {children}
    </span>
  );
}
