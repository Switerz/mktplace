import type { Metadata } from "next";
import "./globals.css";
import AppShell from "@/components/shell/AppShell";
import ThemeProvider from "@/components/theme/ThemeProvider";
import { scriptDeTema } from "@/lib/theme";

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
    <html lang="pt-BR" suppressHydrationWarning>
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
