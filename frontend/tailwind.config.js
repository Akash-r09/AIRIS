/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],

  theme: {
    extend: {
      colors: {
        background: "rgb(var(--background) / <alpha-value>)",
        foreground: "rgb(var(--foreground) / <alpha-value>)",

        surface: "rgb(var(--surface) / <alpha-value>)",
        card: "rgb(var(--card) / <alpha-value>)",

        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-soft": "rgb(var(--accent-soft) / <alpha-value>)",

        success: "rgb(var(--success) / <alpha-value>)",
        "success-soft": "rgb(var(--success-soft) / <alpha-value>)",

        warning: "rgb(var(--warning) / <alpha-value>)",
        "warning-soft": "rgb(var(--warning-soft) / <alpha-value>)",

        danger: "rgb(var(--danger) / <alpha-value>)",
        "danger-soft": "rgb(var(--danger-soft) / <alpha-value>)",

        muted: "rgb(var(--muted) / <alpha-value>)",

        "border-subtle": "rgb(var(--border-subtle) / <alpha-value>)",
      },

      boxShadow: {
        card:
          "0 10px 30px rgba(0,0,0,.35), 0 1px 0 rgba(255,255,255,.03) inset",

        glow:
          "0 0 30px rgba(59,130,246,.18)",

        xlsoft:
          "0 30px 80px rgba(0,0,0,.45)",
      },

      borderRadius: {
        xl2: "1.25rem",
        "3xl": "1.75rem",
      },

      backdropBlur: {
        xs: "2px",
      },

      transitionTimingFunction: {
        smooth: "cubic-bezier(.22,1,.36,1)",
      },

      animation: {
        float: "float 5s ease-in-out infinite",
        glow: "glow 4s ease-in-out infinite",
        pulseSlow: "pulseSlow 3s ease infinite",
      },

      keyframes: {
        float: {
          "0%,100%": {
            transform: "translateY(0px)",
          },
          "50%": {
            transform: "translateY(-4px)",
          },
        },

        glow: {
          "0%,100%": {
            opacity: ".55",
          },
          "50%": {
            opacity: "1",
          },
        },

        pulseSlow: {
          "0%,100%": {
            transform: "scale(1)",
          },
          "50%": {
            transform: "scale(1.03)",
          },
        },
      },

      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "sans-serif",
        ],
      },
    },
  },

  plugins: [],
};