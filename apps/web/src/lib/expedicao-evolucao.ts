/**
 * Gate EXPEDICAO-UX-2 — evolucao do backlog por MARCA, em small multiples.
 *
 * O grafico anterior desenhava todas as contas num eixo so' (0-600): as contas
 * menores viravam linhas planas, a legenda mostrava `seller_id` do Mercado
 * Livre e o seletor dizia "72h" sobre tres horas de historico. Aqui cada conta
 * vira um cartao com a propria escala, titulado pela MARCA.
 *
 * DE ONDE VEM A MARCA
 * -------------------
 * Do proprio ponto da serie: `GET /expedicao/trend` devolve `brand` em cada
 * ponto, ao lado de `shop_account` — o mesmo mapeamento que a tabela "Risco por
 * conta" usa. Nenhuma relacao conta -> marca e' escrita no frontend; so' o
 * ROTULO de exibicao da marca vem do mapa canonico (`lib/filters/brands.ts`).
 *
 * UMA CONTA, UM CARTAO — NUNCA SOMA
 * ---------------------------------
 * Duas contas da mesma marca NAO sao somadas: cada uma segue em cartao proprio,
 * com o nome da marca e um apelido ("conta 1 de 2", na ordem do identificador).
 * O identificador aparece so' no tooltip e na tabela "Ver dados". Somar
 * esconderia qual conta parou, e o mesmo pedido pode estar no backlog de horas
 * seguidas — soma de pontos de tempo diferentes nao e' grandeza nenhuma.
 *
 * HONESTIDADE DO PERIODO
 * ----------------------
 * Nada e' preenchido: hora sem ponto e' lacuna (a linha se interrompe), e antes
 * do primeiro ponto nao existe linha. A cobertura real (`from_hour` ->
 * `to_hour`, da resposta) e' dita por escrito quando e' menor que a janela.
 *
 * As unicas contas feitas aqui sao a diferenca entre o ultimo e o primeiro
 * ponto da MESMA conta (absoluta e percentual) e a escala de cada cartao.
 */
import type { PontoTendencia, RespostaTendencia } from "./expedicao-contract.ts";
import { brandLabel } from "./filters/brands.ts";

const HORA_MS = 3600e3;

/**
 * Folga, em horas, entre a cobertura e a janela pedida antes de chamar a
 * cobertura de PARCIAL. A DAG publica a cada 2 h e a serie e' por hora cheia:
 * uma janela de 24 h bem servida termina com ~22 h entre o primeiro e o ultimo
 * ponto. Menos que (janela - folga) significa que o historico comecou DENTRO
 * da janela.
 */
export const FOLGA_COBERTURA_HORAS = 3;

export const ROTULO_SEM_MARCA = "Marca não informada";

export type EscalaEvolucao = "absoluto" | "variacao";
export const ESCALAS: readonly { valor: EscalaEvolucao; texto: string }[] = [
  { valor: "absoluto", texto: "Volume absoluto" },
  { valor: "variacao", texto: "Variação %" },
];

export interface PontoMarca {
  /** Instante do ponto em ms (eixo de tempo real, nao categorico). */
  t: number;
  hora: string;
  backlog: number;
  over48h: number;
  stalled: number;
}

export type EstadoSerie = "insuficiente" | "sem_variacao" | "variou";

export interface SerieMarca {
  chave: string;
  conta: string;
  /** Slug da marca como veio da API; `null` quando ausente ou vazio. */
  marca: string | null;
  titulo: string;
  /** "conta 1 de 2" quando a marca tem mais de uma conta; senao `null`. */
  apelido: string | null;
  pontos: PontoMarca[];
  /** Trechos continuos: a linha so' liga pontos sem hora faltando no meio. */
  trechos: PontoMarca[][];
  /** Horas em que OUTRA conta do canal tem ponto e esta nao. */
  lacunas: number;
  estado: EstadoSerie;
  atual: number;
  variacaoAbs: number | null;
  /** `null` com um ponto so' ou com o primeiro ponto em zero. */
  variacaoPct: number | null;
  primeiro: string;
  ultimo: string;
}

export interface CoberturaEvolucao {
  janelaHoras: number | null;
  inicio: string | null;
  fim: string | null;
  horasCobertas: number | null;
  /** O historico comecou dentro da janela pedida. */
  parcial: boolean;
}

function marcaDe(p: PontoTendencia): string | null {
  const b = typeof p.brand === "string" ? p.brand.trim() : "";
  return b ? b : null;
}

