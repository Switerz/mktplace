// Cobertura regional POR CANAL (REGIONAL-SOURCE-RECOVERY-2/3). Modulo puro,
// mesmo padrao de regioes-format.ts / regioes-scope.ts.
//
// Problema: `refreshed_at` e' o instante da carga mais recente de QUALQUER
// canal. Com o ML carregado ontem e a Shopee congelada em 24/08/2026, a tela
// dizia "Atualizado em <ontem>" e mostrava a Shopee com 0 em set/out. Aqui a
// tela passa a ler a cobertura que a API declara por canal:
//   - um aviso nomeia o canal atrasado/parado, a data ate onde ele vai e o motivo;
//   - a linha de escopo mostra "Dados completos até" (o menor `dados_ate`),
//     e nao a data do canal mais fresco;
//   - cada ponto da tendencia diz quais canais NAO estao nele.
// Ausencia de dado nunca e' apresentada como zero.
//
// Compatibilidade (RECOVERY-3): o front sobe na Vercel antes da API no Render.
// Nesse intervalo a API antiga NAO manda os campos de cobertura — tudo aqui
// aceita `undefined` e cai no comportamento anterior ("Atualizado em"), sem
// quebrar. Status desconhecido (API mais nova que o front) conta como atraso:
// na duvida, nunca chamar o total de atualizado.
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

type Grupo = "ok" | "leve" | "grave" | "estrutural";

function grupo(status: string): Grupo {
  if (status === "atualizada") return "ok";
  if (status === "sem_cobertura") return "estrutural";
  if (status === "atraso_leve") return "leve";
  return "grave"; // defasada, sem_carga e qualquer status desconhecido
}

/** O que a tela precisa do payload, ja normalizado para API nova OU antiga. */
export interface CoberturaNormalizada {
  /** `false` = API antiga (sem os campos): a tela mantem o comportamento anterior. */
  contratoPresente: boolean;
  canais: CoberturaCanal[];
  /** Canais que impedem chamar o total de atualizado (inclui D-2). */
  canaisComAtraso: string[];
  dadosCompletosAte: string | null;
}

export interface RespostaComCobertura {
  cobertura_por_canal?: CoberturaCanal[] | null;
  canais_defasados?: string[] | null;
  canais_com_atraso?: string[] | null;
  dados_completos_ate?: string | null;
}

export function normalizarCobertura(resp: RespostaComCobertura | null | undefined): CoberturaNormalizada {
  const canais = Array.isArray(resp?.cobertura_por_canal) ? resp.cobertura_por_canal : null;
  if (!canais) return { contratoPresente: false, canais: [], canaisComAtraso: [], dadosCompletosAte: null };
  // Derivado da lista por canal (fonte unica), nao de `canais_com_atraso`:
  // assim funciona tambem com a API da RECOVERY-2, que nao tinha esse campo.
  const canaisComAtraso = canais
    .filter((c) => grupo(c.status) === "leve" || grupo(c.status) === "grave")
    .map((c) => c.channel);
  return {
    contratoPresente: true,
    canais,
    canaisComAtraso,
    dadosCompletosAte: resp?.dados_completos_ate ?? null,
  };
}

export interface AvisoCobertura {
  severidade: "leve" | "alerta" | "critico";
  titulo: string;
  mensagem: string;
  /** Motivo de cada canal atrasado/parado, como veio da API. */
  detalhes: string[];
}

/** `null` quando todo canal com fonte regional esta atualizado (ou a API e'
 * antiga). TikTok (`sem_cobertura`) tem aviso proprio e nunca entra aqui. */
export function buildAvisoCobertura(cobertura: readonly CoberturaCanal[]): AvisoCobertura | null {
  const graves = cobertura.filter((c) => grupo(c.status) === "grave");
  const leves = cobertura.filter((c) => grupo(c.status) === "leve");
  if (graves.length === 0 && leves.length === 0) return null;
  const emDia = cobertura.filter((c) => grupo(c.status) === "ok");
  const afetados = [...graves, ...leves];

  let titulo: string;
  if (afetados.length > 1) {
    titulo = graves.length > 0 ? "Canais com dado regional desatualizado" : "Canais com 1 dia de atraso";
  } else {
    const c = afetados[0];
    const nome = nomeCanal(c.channel);
    if (c.status === "sem_carga" || !c.dados_ate) titulo = `${nome} sem dado regional carregado`;
    else if (grupo(c.status) === "leve") titulo = `${nome} com 1 dia de atraso — dados até ${fmtDataBr(c.dados_ate)}`;
    else titulo = `${nome} com dado regional só até ${fmtDataBr(c.dados_ate)}`;
  }

  const frases = afetados.map((c) =>
    c.dados_ate
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

  const severidade: AvisoCobertura["severidade"] = graves.some((c) => c.status === "sem_carga")
    ? "critico"
    : graves.length > 0 ? "alerta" : "leve";

  return {
    severidade,
    titulo,
    mensagem,
    detalhes: afetados.map((c) => c.motivo_detalhe).filter((d): d is string => !!d),
  };
}

/**
 * Linha temporal do cabecalho. Com canal atrasado, o que vale para o TOTAL e'
 * `dados_completos_ate`; o `refreshed_at` so' aparece qualificado como a
 * carga mais recente de algum canal — nunca sozinho como "Atualizado em".
 * Sem o contrato (API antiga), mantem "Atualizado em", como antes.
 */
export function linhaAtualizacao(
  dadosCompletosAte: string | null,
  canaisComAtraso: readonly string[],
  refreshedAtFmt: string | null,
): string | null {
  if (canaisComAtraso.length === 0) return refreshedAtFmt ? `Atualizado em ${refreshedAtFmt}` : null;
  const completos = fmtDataBr(dadosCompletosAte);
  const base = completos ? `Dados completos até ${completos}` : "Dados incompletos no período";
  return refreshedAtFmt ? `${base} · carga mais recente (parcial) em ${refreshedAtFmt}` : base;
}

/** Marca de um ponto da tendencia que nao contem algum canal. Aceita o
 * campo ausente (API antiga). */
export function marcaPontoAusente(canaisAusentes: readonly string[] | null | undefined): string | null {
  if (!canaisAusentes || canaisAusentes.length === 0) return null;
  return `sem ${juntar(canaisAusentes.map(nomeCanal))}`;
}
