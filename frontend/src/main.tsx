import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// AIRIS frontend entrypoint — Milestone 0.
// App.tsx itself is a minimal placeholder until Milestone 12
// (frontend skeleton) and Milestone 13 (data wiring) land.
ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
