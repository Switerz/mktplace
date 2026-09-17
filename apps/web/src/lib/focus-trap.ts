/**
 * Gate PMA-2C4B-H1 — decisao do focus trap, SEM DOM e SEM JSX.
 *
 * Separado de `KpiDrilldownDialog.tsx` pelo mesmo motivo que
 * `monitoramento-preco-contract.ts` foi separado de `api-client.ts`: o
 * type-stripping nativo do Node (usado por `node --test`) nao processa `.tsx`.
 * Num arquivo `.ts` puro, a regra vira teste no mesmo runner dos demais, sem
 * dependencia nova e sem precisar abrir um navegador.
 *
 * A regra em si nao mudou — existe desde o primeiro commit do dialogo
 * (`8150408`, 2026-07-24) e foi medida integra em Chromium. O que mudou e' que
 * agora ela e' verificavel.
 */

/** Mesmo seletor/padrao de focus trap do MobileDrawer (Gate U1) — reaproveita
 * a convencao ja validada em vez de inventar uma nova. */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Devolve o elemento que DEVE receber o foco, ou `null` quando o navegador
 * pode seguir com o comportamento nativo (mover para o proximo focavel dentro
 * do painel).
 *
 * `null` tambem quando nao ha nada focavel: prender o foco num painel vazio
 * deixaria o usuario de teclado sem saida.
 */
export function alvoDoFocusTrap<T>(params: {
  focusables: T[];
  active: T | null;
  /** O foco esta' dentro do painel? Falso quando o elemento focado foi
   *  desmontado e o foco caiu no `document.body`. */
  dentroDoPainel: boolean;
  shiftKey: boolean;
}): T | null {
  const { focusables, active, dentroDoPainel, shiftKey } = params;
  if (focusables.length === 0) return null;
  const primeiro = focusables[0];
  const ultimo = focusables[focusables.length - 1];
  if (shiftKey) {
    // No primeiro (ou com o foco perdido fora do painel), Shift+Tab da' a
    // volta para o ultimo.
    return active === primeiro || !dentroDoPainel ? ultimo : null;
  }
  // No ultimo (ou fora), Tab volta ao primeiro.
  return active === ultimo || !dentroDoPainel ? primeiro : null;
}
