import type { Metadata } from "next";
import { Be_Vietnam_Pro } from "next/font/google";

import { SiteHeader } from "@/components/SiteHeader";

import "./globals.css";
import { Providers } from "./providers";

// Designed for Vietnamese: diacritics in scraped titles render correctly.
const beVietnam = Be_Vietnam_Pro({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-be-vietnam",
  display: "swap",
});

export const metadata: Metadata = {
  title: "StreamFeed",
  description: "IT job posts from ITviec, LinkedIn and Facebook groups in one feed.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={beVietnam.variable}>
      <body className="min-h-dvh">
        <Providers>
          <SiteHeader />
          {children}
        </Providers>
      </body>
    </html>
  );
}
