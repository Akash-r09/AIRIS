import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// AIRIS frontend entrypoint. App.tsx owns routing (Landing / Dashboard);
// page content is filled in across the Phase 3–5 build-out.
ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