function tituloDaMarca(marca: string | null): string {
  return marca ? brandLabel(marca) : ROTULO_SEM_MARCA;
}

/** Quebra a serie onde o canal tem uma hora que esta conta nao tem. */
function trechosDe(pontos: PontoMarca[], horasDoCanal: number[]): PontoMarca[][] {
  const minhas = new Set(pontos.map((p) => p.t));
  const trechos: PontoMarca[][] = [];
  let atual: PontoMarca[] = [];
  let i = 0;
  for (const h of horasDoCanal) {
    if (h < (pontos[0]?.t ?? Infinity) || h > (pontos.at(-1)?.t ?? -Infinity)) continue;
    if (minhas.has(h)) {
      atual.push(pontos[i++]);
    } else if (atual.length) {
      trechos.push(atual);
      atual = [];
    }
  }
  if (atual.length) trechos.push(atual);
  return trechos;
}

/**
 * Pontos da resposta -> uma serie por CONTA, titulada pela marca.
 * Ordem: marca (rotulo), depois conta. Contas sem marca vao para o fim.
 */
export function montarEvolucaoPorMarca(pontos: PontoTendencia[]): SerieMarca[] {
  const horasDoCanal = [...new Set(pontos.map((p) => Date.parse(p.snapshot_hour)))]
    .filter((t) => Number.isFinite(t))
    .sort((a, b) => a - b);

  const porConta = new Map<string, { marca: string | null; pontos: PontoMarca[] }>();
  for (const p of pontos) {
    const t = Date.parse(p.snapshot_hour);
    if (!Number.isFinite(t)) continue;
    const s = porConta.get(p.shop_account) ?? { marca: marcaDe(p), pontos: [] };
    s.pontos.push({
      t,
      hora: p.snapshot_hour,
      backlog: p.backlog_count,
      over48h: p.over_48h_count,
      stalled: p.stalled_count,
    });
    porConta.set(p.shop_account, s);
  }

  // Apelido so' quando a MESMA marca tem mais de uma conta.
  const contasPorMarca = new Map<string, string[]>();
  for (const [conta, s] of porConta) {
    const k = s.marca ?? "";
    contasPorMarca.set(k, [...(contasPorMarca.get(k) ?? []), conta]);
  }
  for (const lista of contasPorMarca.values()) lista.sort((a, b) => a.localeCompare(b));

  const series: SerieMarca[] = [];
  for (const [conta, s] of porConta) {
    const ord = [...s.pontos].sort((a, b) => a.t - b.t);
    const irmas = contasPorMarca.get(s.marca ?? "") ?? [conta];
    const primeiro = ord[0];
    const ultimo = ord[ord.length - 1];
    const variou = ord.some((p) => p.backlog !== primeiro.backlog);
    const estado: EstadoSerie = ord.length < 2 ? "insuficiente" : variou ? "variou" : "sem_variacao";
    const variacaoAbs = ord.length < 2 ? null : ultimo.backlog - primeiro.backlog;
    series.push({
      chave: conta,
      conta,
      marca: s.marca,
      titulo: tituloDaMarca(s.marca),
      apelido: irmas.length > 1 ? `conta ${irmas.indexOf(conta) + 1} de ${irmas.length}` : null,
      pontos: ord,
      trechos: trechosDe(ord, horasDoCanal),
      lacunas: horasDoCanal.filter((h) => h > primeiro.t && h < ultimo.t && !ord.some((p) => p.t === h)).length,
      estado,
      atual: ultimo.backlog,
      variacaoAbs,
      variacaoPct:
        variacaoAbs === null || primeiro.backlog === 0 ? null : variacaoAbs / primeiro.backlog,
      primeiro: primeiro.hora,
      ultimo: ultimo.hora,
    });
  }
  return series.sort((a, b) => {
    if ((a.marca === null) !== (b.marca === null)) return a.marca === null ? 1 : -1;
    return a.titulo.localeCompare(b.titulo, "pt-BR") || a.conta.localeCompare(b.conta);
  });
}

