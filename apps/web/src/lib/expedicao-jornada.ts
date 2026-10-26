/**
 * T-0010 — contrato da secao "Jornada" da Expedicao.
 *
 * Consome `GET /api/v1/expedicao/jornada` (contrato v1 + ajustes v1.1/v1.2,
 * `.artefacts/deliveries/expedicao-jornada/contrato-jornada-v1.md`).
 *
 * O que a tela garante:
 *   - payload fora do contrato e' REJEITADO pelo parser (nunca "consertado");
 *   - etapas sao exclusivas: com `etapas_fecham = true` a soma TEM de bater
 *     com a base, senao o payload e' invalido; com `false`, a tela avisa
 *     "parcial" e nao refaz a soma;
 *   - sobreposicoes sao SUBCONJUNTOS e nunca entram em soma nenhuma; com
 *     `count = null` a tela diz "indisponível", nunca 0;
 *   - toda contagem vem do servidor, sobre a base completa: nada aqui le a
 *     fila paginada;
 *   - a API nao manda percentual: a tela formata `count` sobre `base`.
 */
import {
  formatarInteiro,
  formatarPercentualComBase,
  rotuloCoorte,
  TEXTO_INDISPONIVEL,
} from "./expedicao-formato.ts";

// ---------------------------------------------------------------------------
// Tipos (espelham o contrato)
// ---------------------------------------------------------------------------
export const STATUS_JORNADA = ["ok", "partial", "unavailable", "disabled"] as const;
export type StatusJornada = (typeof STATUS_JORNADA)[number];

/** Canal na QUERY da rota (o contrato usa `tiktok`, nao `tiktokshop`). */
export type CanalJornada = "shopee" | "mercadolivre" | "tiktok";

export interface EtapaJornada {
  key: string;
  label: string | null;
  count: number;
}

export interface SobreposicaoJornada {
  key: string;
  /** `null` = a medida nao existe no canal (v1.1). Nunca vira 0. */
  count: number | null;
  reason: string | null;
  subconjunto_de: string | null;
}

export interface ForaDaCoorte {
  key: string;
  count: number | null;
  reason: string | null;
}

export interface FilaAbertaJornada {
  grain: "pedido" | "shipment";
  snapshot_at: string | null;
  freshness: string;
  base: number;
  etapas: EtapaJornada[];
  etapas_fecham: boolean;
  sobreposicoes: SobreposicaoJornada[];
  limitacoes: string[];
}

export interface CoorteJornada {
  eixo: "paid_date" | "deadline_date";
  evento: "coleta" | "despacho";
  janela: { inicio: string; fim: string; dias: number };
  base: number;
  etapas: EtapaJornada[];
  etapas_fecham: boolean;
  sobreposicoes: SobreposicaoJornada[];
  fora_da_coorte: ForaDaCoorte[];
  maturidade: { dias_maduros: number; dias_imaturos: number } | null;
  limitacoes: string[];
  /** v1.2 — `max(effective_at)` da janela. Opcional enquanto o backend publica. */
  publicado_em: string | null;
  /** v1.2 — mesmo limite das rotas irmas. Ausente = nao informado. */
  desatualizada: boolean | null;
}

export interface RespostaJornada {
  channel: CanalJornada;
  status: StatusJornada;
  reason: string | null;
  brands_applied: string[];
  generated_at: string | null;
  fila_aberta: FilaAbertaJornada | null;
  coorte: CoorteJornada | null;
  /** v1.1 — limitacoes das secoes `null` (ML sem coorte, TikTok sem fila). */
  limitacoes: string[];
}

export type ResultadoParser =
  | { ok: true; resposta: RespostaJornada }
  | { ok: false; erro: string };

// ---------------------------------------------------------------------------
// Parser defensivo
// ---------------------------------------------------------------------------
class Invalido extends Error {}

function falha(msg: string): never {
  throw new Invalido(msg);
}

type Obj = Record<string, unknown>;

function objeto(v: unknown, onde: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) falha(`${onde}: esperado objeto`);
  return v as Obj;
}

function contagem(v: unknown, onde: string): number {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) falha(`${onde}: contagem inválida`);
  return v;
}

function contagemOuNulo(v: unknown, onde: string): number | null {
  return v === null ? null : contagem(v, onde);
}

