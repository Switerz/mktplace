/**
 * CX-MKT-3 — faixa "LDR · últimos 7 dias" no topo da Expedição.
 *
 * Uma matriz marca × marketplace com a taxa de atraso de envio (LDR) de cada
 * conta, e a META e o TETO de cada canal para ver se estamos perto do limite.
 * Sem React aqui: tudo testável com `node --test`.
 *
 * REGRA DE CADA CANAL (contrato: docs/expedicao_contrato.md)
 * ----------------------------------------------------------
 *   Shopee  pedidos com `ship_by_date` vencendo na janela que não foram
 *           coletados até o prazo ÷ pedidos com prazo vencendo (LDR
 *           operacional, `GET /expedicao/shopee/ldr?days=7`).
 *   TikTok  mesma conta com o prazo de 2 dias úteis após o pagamento, coleta
 *           como marco (`GET /expedicao/tiktok?event=coleta&days=7`).
 *   ML      prazo oficial de despacho do ML (SLA); atrasado = coletado depois do
 *           FIM DO DIA do prazo, marcado `delayed` pelo ML, ou sem coleta com o
 *           dia fechado (`GET /expedicao/ml/ldr?days=7`). Série desde 03/11/2026:
 *           com menos de 7 dias fechados a célula diz "janela parcial".
 *
 * Os números são CÁLCULO TORRE, não a métrica oficial de penalização de cada
 * plataforma (essa fica na seção "Performance"). Ausência nunca vira 0%.
 */

export type CanalLdr = "shopee" | "mercadolivre" | "tiktokshop";

export interface MetaTeto {
  /** Onde queremos estar (fração, 0.02 = 2%). */
  meta: number;
  /** Limite antes de risco de penalização (fração). */
  teto: number;
  origemTeto: string;
}

/**
 * Meta e teto por canal, CONFIRMADOS pelo negócio em 04/11/2026. O teto do ML
 * é o limite MercadoLíder de envios com atraso (doc de reputação do ML); o do
 * TikTok é a referência de 4% que a Torre já usava; o da Shopee é interno.
 */
export const META_TETO: Record<CanalLdr, MetaTeto> = {
  shopee: { meta: 0.02, teto: 0.05, origemTeto: "referência interna" },
  mercadolivre: { meta: 0.03, teto: 0.06, origemTeto: "limite MercadoLíder para envios com atraso" },
  tiktokshop: { meta: 0.02, teto: 0.04, origemTeto: "referência TikTok usada pela Torre (sem fonte oficial)" },
};

export const CANAIS_LDR: readonly { canal: CanalLdr; titulo: string; regra: string }[] = [
  { canal: "shopee", titulo: "Shopee", regra: "prazo Shopee × coleta" },
  { canal: "mercadolivre", titulo: "Mercado Livre", regra: "prazo oficial ML × coleta até o fim do dia" },
  { canal: "tiktokshop", titulo: "TikTok Shop", regra: "2 dias úteis após o pagamento × coleta" },
];

export const DIAS_LDR_TOPO = 7;

/** Contas por canal (cobertura). Fora daqui = "sem conta neste canal". */
export const CONTAS_POR_CANAL: Record<CanalLdr, readonly string[]> = {
  shopee: ["apice", "barbours", "lescent", "rituaria"],
  mercadolivre: ["barbours", "kokeshi", "lescent", "rituaria"],
  tiktokshop: ["apice", "barbours", "kokeshi", "lescent", "rituaria"],
};

export type Faixa = "dentro" | "entre" | "acima";

/** Onde a taxa cai em relação à meta e ao teto. Limite exato conta como dentro. */
export function classificar(taxa: number, mt: MetaTeto): Faixa {
  if (taxa <= mt.meta) return "dentro";
  if (taxa <= mt.teto) return "entre";
  return "acima";
}

export const ROTULO_FAIXA: Record<Faixa, string> = {
  dentro: "Dentro da meta",
  entre: "Entre meta e teto",
  acima: "Acima do teto",
};

export type Celula =
  | { tipo: "dado"; taxa: number; atrasados: number; base: number; faixa: Faixa; parcial: string | null }
  | { tipo: "sem_base"; motivo: string }
  | { tipo: "sem_conta" }
  | { tipo: "indisponivel"; motivo: string }
  | { tipo: "carregando" };

/** Resposta crua de uma rota de LDR, reduzida ao que a faixa usa. */
export interface LinhaLdrMarca {
  brand: string;
  base: number;
  late: number;
  rate: number | null;
}

export type FonteCanal =
  | { estado: "carregando" }
  | { estado: "indisponivel"; motivo: string }
  | { estado: "dado"; marcas: LinhaLdrMarca[]; diasMaduros?: number; diasPedidos?: number };

