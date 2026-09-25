"use client";

import { useEffect, useRef, useState } from "react";
import { THEME_COLOR, THEME_STORAGE_KEY, type Theme } from "@/lib/theme";

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Write the theme everywhere it has to be true at once.
 *
 * The meta tag matters as much as the attribute: without it the browser's own chrome - the
 * address bar on mobile, the title bar of an installed PWA - stays the colour of the theme the
 * user just left, which is more jarring than no transition at all.
 */
function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", THEME_COLOR[theme]);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Private mode and blocked storage both throw. The theme still applies for this page;
    // only the memory of it is lost, which is not worth failing the click over.
  }
}

export function ThemeToggle() {
  // Null until mounted, so the server and the first client render agree. The ICONS are not
  // driven by this - they are driven by CSS on <html data-theme>, so they are already correct
  // in the server HTML and never flash the wrong way while hydrating.
  const [theme, setTheme] = useState<Theme | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  }, []);

  // Follow the OS only while the user has never chosen for themselves. Once they have, their
  // choice outranks it - an explicit decision should not be quietly overridden at sunset.
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => {
      try {
        if (localStorage.getItem(THEME_STORAGE_KEY)) return;
      } catch {
        return;
      }
      const next: Theme = event.matches ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      setTheme(next);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  function swap(next: Theme) {
    applyTheme(next);
    setTheme(next);
  }

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";

    if (prefersReducedMotion()) {
      swap(next);
      return;
    }

    // No View Transitions (Firefox, older Safari): cross-fade the colours instead. The class is
    // removed once the transition has run, so it never interferes with ordinary hover states.
    if (!document.startViewTransition) {
      const root = document.documentElement;
      root.classList.add("theme-anim");
      swap(next);
      window.setTimeout(() => root.classList.remove("theme-anim"), 320);
      return;
    }

    // The reveal grows from the button itself, so the change reads as something this control
    // did rather than as the page blinking. Radius reaches the furthest corner of the viewport.
    const rect = buttonRef.current?.getBoundingClientRect();
    const x = rect ? rect.left + rect.width / 2 : window.innerWidth / 2;
    const y = rect ? rect.top + rect.height / 2 : 0;
    const radius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y),
    );

    const transition = document.startViewTransition(() => swap(next));
    transition.ready
      .then(() => {
        document.documentElement.animate(
          {
            clipPath: [
              `circle(0px at ${x}px ${y}px)`,
              `circle(${radius}px at ${x}px ${y}px)`,
            ],
          },
          {
            duration: 520,
            easing: "cubic-bezier(0.22, 1, 0.36, 1)",
            pseudoElement: "::view-transition-new(root)",
          },
        );
      })
      .catch(() => {
        // `ready` REJECTS whenever the browser skips the transition - a hidden tab, a second
        // transition starting on top of this one, or the snapshot timing out. The theme itself
        // has already been applied by the callback above, so there is nothing to repair: only
        // the sweep is lost. Swallowed deliberately, because an unhandled rejection here would
        // put a red error in the console for a click that worked perfectly.
      });
  }

  const label =
    theme === null
      ? "Switch theme"
      : theme === "dark"
        ? "Switch to light theme"
        : "Switch to dark theme";

  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
    >
      {/* Both icons are always in the DOM and swapped by CSS keyed on <html data-theme>, so the
          right one is already in the server HTML and neither flashes during hydration. They
          rotate past each other: the sun leaves as the moon arrives. Styles live in globals.css
          next to the other theme rules - see `.theme-icon`. */}
      <span aria-hidden className="relative block h-4 w-4">
        <svg viewBox="0 0 16 16" fill="none" className="theme-icon theme-icon-sun">
          <circle cx="8" cy="8" r="3.25" stroke="currentColor" strokeWidth="1.5" />
          <path
            d="M8 1v1.5M8 13.5V15M15 8h-1.5M2.5 8H1M12.95 3.05l-1.06 1.06M4.11 11.89l-1.06 1.06M12.95 12.95l-1.06-1.06M4.11 4.11L3.05 3.05"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
        <svg viewBox="0 0 16 16" fill="none" className="theme-icon theme-icon-moon">
          <path
            d="M13.5 9.6A6 6 0 0 1 6.4 2.5a6 6 0 1 0 7.1 7.1z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    </button>
  );
}
