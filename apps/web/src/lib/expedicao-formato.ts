/**
 * T-0010 — formato UNICO de numero e percentual da Expedicao (3 canais).
 *
 * Antes cada arquivo tinha o seu: `formatarTaxa` (TikTok, `toFixed` + troca de
 * ponto), `formatarTaxaLdr` (Shopee), `numero()` na fila e
 * `Intl.NumberFormat` solto no painel da LDR. Os valores eram os mesmos; o
 * formato divergia. Agora todos passam por aqui.
 *
 * Regras:
 *   - taxa chega como FRACAO (0,034 = 3,4%), como a API publica;
 *   - percentual sem base valida NUNCA vira "0%": vira "indisponível";
 *   - a taxa publicada pela API pode ser passada pronta (`taxa`): a tela
 *     formata, nao recalcula;
 *   - arredondamento do `Intl` (meia unidade para longe do zero), o mesmo do
 *     `toFixed` usado antes — nenhum valor exibido muda de digito.
 *
 * SEM IMPORTS: os contratos dos canais importam daqui.
 */

/** Medida ausente na fonte. Nunca "0". */
export const VALOR_AUSENTE = "—";
/** Percentual sem denominador valido. */
export const TEXTO_INDISPONIVEL = "indisponível";
/** Casas decimais padrao de percentual. */
export const CASAS_PADRAO = 1;
/** Sinal de menos tipografico (U+2212), so' para deltas. */
export const SINAL_MENOS = "−";

const INTEIRO = new Intl.NumberFormat("pt-BR");
const DECIMAIS = new Map<number, Intl.NumberFormat>();

function decimal(casas: number): Intl.NumberFormat {
  const c = Math.max(0, Math.min(6, Math.trunc(casas)));
  let f = DECIMAIS.get(c);
  if (!f) {
    f = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c });
    DECIMAIS.set(c, f);
  }
  return f;
}

