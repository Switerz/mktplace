import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    template: "%s · Torre de Controle",
    default: "Torre de Controle · Gobeaute",
  },
  description: "Monitoramento de marketplaces TikTok Shop e Mercado Livre",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body className="min-h-screen bg-[#f8f7ff]">{children}</body>
    </html>
  );
}
