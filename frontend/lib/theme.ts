/**
 * Theme constants, shared by a SERVER component (the root layout's pre-paint script) and a
 * CLIENT component (ThemeToggle).
 *
 * ⚠ THIS FILE MUST NOT CARRY "use client".
 *
 * That is the whole reason it exists. These constants used to live in ThemeToggle.tsx, which is
 * a client module - and a plain value exported from a client module and imported by a server
 * component does not arrive as its value. Next replaces it with a client REFERENCE, and only
 * components survive that crossing. Interpolated into the layout's inline script, the reference
 * stringified into the function body of Next's own "you called a client export from the server"
 * error, which is not valid JavaScript inside a string literal:
 *
 *     localStorage.getItem('function() { throw new Error("Attempted to call ...") }')
 *
 * The script then failed to parse in the browser, silently: no theme attribute was ever set, the
 * stored preference was read by nobody, and the page always painted light while localStorage
 * happily said "dark". A module with no directive is shared code and is inlined into both.
 */

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "well-slippage-theme";

/**
 * The browser-chrome colour per theme, kept beside the palette it mirrors.
 *
 * These two are the only hex values outside globals.css, and they are duplicates of
 * --color-canvas in each theme. A <meta> tag cannot read a CSS custom property, so there is no
 * way to derive them; if --color-canvas changes in globals.css, change it here too.
 */
export const THEME_COLOR: Record<Theme, string> = {
  light: "#ffffff",
  dark: "#0c1016",
};
