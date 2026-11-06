/**
 * Top produtos por marca (Gerencial): tipos da API e as contas puras do
 * detalhe — agrupar a serie diaria em dia/semana/mes e a variacao.
 */
import { torreFetch } from "../torre-fetch.ts";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export type CanalTop = "tiktok" | "ml" | "shopee";

export interface ProdutoTop {
  posicao: number;
  canal: CanalTop;
  sku: string;
  produto: string;
  kit: boolean;
  pecas: number;
  receita: number;
  preco_medio: number | null;
  share_marca: number | null;
  pecas_ant: number | null;
  receita_ant: number | null;
}
export interface ExtraCanal {
  rotulo: string;
  valor: number;
  formato: "brl" | "num" | "vezes";
}
/** Faixa "por canal" da marca: TikTok traz a origem do GMV; ML, os ads; Shopee, so' a receita. */
export interface ResumoCanal {
  canal: CanalTop;
  receita: number;
  extras: ExtraCanal[];
  aviso_extras: string | null;
}
export interface MarcaTop {
  marca: string;
  receita_marca: number;
  receita_marca_ant: number | null;
  produtos: ProdutoTop[];
  canais: ResumoCanal[];
}
export interface TopProdutosResponse {
  inicio: string;
  fim: string;
  comparacao_inicio: string | null;
  comparacao_fim: string | null;
  canais: CanalTop[];
  marcas: MarcaTop[];
}
export interface PontoDiario {
  dia: string;
  pecas: number;
  receita: number;
}
export interface DetalheProduto {
  canal: CanalTop;
  marca: string;
  sku: string;
  produto: string;
  kit: boolean;
  serie: PontoDiario[];
  extras: ExtraCanal[];
  aviso_extras: string | null;
}

/** "05/11" ou "26/10–31/10": diz contra o que a variacao compara. */
export function rotuloPeriodo(inicio: string, fim: string): string {
  return inicio === fim ? ddmm(fim) : `${ddmm(inicio)}–${ddmm(fim)}`;
}

/**
 * Variacao so' quando os dois lados sao comparaveis: so' "hoje" (dia em
 * andamento) contra o dia anterior INTEIRO daria quedas falsas de 40-70%.
 */
export function comparacaoValida(r: Pick<TopProdutosResponse, "inicio" | "fim" | "comparacao_inicio">, hoje: string): boolean {
  return Boolean(r.comparacao_inicio) && !(r.inicio === hoje && r.fim === hoje);
}

async function pedir<T>(caminho: string, signal: AbortSignal): Promise<T> {
  const r = await torreFetch(`${API_URL}${caminho}`, { signal });
  if (!r.ok) throw new Error(String(r.status));
  return (await r.json()) as T;
}

export function buscarTopProdutos(q: URLSearchParams, signal: AbortSignal) {
  return pedir<TopProdutosResponse>(`/api/v1/gerencial/top-produtos?${q}`, signal);
}

export function buscarDetalhe(p: { canal: CanalTop; marca: string; sku: string; dateFrom: string; dateTo: string }, signal: AbortSignal) {
  const q = new URLSearchParams({ canal: p.canal, marca: p.marca, sku: p.sku, date_from: p.dateFrom, date_to: p.dateTo });
  return pedir<DetalheProduto>(`/api/v1/gerencial/top-produtos/detalhe?${q}`, signal);
}

/** Variacao em %; sem base (nula ou zero) nao inventa 100%. */
export function variacao(atual: number, base: number | null): number | null {
  return base ? ((atual - base) / base) * 100 : null;
}

export type Grao = "dia" | "semana" | "mes";
export interface Barra {
  chave: string;
  rotulo: string;
  pecas: number;
  receita: number;
}

const MES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const DIA_MS = 86_400_000;
const utc = (iso: string) => Date.parse(`${iso}T12:00:00Z`);
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const ddmm = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}`;

/**
 * Agrupa a serie ate `fim`: 30 dias, 12 semanas (blocos de 7 dias terminando
 * em `fim`) ou 12 meses de calendario. Dia sem venda vira barra zero, nunca
 * some — um buraco no eixo seria lido como falta de dado.
 */
export function agrupar(serie: PontoDiario[], fim: string, grao: Grao): Barra[] {
  const porDia = new Map(serie.map((p) => [p.dia, p]));
  const fimMs = utc(fim);
  const barras: Barra[] = [];
  if (grao === "mes") {
    const [a, m] = [Number(fim.slice(0, 4)), Number(fim.slice(5, 7))];
    for (let k = 11; k >= 0; k--) {
      const t = a * 12 + (m - 1) - k;
      const chave = `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
      barras.push({ chave, rotulo: `${MES[t % 12]}/${String(Math.floor(t / 12)).slice(2)}`, pecas: 0, receita: 0 });
    }
    for (const p of serie) {
      const b = barras.find((x) => x.chave === p.dia.slice(0, 7));
      if (b && p.dia <= fim) { b.pecas += p.pecas; b.receita += p.receita; }
    }
    return barras;
  }
  const passo = grao === "dia" ? 1 : 7;
  const n = grao === "dia" ? 30 : 12;
  for (let k = n - 1; k >= 0; k--) {
    const ultimo = fimMs - k * passo * DIA_MS;
    const b: Barra = { chave: iso(ultimo), rotulo: ddmm(iso(ultimo - (passo - 1) * DIA_MS)), pecas: 0, receita: 0 };
    for (let d = 0; d < passo; d++) {
      const p = porDia.get(iso(ultimo - d * DIA_MS));
      if (p) { b.pecas += p.pecas; b.receita += p.receita; }
    }
    barras.push(b);
  }
  return barras;
}