/** Cobertura REAL da resposta frente a janela pedida. */
export function coberturaDaEvolucao(r: RespostaTendencia | null): CoberturaEvolucao {
  const janela = r && Number.isFinite(r.window_hours) && r.window_hours > 0 ? r.window_hours : null;
  const ini = r?.from_hour ? Date.parse(r.from_hour) : NaN;
  const fim = r?.to_hour ? Date.parse(r.to_hour) : NaN;
  const horas = Number.isFinite(ini) && Number.isFinite(fim) ? Math.max(0, (fim - ini) / HORA_MS) : null;
  return {
    janelaHoras: janela,
    inicio: r?.from_hour ?? null,
    fim: r?.to_hour ?? null,
    horasCobertas: horas,
    parcial: janela !== null && horas !== null && horas < janela - FOLGA_COBERTURA_HORAS,
  };
}

/** Data e hora de Brasilia, sempre — o servidor e o navegador podem estar em outro fuso. */
export function formatarHoraBrt(iso: string, comAno = true): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const partes = new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    ...(comAno ? { year: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
  return partes.replace(",", "");
}

function horasTexto(h: number): string {
  const n = Math.round(h * 10) / 10;
  return `${String(n).replace(".", ",")} h`;
}

/**
 * As frases de cobertura. `parcial` so' existe quando o historico comecou
 * dentro da janela: a tela diz quanto do periodo pedido tem dado, sem
 * apresentar a janela como preenchida.
 */
export function descreverCobertura(c: CoberturaEvolucao): { desde: string | null; exibindo: string | null } {
  if (!c.inicio || !c.fim || c.horasCobertas === null) return { desde: null, exibindo: null };
  if (!c.parcial || c.janelaHoras === null) return { desde: null, exibindo: null };
  const desde = `Histórico disponível desde ${formatarHoraBrt(c.inicio)}.`;
  const exibindo =
    c.horasCobertas === 0
      ? `Exibindo um único horário com dados das ${c.janelaHoras} h selecionadas.`
      : `Exibindo ${horasTexto(c.horasCobertas)} disponíveis das ${c.janelaHoras} h selecionadas.`;
  return { desde, exibindo };
}

export function formatarVariacao(s: SerieMarca): string | null {
  if (s.variacaoAbs === null) return null;
  const abs = s.variacaoAbs;
  const sinal = abs > 0 ? "+" : abs < 0 ? "−" : "";
  const absTxt = `${sinal}${Math.abs(abs).toLocaleString("pt-BR")}`;
  if (s.variacaoPct === null) return absTxt;
  const pct = s.variacaoPct * 100;
  const pctTxt = `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct).toFixed(1).replace(".", ",")}%`;
  return `${absTxt} (${pctTxt})`;
}

// ---------------------------------------------------------------------------
// Escala dos minigraficos
// ---------------------------------------------------------------------------

/** Valor plotado no modo escolhido; `null` quando o modo nao se aplica. */
export function valorPlotado(s: SerieMarca, p: PontoMarca, escala: EscalaEvolucao): number | null {
  if (escala === "absoluto") return p.backlog;
  const base = s.pontos[0]?.backlog ?? 0;
  if (base === 0) return null;
  return ((p.backlog - base) / base) * 100;
}

/**
 * Dominio vertical.
 *
 * - `absoluto`: cada cartao com a PROPRIA escala (min..max da conta, com
 *   folga). E' isso que tira as contas pequenas da linha plana. A escala vai
 *   escrita no cartao, para ninguem ler altura como volume entre cartoes.
 * - `variacao`: um dominio COMUM a todos (inclui o zero): e' o modo de
 *   comparar marcas entre si.
 */
export function dominioVertical(
  series: SerieMarca[],
  escala: EscalaEvolucao,
  alvo: SerieMarca,
): [number, number] | null {
  const valores = (escala === "absoluto" ? [alvo] : series).flatMap((s) =>
    s.pontos.map((p) => valorPlotado(s, p, escala)).filter((v): v is number => v !== null),
  );
  if (!valores.length) return null;
  let min = Math.min(...valores);
  let max = Math.max(...valores);
  if (escala === "variacao") {
    min = Math.min(0, min);
    max = Math.max(0, max);
  }
  if (min === max) {
    const folga = Math.max(1, Math.abs(min) * 0.1);
    return [min - folga, max + folga];
  }
  const folga = (max - min) * 0.12;
  return [escala === "absoluto" ? Math.max(0, min - folga) : min - folga, max + folga];
}

/** Dominio horizontal comum: do primeiro ao ultimo ponto REAL do canal. */
export function dominioHorizontal(series: SerieMarca[]): [number, number] | null {
  const ts = series.flatMap((s) => s.pontos.map((p) => p.t));
  if (!ts.length) return null;
  return [Math.min(...ts), Math.max(...ts)];
}
