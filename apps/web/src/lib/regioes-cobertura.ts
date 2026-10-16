// Cobertura regional POR CANAL (REGIONAL-SOURCE-RECOVERY-2). Modulo puro,
// mesmo padrao de regioes-format.ts / regioes-scope.ts.
//
// Problema: `refreshed_at` e' o instante da carga mais recente de QUALQUER
// canal. Com o ML carregado ontem e a Shopee congelada em 24/08/2026, a tela
// dizia "Atualizado em <ontem>" e mostrava a Shopee com 0 em set/out. Aqui a
// tela passa a ler a cobertura que a API declara por canal:
//   - um aviso nomeia o canal parado, a data ate onde ele vai e o motivo;
//   - a linha de escopo mostra "Dados completos até" (o menor `dados_ate`),
//     e nao a data do canal mais fresco;
//   - cada ponto da tendencia diz quais canais NAO estao nele.
// Ausencia de dado nunca e' apresentada como zero.
import type { CoberturaCanal } from "./api-client";

const NOMES: Record<string, string> = { tiktok: "TikTok Shop", ml: "Mercado Livre", shopee: "Shopee" };

export function nomeCanal(channel: string): string {
  return NOMES[channel] ?? channel;
}

/** "2026-08-24" -> "24/08/2026". Sem Date(): evita deslocamento de fuso. */
export function fmtDataBr(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : null;
}

function juntar(nomes: string[]): string {
  if (nomes.length <= 1) return nomes.join("");
  return `${nomes.slice(0, -1).join(", ")} e ${nomes[nomes.length - 1]}`;
}

export interface AvisoCobertura {
  severidade: "alerta" | "critico";
  titulo: string;
  mensagem: string;
  /** Motivo conhecido de cada canal parado, como veio da API. */
  detalhes: string[];
}

/** `null` quando todo canal com fonte regional esta atualizado. TikTok
 * (`sem_cobertura`) tem aviso proprio e nunca entra aqui. */
export function buildAvisoCobertura(cobertura: readonly CoberturaCanal[]): AvisoCobertura | null {
  const parados = cobertura.filter((c) => c.status === "defasada" || c.status === "sem_carga");
  if (parados.length === 0) return null;
  const emDia = cobertura.filter((c) => c.status === "atualizada");

  const titulo = parados.length === 1
    ? parados[0].status === "defasada"
      ? `${nomeCanal(parados[0].channel)} com dado regional só até ${fmtDataBr(parados[0].dados_ate)}`
      : `${nomeCanal(parados[0].channel)} sem dado regional carregado`
    : "Canais com dado regional desatualizado";

  const frases = parados.map((c) =>
    c.status === "defasada"
      ? `${nomeCanal(c.channel)} não entra nos números depois de ${fmtDataBr(c.dados_ate)}`
      : `${nomeCanal(c.channel)} não entra nos números`,
  );
  let mensagem =
    `${frases.join("; ")}. Totais, mapa, ranking e tendência mostram só o que foi carregado — ` +
    "ausência de dado regional não é venda zero.";
  if (emDia.length > 0) {
    const ultimos = emDia.map((c) => `${nomeCanal(c.channel)} até ${fmtDataBr(c.dados_ate)}`);
    mensagem += ` ${juntar(ultimos)} está em dia, mas isso não torna o total atualizado.`;
  }

  return {
    severidade: parados.some((c) => c.status === "sem_carga") ? "critico" : "alerta",
    titulo,
    mensagem,
    detalhes: parados.map((c) => c.motivo_detalhe).filter((d): d is string => !!d),
  };
}

/**
 * Linha temporal do cabecalho. Com canal parado, o que vale para o TOTAL e'
 * `dados_completos_ate`; o `refreshed_at` so' aparece qualificado como a
 * carga mais recente de algum canal — nunca sozinho como "Atualizado em".
 */
export function linhaAtualizacao(
  dadosCompletosAte: string | null,
  canaisDefasados: readonly string[],
  refreshedAtFmt: string | null,
): string | null {
  if (canaisDefasados.length === 0) return refreshedAtFmt ? `Atualizado em ${refreshedAtFmt}` : null;
  const completos = fmtDataBr(dadosCompletosAte);
  const base = completos ? `Dados completos até ${completos}` : "Dados incompletos no período";
  return refreshedAtFmt ? `${base} · carga mais recente (parcial) em ${refreshedAtFmt}` : base;
}

/** Marca de um ponto da tendencia que nao contem algum canal. */
export function marcaPontoAusente(canaisAusentes: readonly string[] | undefined): string | null {
  if (!canaisAusentes || canaisAusentes.length === 0) return null;
  return `sem ${juntar(canaisAusentes.map(nomeCanal))}`;
}
