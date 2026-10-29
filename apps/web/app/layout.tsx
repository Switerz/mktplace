import type { Metadata } from "next";
import { DM_Sans, Inter } from "next/font/google";
import "./globals.css";
import AppShell from "@/components/shell/AppShell";
import ThemeProvider from "@/components/theme/ThemeProvider";
import { scriptDeTema } from "@/lib/theme";

/**
 * VISUAL-PALETA-1 — DM Sans (antes Inter), a fonte da Torre do Atacado: mais
 * arredondada. Autohospedadas: o next/font baixa os arquivos no BUILD e serve
 * do proprio dominio, entao nenhuma requisicao vai ao Google em runtime.
 *
 * MEDIDO: a DM Sans do Google NAO tem algarismos tabulares (`tnum`) — com
 * `tabular-nums`, "1111111" mede 52px e "8888888" 102px a 24px. Por isso a
 * Inter fica so' DENTRO DE TABELAS (`--font-num`, ver globals.css), onde
 * digito de largura variavel desmancha a coluna de R$. O resto e' DM Sans.
 */
const dmSans = DM_Sans({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const inter = Inter({ subsets: ["latin"], variable: "--font-num", display: "swap" });

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
    <html lang="pt-BR" className={`${dmSans.variable} ${inter.variable}`} suppressHydrationWarning>
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
