import "./appica.css";
import { createRoot } from "react-dom/client";
import { BrowseIsland } from "./islands/browse";
import { ComposeIsland } from "./islands/compose";

// ponytail: ThemeProvider lives under subpath export; "." does not re-export it (verified dist/index.js)
import { ThemeProvider } from "@appica/ui-react/providers/theme-provider";

function mount() {
  document.querySelectorAll<HTMLElement>("[data-island]").forEach((el) => {
    const island = el.dataset.island;
    const root = createRoot(el);
    if (island === "browse") {
      root.render(
        <ThemeProvider>
          <BrowseIsland />
        </ThemeProvider>,
      );
    } else if (island === "compose") {
      root.render(
        <ThemeProvider>
          <ComposeIsland />
        </ThemeProvider>,
      );
    }
  });
}

// hydrate islands when present; no-op on pages without data-island (JS-off fallback preserved)
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
