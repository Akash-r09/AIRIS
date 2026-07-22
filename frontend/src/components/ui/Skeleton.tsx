interface SkeletonProps {
  className?: string;
  rounded?: string;
}

export function Skeleton({ className = "", rounded = "rounded-lg" }: SkeletonProps) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={`animate-pulse bg-white/[0.06] ${rounded} ${className}`}
    />
  );
}
