import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import AppShell from "@/components/shell/AppShell";
import ThemeProvider from "@/components/theme/ThemeProvider";
import { scriptDeTema } from "@/lib/theme";

/**
 * UX-TORRE-B2B-1 — Inter, autohospedada: o next/font baixa o arquivo no BUILD
 * e serve do proprio dominio, entao nenhuma requisicao vai ao Google em
 * runtime. Escolhida pelos algarismos tabulares de verdade (`tnum`), que e' o
 * que mantem colunas de R$ alinhadas.
 */
const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: {
    template: "%s · Torre de Controle",
    default: "Torre de Controle · Gobeaute",
  },
  description: "Monitoramento de marketplaces TikTok Shop e Mercado Livre",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `suppressHydrationWarning` e' obrigatorio e NAO e' um remendo: o script
    // abaixo altera `class` e `style` de <html> antes da hidratacao, de
    // proposito. Sem isto o React reclamaria da divergencia que ele mesmo
    // precisa aceitar para nao haver flash de tema errado.
    <html lang="pt-BR" className={inter.variable} suppressHydrationWarning>
      <head>
        {/* Sincrono e no <head>: precisa rodar ANTES da primeira pintura. */}
        <script dangerouslySetInnerHTML={{ __html: scriptDeTema() }} />
      </head>
      <body className="min-h-screen bg-canvas text-ink antialiased">
        <ThemeProvider>
          <AppShell>{children}</AppShell>
        </ThemeProvider>
      </body>
    </html>
  );
}