export function ehNumero(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Inteiro pt-BR ("1.820"); ausente vira "—". */
export function formatarInteiro(v: number | null | undefined): string {
  return ehNumero(v) ? INTEIRO.format(v) : VALOR_AUSENTE;
}

export interface OpcoesPercentual {
  /** Casas decimais. Padrao: 1. */
  casas?: number;
}

/** Fracao -> "3,4%". Ausente vira "—" (nunca "0%"). */
export function formatarPercentual(
  taxa: number | null | undefined,
  opcoes: OpcoesPercentual = {},
): string {
  if (!ehNumero(taxa)) return VALOR_AUSENTE;
  // `+ 0` normaliza -0, que o Intl escreveria "-0".
  return `${decimal(opcoes.casas ?? CASAS_PADRAO).format(taxa * 100 + 0)}%`;
}

export interface OpcoesPercentualComBase extends OpcoesPercentual {
  /**
   * Taxa JA' publicada pela API. Quando vem, e' ela que aparece — a tela nao
   * divide de novo. `null` com base valida = taxa ausente na fonte.
   */
  taxa?: number | null;
}

/** "62 de 1.820". Sem base valida: "indisponível". */
export function formatarBase(
  numerador: number | null | undefined,
  base: number | null | undefined,
): string {
  if (!ehNumero(base) || base <= 0 || !ehNumero(numerador)) return TEXTO_INDISPONIVEL;
  return `${formatarInteiro(numerador)} de ${formatarInteiro(base)}`;
}

/**
 * "3,4% · 62 de 1.820". Base 0, negativa, `null` ou ausente -> "indisponível":
 * zero pedidos na base nao e' 0% de atraso, e' falta de denominador.
 */
export function formatarPercentualComBase(
  numerador: number | null | undefined,
  base: number | null | undefined,
  opcoes: OpcoesPercentualComBase = {},
): string {
  if (!ehNumero(base) || base <= 0 || !ehNumero(numerador)) return TEXTO_INDISPONIVEL;
  const taxa = opcoes.taxa !== undefined ? opcoes.taxa : numerador / base;
  if (!ehNumero(taxa)) return TEXTO_INDISPONIVEL;
  return `${percentualDeContagem(taxa, numerador, opcoes)} · ${formatarBase(numerador, base)}`;
}

/**
 * QA T-0010 (M2): contagem MAIOR que zero cujo percentual arredonda para
 * zero vira "<0,1%" (ou "<0,01%" com 2 casas). "0,0%" leria como nenhum.
 */
function percentualDeContagem(taxa: number, numerador: number, opcoes: OpcoesPercentual): string {
  const casas = opcoes.casas ?? CASAS_PADRAO;
  const texto = formatarPercentual(taxa, opcoes);
  if (numerador > 0 && taxa > 0 && /^[0,]+%$/.test(texto)) {
    return `<${decimal(casas).format(1 / 10 ** casas)}%`;
  }
  return texto;
}

/** Decimal pt-BR com casas fixas ("33,0"). Ausente vira "—". */
export function formatarDecimal(v: number | null | undefined, casas = 1): string {
  return ehNumero(v) ? decimal(casas).format(v + 0) : VALOR_AUSENTE;
}

/**
 * As duas partes de `formatarPercentualComBase` ("3,35%" e "199 de 5.940"),
 * para telas que mostram a base numa linha secundaria. `null` = sem base
 * valida ("indisponível").
 */
export function partesDoPercentualComBase(
  numerador: number | null | undefined,
  base: number | null | undefined,
  opcoes: OpcoesPercentualComBase = {},
): { percentual: string; base: string } | null {
  const texto = formatarPercentualComBase(numerador, base, opcoes);
  if (texto === TEXTO_INDISPONIVEL) return null;
  const [percentual, resto] = texto.split(" · ");
  return { percentual, base: resto };
}

/**
 * Diferenca entre duas taxas (fracoes) em pontos percentuais:
 * "+0,8 p.p.", "−1,2 p.p.", "0,0 p.p.". Falta um lado -> "indisponível".
 */
export function formatarDeltaPp(
  atual: number | null | undefined,
  anterior: number | null | undefined,
  opcoes: OpcoesPercentual = {},
): string {
  if (!ehNumero(atual) || !ehNumero(anterior)) return TEXTO_INDISPONIVEL;
  const pp = (atual - anterior) * 100;
  const texto = decimal(opcoes.casas ?? CASAS_PADRAO).format(Math.abs(pp));
  const zero = /^[0,]+$/.test(texto);
  const sinal = zero ? "" : pp > 0 ? "+" : SINAL_MENOS;
  return `${sinal}${texto} p.p.`;
}

// ---------------------------------------------------------------------------
// Datas e rotulos de janela/coorte
// ---------------------------------------------------------------------------

function partes(iso: string): [number, number, number] | null {
  const [a, m, d] = iso.slice(0, 10).split("-").map(Number);
  return a && m && d ? [a, m, d] : null;
}

/**
 * "AAAA-MM-DD" -> "dd/mm", pelas PARTES da data: `new Date("2026-09-07")` e'
 * meia-noite UTC e volta um dia em fuso negativo.
 */
export function formatarDiaMes(iso: string): string {
  const p = partes(iso);
  if (!p) return iso;
  return `${String(p[2]).padStart(2, "0")}/${String(p[1]).padStart(2, "0")}`;
}

/** "AAAA-MM-DD" -> "dd/mm/aaaa". */
export function formatarDataLonga(iso: string): string {
  const p = partes(iso);
  if (!p) return iso;
  return `${String(p[2]).padStart(2, "0")}/${String(p[1]).padStart(2, "0")}/${p[0]}`;
}

export type EixoJanela = "pagamento" | "vencimento" | "hora";

export const ROTULO_EIXO: Record<EixoJanela, string> = {
  pagamento: "por dia de pagamento",
  vencimento: "por dia de vencimento",
  hora: "por hora da fotografia",
};

/** "últimos 7 dias · por dia de pagamento". */
export function rotuloJanela(dias: number | null | undefined, eixo?: EixoJanela): string {
  if (!ehNumero(dias) || dias < 1) return "janela não informada";
  const n = Math.trunc(dias);
  const base = n === 1 ? "último dia" : `últimos ${formatarInteiro(n)} dias`;
  return eixo ? `${base} · ${ROTULO_EIXO[eixo]}` : base;
}

/** "pedidos pagos de 19/10 a 25/10" / "pedidos com vencimento de ...". */
export function rotuloCoorte(inicio: string, fim: string, eixo: "pagamento" | "vencimento"): string {
  const sujeito = eixo === "pagamento" ? "pedidos pagos" : "pedidos com vencimento";
  if (inicio === fim) return `${sujeito} em ${formatarDiaMes(inicio)}`;
  return `${sujeito} de ${formatarDiaMes(inicio)} a ${formatarDiaMes(fim)}`;
}