function texto(v: unknown, onde: string): string {
  if (typeof v !== "string" || !v) falha(`${onde}: texto inválido`);
  return v;
}

function textoOuNulo(v: unknown, onde: string): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v !== "string") falha(`${onde}: texto inválido`);
  return v;
}

function booleano(v: unknown, onde: string): boolean {
  if (typeof v !== "boolean") falha(`${onde}: booleano inválido`);
  return v;
}

function lista<T>(v: unknown, onde: string, item: (x: unknown, i: number) => T, opcional = false): T[] {
  if ((v === undefined || v === null) && opcional) return [];
  if (!Array.isArray(v)) falha(`${onde}: esperado lista`);
  return v.map(item);
}

const textos = (v: unknown, onde: string) =>
  lista(v, onde, (x, i) => texto(x, `${onde}[${i}]`), true);

function dataIso(v: unknown, onde: string): string {
  const t = texto(v, onde);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) falha(`${onde}: data inválida`);
  return t;
}

function etapas(v: unknown, onde: string): EtapaJornada[] {
  const lst = lista(v, onde, (x, i) => {
    const o = objeto(x, `${onde}[${i}]`);
    return {
      key: texto(o.key, `${onde}[${i}].key`),
      label: textoOuNulo(o.label, `${onde}[${i}].label`),
      count: contagem(o.count, `${onde}[${i}].count`),
    };
  });
  if (new Set(lst.map((e) => e.key)).size !== lst.length) falha(`${onde}: etapa repetida`);
  return lst;
}

function sobreposicoes(v: unknown, onde: string, base: number, ets: EtapaJornada[]): SobreposicaoJornada[] {
  return lista(
    v,
    onde,
    (x, i) => {
      const o = objeto(x, `${onde}[${i}]`);
      const s: SobreposicaoJornada = {
        key: texto(o.key, `${onde}[${i}].key`),
        count: contagemOuNulo(o.count, `${onde}[${i}].count`),
        reason: textoOuNulo(o.reason, `${onde}[${i}].reason`),
        subconjunto_de: textoOuNulo(o.subconjunto_de, `${onde}[${i}].subconjunto_de`),
      };
      // Subconjunto nao pode ser maior que o conjunto: um payload assim
      // somou alguma coisa que nao devia.
      if (s.count !== null && s.count > base) falha(`${onde}[${i}]: maior que a base`);
      if (s.subconjunto_de !== null) {
        const pai = ets.find((e) => e.key === s.subconjunto_de);
        if (!pai) falha(`${onde}[${i}]: subconjunto de etapa inexistente`);
        if (s.count !== null && s.count > pai.count) falha(`${onde}[${i}]: maior que a etapa`);
      }
      return s;
    },
    true,
  );
}

/** Etapas exclusivas: "fecham = true" que nao soma a base e' payload mentiroso. */
function conferirFechamento(ets: EtapaJornada[], base: number, fecham: boolean, onde: string): void {
  // Conferencia de CONSISTENCIA sobre agregados do servidor (nao e' a fila
  // paginada): a tela nao usa esta soma para nada alem de recusar o payload.
  const soma = ets.reduce((s, e) => s + e.count, 0);
  if (fecham && soma !== base) falha(`${onde}: etapas_fecham=true mas a soma não bate com a base`);
}

function filaAberta(v: unknown): FilaAbertaJornada | null {
  if (v === null || v === undefined) return null;
  const o = objeto(v, "fila_aberta");
  const grain = o.grain;
  if (grain !== "pedido" && grain !== "shipment") falha("fila_aberta.grain inválido");
  const base = contagem(o.base, "fila_aberta.base");
  const ets = etapas(o.etapas, "fila_aberta.etapas");
  const fecham = booleano(o.etapas_fecham, "fila_aberta.etapas_fecham");
  conferirFechamento(ets, base, fecham, "fila_aberta");
  return {
    grain,
    snapshot_at: textoOuNulo(o.snapshot_at, "fila_aberta.snapshot_at"),
    freshness: textoOuNulo(o.freshness, "fila_aberta.freshness") ?? "unknown",
    base,
    etapas: ets,
    etapas_fecham: fecham,
    sobreposicoes: sobreposicoes(o.sobreposicoes, "fila_aberta.sobreposicoes", base, ets),
    limitacoes: textos(o.limitacoes, "fila_aberta.limitacoes"),
  };
}

