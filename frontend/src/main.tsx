import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import { App } from "./App";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Missing #root element");
}

createRoot(rootElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
