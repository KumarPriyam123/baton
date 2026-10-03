import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { Providers } from "./app/providers";
import { router } from "./app/router";
import "./styles/globals.css";
import { applyTheme, getTheme } from "./lib/theme";

// Before the first paint, so a dark-mode user never sees a white flash.
applyTheme(getTheme());

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
