import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono, Playfair_Display } from "next/font/google";
import "./globals.css";

/**
 * Self-hosted fonts via next/font/google — works with `output: "export"`
 * because the font files are downloaded at build time and served statically
 * (Capacitor has no network guarantee at first paint, port-map §4).
 * Each font exposes a CSS variable consumed by the Tailwind font tokens.
 */
const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-inter",
  display: "swap",
});

const playfairDisplay = Playfair_Display({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  style: ["normal", "italic"],
  variable: "--font-playfair",
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "blip — audio news",
  description: "30 stories. 30 minutes. Caught up.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  // Reason: viewport-fit=cover lets content extend under the Dynamic Island /
  // home indicator so env(safe-area-inset-*) reports real insets (port-map §6).
  viewportFit: "cover",
};

/**
 * Root layout: wires fonts + global styles and sets the app base surface
 * (near-black canvas, white text, Inter chrome) on <body>.
 */
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${playfairDisplay.variable} ${jetBrainsMono.variable}`}>
      <body className="bg-background text-text-primary font-sans">{children}</body>
    </html>
  );
}
