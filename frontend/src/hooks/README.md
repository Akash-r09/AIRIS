# frontend/src/hooks/

One data-fetching hook per backend resource (`useForecast.ts`, `useAttribution.ts`, etc.), each a thin wrapper around `lib/api.ts` with local loading/error state.
