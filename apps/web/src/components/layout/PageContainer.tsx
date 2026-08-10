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
  return (
    <div className="max-w-[1440px] mx-auto px-4 sm:px-6 py-6 flex flex-col gap-3 sm:gap-4">
      {children}
    </div>
  );
}
