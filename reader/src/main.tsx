import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "@fontsource-variable/literata/opsz.css";
import "@fontsource-variable/literata/opsz-italic.css";
import "@fontsource/atkinson-hyperlegible/400.css";
import "@fontsource/atkinson-hyperlegible/700.css";
import "@fontsource/caveat/500.css";
import "@fontsource/cabin-sketch/latin-700.css"; // the Imagine-Reader logo
import "@fontsource/noto-serif-bengali/bengali-400.css"; // Bangla books; fetched only for Bangla text
import "@fontsource/noto-serif-bengali/bengali-700.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