function numero(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/**
 * Extrai `brands[]` de `/expedicao/tiktok` ou `/expedicao/shopee/ldr`. Forma
 * inválida = indisponível (nunca número parcial).
 */
export function lerRespostaLdr(json: unknown): FonteCanal {
  if (typeof json !== "object" || json === null) return { estado: "indisponivel", motivo: "Resposta inválida." };
  const j = json as Record<string, unknown>;
  if (j.availability !== "available") {
    const motivo = typeof j.unavailable_reason === "string" ? j.unavailable_reason : "não publicado";
    return { estado: "indisponivel", motivo: MOTIVOS_INDISPONIVEL[motivo] ?? "Série ainda não publicada." };
  }
  if (!Array.isArray(j.brands)) return { estado: "indisponivel", motivo: "Resposta inválida." };
  const marcas: LinhaLdrMarca[] = [];
  for (const b of j.brands) {
    if (typeof b !== "object" || b === null) return { estado: "indisponivel", motivo: "Resposta inválida." };
    const m = b as Record<string, unknown>;
    if (typeof m.brand !== "string" || !numero(m.base) || !numero(m.late)) {
      return { estado: "indisponivel", motivo: "Resposta inválida." };
    }
    if (!(m.rate === null || m.rate === undefined || numero(m.rate))) {
      return { estado: "indisponivel", motivo: "Resposta inválida." };
    }
    marcas.push({ brand: m.brand, base: m.base, late: m.late, rate: numero(m.rate) ? m.rate : null });
  }
  const parcial =
    numero(j.mature_days) && numero(j.requested_days) && j.mature_days < j.requested_days
      ? { diasMaduros: j.mature_days, diasPedidos: j.requested_days }
      : {};
  return { estado: "dado", marcas, ...parcial };
}

const MOTIVOS_INDISPONIVEL: Record<string, string> = {
  disabled: "LDR deste canal ainda desligado na Torre.",
  table_not_migrated: "LDR deste canal ainda não publicado (tabela pendente).",
  no_series_published: "Sem série publicada na janela.",
  series_not_started: "Prazo oficial do ML gravado desde 03/11/2026: o primeiro dia ainda não fechou.",
  source_error: "Não foi possível calcular agora.",
};

/** Motivo fixo da coluna do ML enquanto o SLA não tem 7 dias de série. */
export const MOTIVO_ML_AGUARDANDO =
  "Aguardando 7 dias de prazo oficial do ML (gravado desde 03/11/2026).";

export function celula(fonte: FonteCanal, canal: CanalLdr, marca: string): Celula {
  if (!CONTAS_POR_CANAL[canal].includes(marca)) return { tipo: "sem_conta" };
  if (fonte.estado === "carregando") return { tipo: "carregando" };
  if (fonte.estado === "indisponivel") return { tipo: "indisponivel", motivo: fonte.motivo };
  const linha = fonte.marcas.find((m) => m.brand === marca);
  if (!linha) return { tipo: "sem_base", motivo: "Nenhum pedido com prazo vencido na janela." };
  if (linha.rate === null || linha.base <= 0) {
    return { tipo: "sem_base", motivo: "Sem base madura na janela (não é 0%)." };
  }
  return {
    tipo: "dado",
    taxa: linha.rate,
    atrasados: linha.late,
    base: linha.base,
    faixa: classificar(linha.rate, META_TETO[canal]),
    parcial:
      fonte.diasMaduros !== undefined && fonte.diasPedidos !== undefined
        ? `janela parcial: ${fonte.diasMaduros} de ${fonte.diasPedidos} dias`
        : null,
  };
}

export interface LinhaMatriz {
  marca: string;
  celulas: Record<CanalLdr, Celula>;
}

export function montarMatriz(
  fontes: Record<CanalLdr, FonteCanal>,
  marcas: readonly string[],
): LinhaMatriz[] {
  return marcas.map((marca) => ({
    marca,
    celulas: {
      shopee: celula(fontes.shopee, "shopee", marca),
      mercadolivre: celula(fontes.mercadolivre, "mercadolivre", marca),
      tiktokshop: celula(fontes.tiktokshop, "tiktokshop", marca),
    },
  }));
}

const FMT_PCT = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const FMT_INT = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

export function pct(fracao: number): string {
  return `${FMT_PCT.format(fracao * 100)}%`;
}

export function inteiro(n: number): string {
  return FMT_INT.format(n);
}

/**
 * Posições (0-100) da barra: a escala vai até 1,6× o teto para a marca do teto
 * nunca encostar na borda; taxa acima disso fica cheia.
 */
export function escalaBarra(taxa: number, mt: MetaTeto): { preenchido: number; meta: number; teto: number } {
  const max = mt.teto * 1.6;
  const r = (v: number) => Math.round(Math.min(100, Math.max(0, (v / max) * 100)) * 10) / 10;
  return { preenchido: r(taxa), meta: r(mt.meta), teto: r(mt.teto) };
}

export function urlLdr(api: string, canal: CanalLdr, marca: string | null): string {
  const qs = new URLSearchParams({ days: String(DIAS_LDR_TOPO) });
  if (marca) qs.set("brands", marca);
  if (canal === "shopee") return `${api}/api/v1/expedicao/shopee/ldr?${qs.toString()}`;
  if (canal === "tiktokshop") {
    qs.set("event", "coleta");
    return `${api}/api/v1/expedicao/tiktok?${qs.toString()}`;
  }
  return `${api}/api/v1/expedicao/ml/ldr?${qs.toString()}`;
}