function coorte(v: unknown): CoorteJornada | null {
  if (v === null || v === undefined) return null;
  const o = objeto(v, "coorte");
  if (o.eixo !== "paid_date" && o.eixo !== "deadline_date") falha("coorte.eixo inválido");
  if (o.evento !== "coleta" && o.evento !== "despacho") falha("coorte.evento inválido");
  const j = objeto(o.janela, "coorte.janela");
  const janela = {
    inicio: dataIso(j.inicio, "coorte.janela.inicio"),
    fim: dataIso(j.fim, "coorte.janela.fim"),
    dias: contagem(j.dias, "coorte.janela.dias"),
  };
  if (janela.inicio > janela.fim) falha("coorte.janela: início depois do fim");
  const base = contagem(o.base, "coorte.base");
  const ets = etapas(o.etapas, "coorte.etapas");
  const fecham = booleano(o.etapas_fecham, "coorte.etapas_fecham");
  conferirFechamento(ets, base, fecham, "coorte");
  let maturidade: CoorteJornada["maturidade"] = null;
  if (o.maturidade !== null && o.maturidade !== undefined) {
    const m = objeto(o.maturidade, "coorte.maturidade");
    maturidade = {
      dias_maduros: contagem(m.dias_maduros, "coorte.maturidade.dias_maduros"),
      dias_imaturos: contagem(m.dias_imaturos, "coorte.maturidade.dias_imaturos"),
    };
  }
  const desat = o.desatualizada;
  if (desat !== undefined && desat !== null && typeof desat !== "boolean") falha("coorte.desatualizada inválido");
  return {
    eixo: o.eixo,
    evento: o.evento,
    janela,
    base,
    etapas: ets,
    etapas_fecham: fecham,
    sobreposicoes: sobreposicoes(o.sobreposicoes, "coorte.sobreposicoes", base, ets),
    fora_da_coorte: lista(
      o.fora_da_coorte,
      "coorte.fora_da_coorte",
      (x, i) => {
        const f = objeto(x, `coorte.fora_da_coorte[${i}]`);
        return {
          key: texto(f.key, `coorte.fora_da_coorte[${i}].key`),
          count: contagemOuNulo(f.count, `coorte.fora_da_coorte[${i}].count`),
          reason: textoOuNulo(f.reason, `coorte.fora_da_coorte[${i}].reason`),
        };
      },
      true,
    ),
    maturidade,
    limitacoes: textos(o.limitacoes, "coorte.limitacoes"),
    publicado_em: textoOuNulo(o.publicado_em, "coorte.publicado_em"),
    desatualizada: typeof desat === "boolean" ? desat : null,
  };
}

const CANAIS_RESPOSTA: Record<string, CanalJornada> = {
  shopee: "shopee",
  mercadolivre: "mercadolivre",
  tiktok: "tiktok",
  tiktokshop: "tiktok",
};

/**
 * Valida o payload. Qualquer violacao do contrato -> `{ ok: false }`; a tela
 * mostra erro, nunca um numero de um payload que nao fecha.
 */
export function lerJornada(json: unknown, canalPedido: CanalJornada): ResultadoParser {
  try {
    const o = objeto(json, "resposta");
    const canal = typeof o.channel === "string" ? CANAIS_RESPOSTA[o.channel] : undefined;
    if (!canal) falha("channel fora da allowlist");
    if (canal !== canalPedido) falha("channel diferente do pedido");
    const status = o.status;
    if (typeof status !== "string" || !(STATUS_JORNADA as readonly string[]).includes(status)) {
      falha("status inválido");
    }
    const resposta: RespostaJornada = {
      channel: canal,
      status: status as StatusJornada,
      reason: textoOuNulo(o.reason, "reason"),
      brands_applied: textos(o.brands_applied, "brands_applied"),
      generated_at: textoOuNulo(o.generated_at, "generated_at"),
      fila_aberta: filaAberta(o.fila_aberta),
      coorte: coorte(o.coorte),
      limitacoes: textos(o.limitacoes, "limitacoes"),
    };
    // `ok` com secao que nao fecha e' contradicao do proprio payload.
    const naoFecha =
      (resposta.fila_aberta && !resposta.fila_aberta.etapas_fecham) ||
      (resposta.coorte && !resposta.coorte.etapas_fecham);
    if (resposta.status === "ok" && naoFecha) falha("status ok com etapas que não fecham");
    if (resposta.status === "ok" && !resposta.fila_aberta && !resposta.coorte) {
      falha("status ok sem nenhuma seção");
    }
    return { ok: true, resposta };
  } catch (e) {
    if (e instanceof Invalido) return { ok: false, erro: e.message };
    throw e;
  }
}

