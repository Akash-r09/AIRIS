import { forwardRef, type HTMLAttributes } from "react";
import { motion } from "framer-motion";

type NativeDivProps = Omit<
  HTMLAttributes<HTMLDivElement>,
  | "onDrag"
  | "onDragStart"
  | "onDragEnd"
  | "onAnimationStart"
  | "onAnimationEnd"
  | "onAnimationIteration"
>;

interface CardProps extends NativeDivProps {
  hoverable?: boolean;
  padded?: boolean;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  (
    {
      hoverable = false,
      padded = true,
      className = "",
      children,
      ...rest
    },
    ref
  ) => {
    return (
      <motion.div
        ref={ref}
        whileHover={
          hoverable
            ? {
                y: -6,
                scale: 1.01,
              }
            : undefined
        }
        transition={{
          duration: 0.25,
          ease: [0.22, 1, 0.36, 1],
        }}
        className={`
          relative
          overflow-hidden
          rounded-2xl

          border
          border-white/10

          bg-gradient-to-br
          from-white/[0.06]
          via-white/[0.03]
          to-transparent

          backdrop-blur-xl

          shadow-card

          before:absolute
          before:inset-0
          before:bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,.08),transparent_45%)]

          after:absolute
          after:inset-px
          after:rounded-[15px]
          after:border
          after:border-white/[0.03]

          ${hoverable ? "cursor-pointer hover:shadow-glow" : ""}

          ${padded ? "p-5" : ""}

          ${className}
        `}
        {...rest}
      >
        <div className="relative z-10">{children}</div>
      </motion.div>
    );
  }
);

Card.displayName = "Card";