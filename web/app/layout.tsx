import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

// No next/font/google: it fetches at build time, and this tool has to build on
// a laptop with no network just as happily as on one with.
export const metadata: Metadata = {
  title: "jobagent",
  description: "Search, filter and label jobs",
};

const NAV = [
  { href: "/review", label: "Review" },
  { href: "/jobs", label: "Browse" },
  { href: "/searches", label: "Saved searches" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-border bg-surface">
          <nav className="mx-auto flex max-w-7xl items-center gap-6 px-5 py-3">
            <Link href="/review" className="font-semibold tracking-tight">
              jobagent
            </Link>
            <div className="flex gap-4 text-sm">
              {NAV.map((n) => (
                <Link key={n.href} href={n.href} className="text-muted hover:text-fg">
                  {n.label}
                </Link>
              ))}
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-7xl px-5 py-6">{children}</main>
      </body>
    </html>
  );
}
