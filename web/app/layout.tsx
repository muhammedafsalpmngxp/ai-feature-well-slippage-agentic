import type { Metadata, Viewport } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Well Slippage — PDO / Al Tasnim",
  description:
    "Contractual milestone slippage and task-level activity delay across the active well portfolio.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f8fa" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0f17" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        {/* First tab stop on every page. A data-dense table is exactly where a keyboard user
            needs to skip the chrome, and it costs nothing when unused. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:shadow-lg"
        >
          Skip to content
        </a>

        <header className="sticky top-0 z-30 border-b border-line bg-surface/85 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4 sm:px-6">
            <Link href="/" className="flex items-center gap-2.5">
              <span
                aria-hidden
                className="grid h-7 w-7 place-items-center rounded-md bg-accent text-[11px] font-bold text-white"
              >
                WS
              </span>
              <span className="text-sm font-semibold tracking-tight">Well Slippage</span>
            </Link>
            <span className="hidden text-xs text-ink-3 sm:inline">
              PDO / Al&nbsp;Tasnim · operations intelligence
            </span>
          </div>
        </header>

        <main id="main" className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 sm:py-8">
          {children}
        </main>

        <footer className="mx-auto max-w-[1400px] px-4 pb-10 text-xs text-ink-3 sm:px-6">
          <p>
            Figures are produced by a verified SQL pipeline and show recorded schedule slippage.
            They do not establish an underlying cause.
          </p>
        </footer>
      </body>
    </html>
  );
}
