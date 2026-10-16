/**
 * Gate EXPEDICAO-UX-JANELA-CUSTOMIZAVEL-1 — "ultimos N dias" nas duas
 * superficies da Expedicao, alem dos atalhos fixos.
 *
 * UNIDADE DO ESTADO: DIAS, SEMPRE
 * -------------------------------
 * O TikTok fala `days` com a API; a fila (Shopee/ML) fala `window_hours`. Os
 * tres atalhos da fila (24h, 72h, 7d) ja' sao dias inteiros, entao a tela
 * guarda dias nas duas superficies e a fila so' converte para horas ao montar
 * a requisicao (`queryDaTendenciaEmDias`). Guardar horas na URL de uma e dias
 * na da outra faria `?dias=7` significar coisas diferentes conforme o canal.
 *
 * VALIDAR ANTES DA REQUISICAO
 * ---------------------------
 * Fora do intervalo a API devolve 422, e a tela mostra "Tendência indisponível
 * agora" — o operador leria uma falha da fonte onde houve so' um erro de
 * digitacao. Por isso entrada invalida NUNCA vira requisicao: nem arredondada,
 * nem grampeada no limite. Ela vira mensagem, e a janela anterior continua.
 */
import {
  JANELAS_TENDENCIA,
  JANELA_INICIAL_HORAS,
  JANELA_MAX_HORAS,
  queryDaTendencia,
  type Canal,
} from "./expedicao-contract.ts";
import { JANELAS, JANELA_INICIAL_DIAS, JANELA_MAX_DIAS } from "./expedicao-tiktok-contract.ts";

export type Superficie = "fila" | "tiktok";

/** Mesmo nome nas duas superficies: o que muda e' o limite, nao o parametro. */
export const PARAM_DIAS = "dias";

export const OPCAO_PERSONALIZADA = "personalizado";
export const ROTULO_PERSONALIZADA = "Personalizado";

export interface Atalho {
  dias: number;
  rotulo: string;
}

export interface RegraJanela {
  max: number;
  padrao: number;
  atalhos: readonly Atalho[];
}

/**
 * Limites DERIVADOS dos contratos que ja' espelham a API (336 h e 90 dias),
 * nao redigitados: se a API mudar o teto, um so' numero muda.
 */
export const REGRA_JANELA: Record<Superficie, RegraJanela> = {
  fila: {
    max: JANELA_MAX_HORAS / 24,
    padrao: JANELA_INICIAL_HORAS / 24,
    atalhos: JANELAS_TENDENCIA.map((j) => ({ dias: j.horas / 24, rotulo: j.rotulo })),
  },
  tiktok: {
    max: JANELA_MAX_DIAS,
    padrao: JANELA_INICIAL_DIAS,
    atalhos: JANELAS.map((j) => ({ dias: j.dias, rotulo: j.rotulo })),
  },
};

export type ResultadoDias = { ok: true; dias: number } | { ok: false; erro: string };

/**
 * Texto digitado (ou lido da URL) -> dias. Estrito: so' algarismos ASCII.
 *
 * `Number("1.5")`, `Number("1e1")` e `Number(" 7 ")` todos "funcionam" — e
 * mandariam 1,5 dia, 10 dias ou um valor que o operador nao escreveu. Cada
 * forma invalida tem mensagem propria porque "valor invalido" nao diz o que
 * corrigir.
 */
export function validarDias(texto: string, max: number): ResultadoDias {
  const t = texto.trim();
  if (t === "") return { ok: false, erro: "Informe quantos dias." };
  if (/^-/.test(t)) return { ok: false, erro: "Use um número positivo de dias." };
  if (/^\d*[.,]\d*$/.test(t) && /\d/.test(t)) {
    return { ok: false, erro: "Use um número inteiro de dias, sem casas decimais." };
  }
  if (!/^\d+$/.test(t)) return { ok: false, erro: "Use só algarismos." };
  const dias = Number(t);
  if (dias === 0) return { ok: false, erro: "A janela precisa ter pelo menos 1 dia." };
  if (dias > max) return { ok: false, erro: `O máximo neste canal é ${max} dias.` };
  return { ok: true, dias };
}

/**
 * `?dias=` da URL. Ausente -> padrao. Invalido -> padrao E `descartado`, para
 * a tela avisar em vez de fingir que o link pediu a janela padrao.
 */
export function lerDiasDaUrl(
  busca: string,
  regra: RegraJanela,
): { dias: number; descartado: boolean } {
  const bruto = new URLSearchParams(busca).get(PARAM_DIAS);
  if (bruto === null) return { dias: regra.padrao, descartado: false };
  const r = validarDias(bruto, regra.max);
  return r.ok ? { dias: r.dias, descartado: false } : { dias: regra.padrao, descartado: true };
}

/**
 * Escreve a janela na URL. A janela padrao SAI da URL, como o canal padrao ja'
 * sai: o endereco historico da tela continua sendo `/expedicao`.
 */
export function urlComDias(href: string, dias: number, regra: RegraJanela): string {
  const url = new URL(href);
  if (dias === regra.padrao) url.searchParams.delete(PARAM_DIAS);
  else url.searchParams.set(PARAM_DIAS, String(dias));
  return url.toString();
}

/** Trocar de superficie descarta a janela: 30 dias vale no TikTok e nao na fila. */
export function urlSemDias(href: string): string {
  const url = new URL(href);
  url.searchParams.delete(PARAM_DIAS);
  return url.toString();
}

export function ehAtalho(dias: number, regra: RegraJanela): boolean {
  return regra.atalhos.some((a) => a.dias === dias);
}

export function opcoesDoSeletor(regra: RegraJanela): { valor: string; texto: string }[] {
  return [
    ...regra.atalhos.map((a) => ({ valor: String(a.dias), texto: a.rotulo })),
    { valor: OPCAO_PERSONALIZADA, texto: ROTULO_PERSONALIZADA },
  ];
}

/**
 * Qual opcao do seletor fica acesa. Uma janela que nao e' atalho (vinda da URL
 * ou digitada) acende "Personalizado" — nunca deixa o grupo sem opcao marcada.
 */
export function valorDoSeletor(
  dias: number,
  regra: RegraJanela,
  personalizadoAberto: boolean,
): string {
  if (personalizadoAberto || !ehAtalho(dias, regra)) return OPCAO_PERSONALIZADA;
  return String(dias);
}

/** O UNICO ponto onde dias viram horas na fila. */
export function queryDaTendenciaEmDias(dias: number, canal: Canal): string {
  return queryDaTendencia(dias * 24, canal);
}

/**
 * Descreve a janela que a API DEVOLVEU (`window_hours` da resposta), nao a que
 * a tela pediu: se as duas divergissem, o que vale e' o que foi consultado.
 */
export function descreverJanelaEmHoras(horas: number | null | undefined): string | null {
  if (typeof horas !== "number" || !Number.isFinite(horas) || horas <= 0) return null;
  if (horas % 24 === 0) {
    const d = horas / 24;
    return `últimos ${d} ${d === 1 ? "dia" : "dias"} (${horas} h)`;
  }
  return `últimas ${horas} h`;
}