// ---------------------------------------------------------------------------
// Requisicao e estado
// ---------------------------------------------------------------------------
/** `tiktokshop` (canal da tela) -> `tiktok` (canal da rota). */
export function canalDaJornada(canal: "shopee" | "mercadolivre" | "tiktokshop"): CanalJornada {
  return canal === "tiktokshop" ? "tiktok" : canal;
}

export function queryDaJornada(
  canal: CanalJornada,
  marca: string | null,
  opcoes: { dias?: number; evento?: "coleta" | "despacho" } = {},
): string {
  const p = new URLSearchParams();
  p.set("channel", canal);
  // A marca da URL (`?marca=`) vai como `brands`: o servidor recorta TODOS os
  // totais, nao so' uma lista.
  if (marca) p.set("brands", marca);
  if (opcoes.dias !== undefined && canal !== "mercadolivre") p.set("days", String(opcoes.dias));
  // `event` so' existe no TikTok (fora dele a API responde 422).
  if (opcoes.evento && canal === "tiktok") p.set("event", opcoes.evento);
  return `/api/v1/expedicao/jornada?${p.toString()}`;
}

export type EstadoJornada = "carregando" | "indisponivel" | "erro" | "fora_do_ar" | "pronta";

export interface CargaJornada {
  carregando: boolean;
  /** Status HTTP da ultima resposta (null = sem resposta: erro de rede). */
  http: number | null;
  erroDeRede: boolean;
  resultado: ResultadoParser | null;
}

/**
 * A rota AINDA NAO existe em producao: 404, erro de rede e `disabled` sao
 * "Jornada indisponível" discreto (`indisponivel`), sem alarme. Falha do
 * servidor (5xx) ou payload fora do contrato sao `erro`, com nova tentativa.
 * `unavailable` vira `fora_do_ar` com o motivo.
 */
export function estadoDaJornada(c: CargaJornada): EstadoJornada {
  if (c.carregando) return "carregando";
  if (c.erroDeRede || c.http === 404) return "indisponivel";
  if (c.http !== null && c.http >= 400) return "erro";
  if (!c.resultado) return "carregando";
  if (!c.resultado.ok) return "erro";
  if (c.resultado.resposta.status === "disabled") return "indisponivel";
  if (c.resultado.resposta.status === "unavailable") return "fora_do_ar";
  return "pronta";
}

// ---------------------------------------------------------------------------
// Rotulos e textos
// ---------------------------------------------------------------------------
const ROTULO_ETAPA: Record<string, string> = {
  aguardando_expedicao: "Aguardando expedição",
  fora_amostra: "Amostras (fora da taxa)",
  fora_fbs: "FBS (envio pela Shopee)",
  cancelado_antes: "Cancelados antes do envio",
  enviado_sem_registro: "Enviados sem marco de coleta",
  aguardando: "Aguardando envio",
};

const ROTULO_SOBREPOSICAO: Record<string, string> = {
  vencido: "vencidos",
  vence_24h: "vencem em 24 h",
  sem_prazo: "sem prazo publicado",
  acima_48h: "abertos há mais de 48 h (limiar interno)",
  travado: "travados",
  lento: "lentos",
  zumbi_fonte: "parados na fonte",
  aguardando_vencido: "vencidos ainda sem envio",
  concluido_atrasado: "enviados com atraso",
};

