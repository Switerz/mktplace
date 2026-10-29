/**
 * Container de pagina do V2 (Gate V2-2, Task 2/2).
 *
 * Unifica largura, respiro e ritmo vertical das dez superficies que nao sao a
 * Gerencial. Os valores replicam a decisao ja tomada no §7 do
 * `docs/UI_REVAMP_V2_PLAN.md` e aplicada na Gerencial:
 *
 * - `max-w-[1440px]` no lugar de `max-w-7xl` (1280px), que apertava as tabelas
 *   largas em telas de 1440 e deixava trilha morta nas laterais;
 * - `px-4` em mobile/tablet e `px-6` a partir de `sm`;
 * - `gap-3` abaixo de `sm` e `gap-4` acima — o mesmo ritmo da Gerencial, no
 *   lugar do `gap-6` que espalhava os blocos e reduzia densidade util.
 *
 * A pagina continua sendo a fonte de verdade de tudo o mais: este componente
 * nao conhece filtros, dados nem estado.
 */
export default function PageContainer({ children }: { children: React.ReactNode }) {
  // UI-DENSIDADE-1 — largura total, borda de 16px e 12px entre blocos, como a
  // Torre do Atacado. O teto de 1440px deixava 137px vazios de cada lado num
  // monitor de 1920px.
  return (
    <div className="w-full px-4 py-4 flex flex-col gap-3">
      {children}
    </div>
  );
}
