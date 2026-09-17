import type { Metadata, Viewport } from "next";
import { Archivo, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import Link from "next/link";
import { ThemeToggle } from "@/components/ThemeToggle";
import { THEME_COLOR, THEME_STORAGE_KEY } from "@/lib/theme";
import "./globals.css";

/*
  Three faces, self-hosted by next/font: no runtime request to Google, no layout shift, and the
  CSS variables below are what globals.css reads.

  No `weight` on the first two on purpose - both are VARIABLE fonts, so one file covers the whole
  range and every weight is available without adding a request. IBM Plex Mono has no variable
  version, so its two weights are listed explicitly; adding a third is a third download.

  If a build ever runs with no network, next/font cannot fetch these and fails the build. The
  fallback is to drop these three declarations and let the stacks in globals.css stand.
*/
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  variable: "--font-plex-sans",
  display: "swap",
});

const archivo = Archivo({
  subsets: ["latin"],
  variable: "--font-archivo",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Well Slippage — PDO / Al Tasnim",
  description:
    "Contractual milestone slippage and task-level activity delay across the active well portfolio.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // A single tag, not a prefers-color-scheme pair: the theme is the USER's choice here, not the
  // OS's, so ThemeToggle rewrites this one tag's content on every switch. A media pair would
  // keep asserting the OS preference and could not be corrected.
  themeColor: THEME_COLOR.light,
};

/*
  Runs before the browser paints anything, which is the whole point: set from an effect, or via
  next/script (which is `afterInteractive` by default), the page paints white first and a dark-mode
  user gets a full-brightness flash on every single navigation.

  It cannot go in a <head> tag - the App Router forbids hand-written <head> in a root layout - so
  it is the first child of <body>, which still executes before any of the body renders.

  Deliberately tiny and dependency-free. Its constants come from lib/theme.ts, which carries NO
  "use client" - importing them from the toggle itself silently produced an unparseable script.
  That file explains why.
*/
const THEME_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('${THEME_STORAGE_KEY}');
    var dark = stored ? stored === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    if (dark) {
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', '${THEME_COLOR.dark}');
    }
  } catch (e) {
    /* Blocked storage, or no matchMedia. Light is the base theme, so doing nothing is correct. */
    document.documentElement.dataset.theme = 'light';
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${plexSans.variable} ${archivo.variable} ${plexMono.variable}`}
      // The script below sets data-theme on this element BEFORE React hydrates, so the server
      // HTML (no attribute) and the live DOM (dark, say) necessarily disagree - React reports it
      // as a hydration mismatch. That disagreement is the design, not a defect: the alternative
      // is rendering the theme on the server, which cannot be done because the preference lives
      // in localStorage and the server never sees it.
      //
      // Scoped to this element's own attributes - it does NOT suppress warnings for any child -
      // so a genuine mismatch anywhere inside the app is still reported.
      suppressHydrationWarning
    >
      <body className="min-h-screen">
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />

        {/* First tab stop on every page. A data-dense table is exactly where a keyboard user
            needs to skip the chrome, and it costs nothing when unused. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:rounded-md focus:border focus:border-line focus:bg-surface focus:px-4 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>

        {/* Solid, not translucent: a blurred backdrop buys nothing over a flat ground and costs
            a compositing layer under every scroll. */}
        <header className="sticky top-0 z-30 border-b border-line bg-surface">
          <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4 sm:px-6">
            <Link href="/" className="flex items-center gap-2.5 no-underline">
              <span
                aria-hidden
                className="grid h-[26px] w-[26px] place-items-center rounded-md bg-accent text-on-accent"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path
                    d="M2 12V6M7 12V2M12 12V9"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                  />
                </svg>
              </span>
              <span className="display text-sm font-semibold text-ink">Well Slippage</span>
            </Link>
            <span aria-hidden className="h-[18px] w-px bg-line" />
            <span className="hidden text-xs text-ink-3 sm:inline">
              PDO / Al&nbsp;Tasnim · operations intelligence
            </span>
            <div className="ml-auto">
              <ThemeToggle />
            </div>
          </div>
        </header>

        <main id="main" className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 sm:py-8">
          {children}
        </main>

        <footer className="mx-auto max-w-[1400px] px-4 pb-10 text-xs leading-relaxed text-ink-3 sm:px-6">
          <p>
            Figures are produced by a verified SQL pipeline and show recorded schedule slippage.
            They do not establish an underlying cause.
          </p>
        </footer>
      </body>
    </html>
  );
}
