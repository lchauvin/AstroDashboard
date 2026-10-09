import type { Metadata } from "next";
import { Barlow, Barlow_Condensed } from "next/font/google";

import { Header } from "@/components/Header";
import "@/styles/global.css";

const barlow = Barlow({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-barlow",
  display: "swap",
});

const barlowCondensed = Barlow_Condensed({
  subsets: ["latin"],
  weight: ["400", "600"],
  variable: "--font-barlow-condensed",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "AstroDashboard", template: "%s · AstroDashboard" },
  description: "Library, diagnostics, live monitoring and planning for the observatory.",
};

// Applies the stored theme before first paint so the page never flashes light at night.
const themeScript = `try{var t=localStorage.getItem("astrodash-theme");if(t==="dark"||t==="night")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${barlow.variable} ${barlowCondensed.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <Header />
        {children}
      </body>
    </html>
  );
}
