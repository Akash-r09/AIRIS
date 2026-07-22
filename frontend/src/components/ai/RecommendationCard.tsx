import { Badge } from "../ui/Badge";
import { RISK_LEVEL_BADGE_VARIANT, RISK_LEVEL_LABEL } from "../../lib/constants";
import type { AIRecommendation } from "../../types/forecast";

interface RecommendationCardProps {
  recommendation: AIRecommendation;
}

export function RecommendationCard({ recommendation }: RecommendationCardProps) {
  return (
    <div className="rounded-xl border border-border-subtle bg-white/[0.02] p-4">
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-sm font-medium text-white">{recommendation.title}</h4>
        <Badge variant={RISK_LEVEL_BADGE_VARIANT[recommendation.severity]} className="shrink-0">
          {RISK_LEVEL_LABEL[recommendation.severity]}
        </Badge>
      </div>
      <p className="mt-1.5 text-sm leading-relaxed text-white/60">{recommendation.description}</p>
    </div>
  );
}
