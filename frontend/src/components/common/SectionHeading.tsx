interface SectionHeadingProps {
  title: string;
  description?: string;
}

export function SectionHeading({ title, description }: SectionHeadingProps) {
  return (
    <div className="mb-4">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-white/40">{title}</h2>
      {description && <p className="mt-1 text-sm text-white/50">{description}</p>}
    </div>
  );
}
