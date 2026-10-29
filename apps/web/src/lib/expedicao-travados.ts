/**
 * EXPEDICAO-TRAVADOS-1 — a parte pura do popup de travados (testavel sem o
 * dialogo, que depende do roteador do Next).
 */
import { formatarDecimal } from "./expedicao-formato.ts";
import {
  FILTROS_PADRAO,
  construirQuery,
  type Canal,
  type LinhaFila,
} from "./expedicao-contract.ts";

/** Mais que isso vira "mostrando N de M": o popup e' para olhar, nao exportar. */
export const LIMITE_TRAVADOS = 200;

export interface AlvoTravados {
  /** Titulo curto: "Travados · ÁPICE". */
  titulo: string;
  marca: string | null;
  conta: string | null;
  travados: number;
  backlog: number;
  /**
   * A lista vem da fila PUBLICADA. Com a divisao vendedor x FBS na tela, o
   * cartao e' so' do vendedor: o popup diz que a lista inclui FBS.
   */
  incluiFbs?: boolean;
}

/** Por que o pipeline marcou o pedido como travado, em linguagem de gestor. */
export function motivoDoTravado(l: Pick<LinhaFila, "is_slow_vs_baseline" | "is_source_zombie">): string {
  if (l.is_source_zombie) return "Aberto há mais de 30 dias";
  if (l.is_slow_vs_baseline) return "Mais lento que o normal da conta";
  return "Travado";
}

/** "6,7% da fila" — so' com base positiva; sem base, nada. */
export function percentualDaFila(parte: number, base: number): string | null {
  if (!(base > 0)) return null;
  return `${formatarDecimal((parte / base) * 100, 1)}%`;
}

export function queryDosTravados(canal: Canal, marca: string | null, conta: string | null): string {
  return construirQuery({
    ...FILTROS_PADRAO,
    channel: canal,
    brands: marca ? [marca] : [],
    accounts: conta ? [conta] : [],
    situacao: ["stalled"],
    orderBy: "oldest",
    limit: LIMITE_TRAVADOS,
  });
}