function humanizar(key: string): string {
  const t = key.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** O `label` da API manda; o mapa local so' cobre resposta sem ele. */
export function rotuloEtapa(e: EtapaJornada, evento: "coleta" | "despacho" | null = null): string {
  if (e.label) return e.label;
  if (e.key === "concluido") return evento === "despacho" ? "Despachados" : "Coletados";
  return ROTULO_ETAPA[e.key] ?? humanizar(e.key);
}

export function rotuloSobreposicao(key: string): string {
  return ROTULO_SOBREPOSICAO[key] ?? key.replace(/_/g, " ");
}

/** Largura da barra da etapa, em % da base. Base 0 nao desenha nada. */
export function larguraDaEtapa(count: number, base: number): number {
  if (!(base > 0)) return 0;
  return Math.min(100, (count / base) * 100);
}

/**
 * Uma sobreposicao em frase, SEMPRE como subconjunto:
 * "62 vencidos dentro de Aguardando expedição (3,4% · 62 de 1.820)".
 * `count = null` -> "Vencidos: indisponível — <motivo>", nunca 0.
 */
export function textoDaSobreposicao(
  s: SobreposicaoJornada,
  ets: EtapaJornada[],
  base: number,
  evento: "coleta" | "despacho" | null = null,
): string {
  const rotulo = rotuloSobreposicao(s.key);
  if (s.count === null) {
    const r = rotulo.charAt(0).toUpperCase() + rotulo.slice(1);
    return `${r}: ${TEXTO_INDISPONIVEL}${s.reason ? ` — ${s.reason}` : ""}`;
  }
  const pai = s.subconjunto_de ? ets.find((e) => e.key === s.subconjunto_de) : null;
  const unica = !pai && ets.length === 1 ? ets[0] : null;
  const conjunto = pai ?? unica;
  const dentro = conjunto ? `dentro de ${rotuloEtapa(conjunto, evento)}` : "dentro da base";
  const denominador = pai ? pai.count : base;
  return `${formatarInteiro(s.count)} ${rotulo} ${dentro} (${formatarPercentualComBase(s.count, denominador)})`;
}

const MOTIVOS: Record<string, string> = {
  table_not_migrated: "a tabela ainda não foi publicada nesta base",
  no_series_published: "nenhuma série publicada ainda",
  disabled: "a rota está desligada",
  feature_flag_disabled: "a rota está desligada",
};

/**
 * `reason` vem com o prefixo da secao ("coorte: table_not_migrated"). Codigo
 * conhecido vira frase; texto livre passa como veio; codigo desconhecido
 * nao e' ecoado cru.
 */
export function explicarMotivo(reason: string | null): string | null {
  if (!reason) return null;
  return reason
    .split(/;\s*/)
    .map((parte) => {
      const m = parte.match(/^(fila_aberta|coorte):\s*(.*)$/);
      const secao = m ? (m[1] === "coorte" ? "Coorte" : "Fila aberta") : null;
      const resto = (m ? m[2] : parte).trim();
      const codigo = resto.match(/^([a-z_]+)(?:\s*\((\d+)\))?$/);
      let frase: string;
      if (codigo && MOTIVOS[codigo[1]]) frase = MOTIVOS[codigo[1]];
      else if (codigo && codigo[1] === "dias_sem_publicacao") frase = `${codigo[2] ?? "algum"} dia(s) sem publicação na janela`;
      else if (codigo) frase = "motivo não detalhado pela API";
      else frase = resto;
      return secao ? `${secao}: ${frase}` : frase;
    })
    .join("; ");
}

/** "pedidos pagos de 19/10 a 25/10" (TikTok) / "pedidos com vencimento ..." (Shopee). */
export function rotuloDaCoorte(c: CoorteJornada): string {
  return rotuloCoorte(c.janela.inicio, c.janela.fim, c.eixo === "paid_date" ? "pagamento" : "vencimento");
}

/** Frescor da fila aberta: o vocabulario da fila (`fresh` e' o unico em dia). */
export function filaDesatualizada(f: FilaAbertaJornada): boolean {
  return f.freshness !== "fresh";
}

/**
 * QA T-0010 (I4): a tela pediu `brands` e a API nao as aplicou? Entao os
 * numeros sao do canal inteiro e a tela precisa DIZER isso.
 */
export function avisoDeMarcaNaoAplicada(marca: string | null, r: RespostaJornada): string | null {
  if (!marca) return null;
  const aplicadas = r.brands_applied.map((b) => b.toLowerCase());
  if (aplicadas.includes(marca.toLowerCase())) return null;
  return `O filtro de marca (${marca.toUpperCase()}) não foi aplicado pela API: os números da jornada são do canal inteiro.`;
}
