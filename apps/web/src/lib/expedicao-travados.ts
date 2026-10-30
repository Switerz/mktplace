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

/**
 * Etapa do pedido (T-0015) — o MESMO dominio e rotulo da API
 * (`ETAPAS_FILA` em `expedicao_service.py`), na ordem do fluxo.
 */
export const ETAPAS_DO_PEDIDO: readonly (readonly [string, string])[] = [
  ["a_faturar", "A faturar"],
  ["a_imprimir", "A imprimir"],
  ["em_separacao", "Em separação"],
  ["a_preparar", "A preparar"],
  ["aguardando_coleta", "Aguardando coleta"],
  ["entregue_transportadora", "Entregue à transportadora"],
  ["sinalizado_ml", "Sinalizado pelo Mercado Livre"],
  ["outro", "Outro status da fonte"],
  ["nao_publicada", "Etapa ainda não publicada"],
];
const ROTULO_ETAPA = new Map(ETAPAS_DO_PEDIDO);
const ORDEM_ETAPA = new Map(ETAPAS_DO_PEDIDO.map(([k], i) => [k, i]));

/** Rotulo da etapa; valor desconhecido aparece cru (nunca some). */
export function rotuloDaEtapa(etapa: string | null | undefined): string {
  if (!etapa) return "—";
  return ROTULO_ETAPA.get(etapa) ?? etapa;
}

/** A etapa REAL foi publicada para alguma linha (nao so' o default da 033). */
export function temEtapaPublicada(linhas: Pick<LinhaFila, "etapa">[]): boolean {
  return linhas.some((l) => l.etapa && l.etapa !== "nao_publicada");
}

/** Contagem por etapa das linhas, na ordem do fluxo: "onde estao travados". */
export function travadosPorEtapa(linhas: Pick<LinhaFila, "etapa">[]): { etapa: string; rotulo: string; n: number }[] {
  const n = new Map<string, number>();
  for (const l of linhas) {
    const k = l.etapa || "nao_publicada";
    n.set(k, (n.get(k) ?? 0) + 1);
  }
  return [...n.entries()]
    .sort((a, b) => (ORDEM_ETAPA.get(a[0]) ?? 99) - (ORDEM_ETAPA.get(b[0]) ?? 99))
    .map(([etapa, qtd]) => ({ etapa, rotulo: rotuloDaEtapa(etapa), n: qtd }));
}

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
