/**
 * T-0010 — dados FICTICIOS da Expedicao, para QA visual sem API.
 *
 * So' sao carregados pelo modo de demonstracao (`?demo=`), que existe apenas
 * fora de producao (`expedicao-demo.ts`). Nenhum numero aqui veio da fonte.
 * Os valores sao inventados mas COERENTES com os contratos: a particao de
 * prazo da Shopee fecha o backlog, as etapas da jornada fecham a base e as
 * sobreposicoes sao subconjuntos — o QA nao valida tela sobre payload
 * impossivel (`tests/expedicao-redesign.test.ts` trava isso).
 *
 * Cenarios (`?demo=<cenario>`; `1` = `ok`):
 *   ok            tudo publicado e em dia
 *   parcial       jornada parcial (etapas nao fecham), dias da LDR no prazo
 *   vazio         fila zerada, TikTok sem loja com base, jornada com base 0
 *   desatualizado fotografia e serie velhas, coorte desatualizada
 *   erro          todas as rotas respondem 500
 *   indisponivel  sem fotografia/serie; jornada 404 (como hoje em producao)
 *   carregando    nenhuma resposta chega
 *   jornada404    pagina ok; so' a Jornada responde 404 (como em producao hoje)
 *   jornadaErro   pagina ok; so' a Jornada responde 500 (erro com nova tentativa)
 *   jornadaVazia  pagina ok; a Jornada responde com base 0
 *   jornadaCoorteAusente  pagina ok; Shopee com a fila valida e a coorte
 *                 nula (`coorte: table_not_migrated`) — R21: a fila continua
 *
 * Com `&marca=`, a Jornada recorta pela marca e devolve `brands_applied`.
 */
import type {
  PontoTendencia,
  RespostaExpedicao,
  RespostaTendencia,
  ResumoConta,
  Totais,
} from "./expedicao-contract.ts";
import type { RespostaLdrShopee } from "./expedicao-shopee-ldr.ts";
import type { TikTokLdrDia, TikTokPayload } from "./expedicao-tiktok-contract.ts";

export const CENARIOS_DEMO = [
  "ok",
  "parcial",
  "vazio",
  "desatualizado",
  "erro",
  "indisponivel",
  "carregando",
  // So' a Jornada muda; o resto da pagina fica no cenario `ok`:
  "jornada404",
  "jornadaErro",
  "jornadaVazia",
  "jornadaCoorteAusente",
] as const;
export type CenarioDemo = (typeof CENARIOS_DEMO)[number];

/** `1` (o atalho documentado) e valores desconhecidos abrem no `ok`. */
export function lerCenario(valor: string | null): CenarioDemo {
  return (CENARIOS_DEMO as readonly string[]).includes(valor ?? "") ? (valor as CenarioDemo) : "ok";
}

const AGORA = "2026-10-26T18:00:00Z";
const VELHO = "2026-10-25T09:00:00Z";

function totais(backlog: number, vencidos: number, em24h: number, semPrazo: number, extra: Partial<Totais> = {}): Totais {
  return {
    backlog_count: backlog,
    overdue_count: vencidos,
    due_within_24h_count: em24h,
    on_time_count: backlog - vencidos - em24h - semPrazo,
    deadline_unavailable_count: semPrazo,
    over_48h_count: Math.round(backlog * 0.08),
    slow_count: Math.round(backlog * 0.02),
    zombie_count: 1,
    stalled_count: Math.round(backlog * 0.02) + 1,
    ...extra,
  };
}

function soma(lista: Totais[]): Totais {
  const chaves = Object.keys(lista[0]) as (keyof Totais)[];
  return Object.fromEntries(chaves.map((k) => [k, lista.reduce((s, t) => s + t[k], 0)])) as unknown as Totais;
}

function conta(shop: string, brand: string, t: Totais, hora: string): ResumoConta {
  return { ...t, shop_account: shop, brand, run_status: "success", source_watermark_at: hora, source_advanced: true, snapshot_hour: hora };
}

// ---------------------------------------------------------------------------
// Fila (Shopee / Mercado Livre)
// ---------------------------------------------------------------------------
function fila(canal: "shopee" | "mercadolivre", c: CenarioDemo): RespostaExpedicao {
  const hora = c === "desatualizado" ? VELHO : AGORA;
  const idade = c === "desatualizado" ? 33 : 0.6;
  const vazio = c === "vazio";
  const shopee = canal === "shopee";
  const contas = shopee
    ? [
        conta("apice", "apice", vazio ? totais(0, 0, 0, 0) : totais(620, 24, 130, 6), hora),
        conta("barbours", "barbours", vazio ? totais(0, 0, 0, 0) : totais(980, 38, 210, 4), hora),
        conta("kokeshi", "kokeshi", vazio ? totais(0, 0, 0, 0) : totais(220, 0, 40, 0), hora),
      ]
    : [
        // ML: sem prazo publicado — todo o backlog e' "sem prazo".
        conta("2227056661", "kokeshi", vazio ? totais(0, 0, 0, 0) : totais(140, 0, 0, 140, { over_48h_count: 31 }), hora),
        conta("1984003355", "barbours", vazio ? totais(0, 0, 0, 0) : totais(90, 0, 0, 90, { over_48h_count: 12 }), hora),
      ];
  const t = soma(contas);
  const marcas = contas.map((x) => x.brand);
  return {
    availability: c === "indisponivel" ? "unavailable" : "available",
    unavailable_reason: c === "indisponivel" ? "no_snapshot" : null,
    channel: canal,
    snapshot: {
      channel: canal,
      refresh_batch_id: "demo",
      effective_at: hora,
      snapshot_hour: hora,
      load_mode: "manual_snapshot",
      source_health: "ok",
      source_advanced: true,
      run_status: "success",
      rows_extracted: t.backlog_count,
      rows_loaded: t.backlog_count,
      audit_run_id: null,
    },
    totals: c === "indisponivel" ? null : t,
    accounts: c === "indisponivel" ? [] : contas,
    freshness: marcas.map((b, i) => ({
      brand: b,
      freshness: c === "desatualizado" ? "critical" : "fresh",
      status: "pass",
      severity: "low",
      source_watermark_at: hora,
      source_age_hours: c === "desatualizado" ? 30 : 0.5,
      oldest_row_age_hours: 70 + i * 20,
      accounts: 1,
      observed_at: hora,
      measures: null,
      derived_from_batch: false,
    })),
    coverage: {
      expected_accounts: contas.map((x) => x.shop_account),
      observed_accounts: contas.map((x) => x.shop_account),
      missing_accounts: [],
      unexpected_accounts: [],
      accounts: [],
      brands_not_covered: shopee ? [] : ["apice"],
    },
    queue: vazio
      ? []
      : contas.slice(0, 2).map((x, i) => ({
          order_ref: null,
          shop_account: x.shop_account,
          brand: x.brand,
          created_at: hora,
          paid_at: hora,
          dispatch_deadline: shopee ? hora : null,
          deadline_source: shopee ? "ship_by_date" : "unavailable",
          deadline_status: shopee ? (i === 0 ? "overdue" : "due_within_24h") : "unavailable",
          operational_age_status: i === 0 ? "over_48h" : "within_48h",
          is_slow_vs_baseline: false,
          is_source_zombie: false,
          is_stalled: false,
          hours_open: i === 0 ? 61 : 20,
          hours_overdue: null,
          logistic_type: null,
          carrier: "Transportadora demo",
          source_ingested_at: hora,
          source_freshness_status: "fresh",
          timestamp_quality: "ok",
        })),
    pagination: { limit: 100, offset: 0, returned: vazio ? 0 : 2, total: vazio ? 0 : t.backlog_count, has_more: !vazio },
    limitations: {
      no_automation: true,
      load_mode: "manual_snapshot",
      snapshot_age_hours: idade,
      brands_not_covered: shopee ? [] : ["apice"],
      order_identifier_withheld: true,
      notes: [],
    },
  };
}

function tendencia(canal: "shopee" | "mercadolivre", c: CenarioDemo): RespostaTendencia {
  const f = fila(canal, c);
  const pontos: PontoTendencia[] = [];
  const horas = c === "vazio" || c === "indisponivel" ? 0 : 24;
  for (let h = 0; h < horas; h += 3) {
    const instante = new Date(Date.parse(AGORA) - (horas - h) * 3_600_000).toISOString();
    f.accounts.forEach((a, i) => {
      // `parcial`: falta a segunda conta nas primeiras horas (linha interrompida).
      if (c === "parcial" && i === 1 && h < 9) return;
      const fator = 0.8 + 0.2 * Math.sin((h + i * 5) / 4);
      const t = totais(Math.round(a.backlog_count * fator), 0, 0, 0);
      pontos.push({
        ...t,
        snapshot_hour: instante,
        shop_account: a.shop_account,
        brand: a.brand,
        refresh_batch_id: "demo",
        observed_at: instante,
        source_watermark_at: instante,
        source_advanced: true,
        run_status: "success",
      });
    });
  }
  return {
    availability: "available",
    unavailable_reason: null,
    channel: canal,
    window_hours: 72,
    from_hour: pontos[0]?.snapshot_hour ?? null,
    to_hour: pontos[pontos.length - 1]?.snapshot_hour ?? null,
    points: pontos,
    truncated: false,
    limitations: f.limitations,
  };
}

// ---------------------------------------------------------------------------
// LDR operacional da Shopee
// ---------------------------------------------------------------------------
function ldrShopee(c: CenarioDemo): RespostaLdrShopee {
  const f = fila("shopee", c);
  const vendedor = f.totals ?? totais(0, 0, 0, 0);
  const fbs = totais(0, 0, 0, 0);
  return {
    availability: c === "indisponivel" ? "unavailable" : "available",
    unavailable_reason: c === "indisponivel" ? "table_not_migrated" : null,
    channel: "shopee",
    metric_name: "LDR operacional (coleta × prazo Shopee)",
    official: false,
    disclaimer: "Indicador interno da Torre: não é a taxa de envio atrasado que a Shopee usa para penalização.",
    event: "coleta",
    thresholds: { coverage_warning: 0.02, coverage_hide_rate: 0.1 },
    snapshot: {
      refresh_batch_id: "demo",
      effective_at: c === "desatualizado" ? VELHO : AGORA,
      source_watermark_at: AGORA,
      today_brt: "2026-10-26",
      age_hours: c === "desatualizado" ? 33 : 0.6,
      stale: c === "desatualizado",
    },
    ldr: {
      from: "2026-10-19",
      to: "2026-10-26",
      days: 7,
      rate: c === "vazio" ? null : 0.0335,
      rate_hidden_reason: null,
      base: c === "vazio" ? 0 : 5940,
      late: c === "vazio" ? 0 : 199,
      shipped_on_time: 5741,
      shipped_late: 160,
      pending_overdue: 39,
      pending_on_time: 340,
      pending_at_risk: 170,
      mature_due_days: c === "vazio" ? 0 : 7,
      partial_due_days: c === "parcial" ? 2 : 1,
      daily: [],
    },
    exclusions: {
      fbs: 210,
      cancelled_without_pickup: 44,
      cancelled_seller_did_not_ship: 3,
      cancelled_unknown_reason: 0,
      shipped_without_pickup: 2,
      without_deadline: 4,
    },
    coverage: { state: "completa", missing_share: 0.001, missing: 6, eligible: 5946, without_deadline: 4, shipped_without_pickup: 2 },
    brands: [],
    alerts: [],
    queue_by_flow: {
      effective_at: AGORA,
      source_watermark_at: AGORA,
      stale: c === "desatualizado",
      seller: vendedor,
      fbs,
      total: vendedor,
      accounts: f.accounts.map((a) => ({ shop_account: a.shop_account, brand: a.brand, seller: a, fbs })),
    },
  };
}

// ---------------------------------------------------------------------------
// TikTok
// ---------------------------------------------------------------------------
function tiktok(c: CenarioDemo): TikTokPayload {
  const taxas = [0.021, 0.034, 0.052, 0.118, 0.046, 0.03, 0.025];
  const daily: TikTokLdrDia[] = taxas.map((r, i) => ({
    due_date: `2026-10-${String(19 + i).padStart(2, "0")}`,
    base: 600,
    late: Math.round(600 * r),
    pending_overdue: i === 6 ? 4 : 0,
    pending_on_time: 0,
    rate: r,
    is_mature: !(c === "parcial" && i >= 5),
    above_target: r > 0.04,
    is_critical: r > 0.1,
  }));
  daily.push({ due_date: "2026-10-26", base: 520, late: 0, pending_overdue: 0, pending_on_time: 140, rate: 0, is_mature: false, above_target: false, is_critical: false });
  const maduros = daily.filter((d) => d.is_mature);
  const base = maduros.reduce((s, d) => s + d.base, 0);
  const late = maduros.reduce((s, d) => s + d.late, 0);
  return {
    availability: c === "indisponivel" ? "unavailable" : "available",
    unavailable_reason: c === "indisponivel" ? "no_series_published" : null,
    channel: "tiktokshop",
    event: "coleta",
    deadline_is_reconstructed: true,
    sla_business_days: { coleta: 2, despacho: 1 },
    targets: { tiktok_ldr: 0.04, internal_critical: 0.1 },
    snapshot: {
      refresh_batch_id: "demo",
      effective_at: c === "desatualizado" ? VELHO : AGORA,
      source_watermark_at: AGORA,
      today_brt: "2026-10-26",
    },
    ldr: {
      from: "2026-10-19",
      to: "2026-10-26",
      days: 7,
      rate: base ? late / base : null,
      base,
      late,
      pending_overdue: 4,
      pending_on_time: 140,
      pending_at_risk: 88,
      mature_due_days: maduros.length,
      partial_due_days: daily.length - maduros.length,
      above_target: base ? late / base > 0.04 : false,
      internal_critical: false,
      incident_start: "2026-10-22",
      incident_end: "2026-10-22",
      daily,
    },
    payment_flow: [],
    brands:
      c === "vazio"
        ? []
        : [
            { brand: "kokeshi", shop_name: "Kokeshi Oficial", base: 2100, late: 120, rate: 120 / 2100, above_target: true },
            { brand: "barbours", shop_name: "Barbours", base: 2100, late: 50, rate: 50 / 2100, above_target: false },
          ],
    alerts: [
      { severity: "critical", code: "fora_da_meta_tiktok", message: "Acima da meta — medição interna (dados fictícios)." },
      ...(c === "desatualizado"
        ? [{ severity: "warning" as const, code: "fotografia_velha", message: "Última atualização há 33 h (dados fictícios)." }]
        : []),
    ],
  };
}

// ---------------------------------------------------------------------------
// Jornada (contrato v1.2)
// ---------------------------------------------------------------------------
/**
 * Parte de cada marca na base do canal (ficticia). A jornada da demo recorta
 * pela marca como a API real faz: o filtro vale tambem para os TOTAIS, e
 * `brands_applied` diz qual marca foi aplicada.
 */
const PARTE_DA_MARCA: Record<string, Record<string, number>> = {
  shopee: { apice: 620 / 1820, barbours: 980 / 1820, kokeshi: 220 / 1820 },
  mercadolivre: { kokeshi: 140 / 230, barbours: 90 / 230 },
  tiktok: { kokeshi: 0.5, barbours: 0.5 },
};

/** Etapas proporcionais que FECHAM a base (a ultima leva o resto). */
function etapasQueFecham(base: number, modelo: { key: string; label: string; parte: number }[]) {
  let usado = 0;
  return modelo.map((m, i) => {
    const count = i === modelo.length - 1 ? base - usado : Math.floor(base * m.parte);
    usado += count;
    return { key: m.key, label: m.label, count };
  });
}

function jornada(canal: string, marca: string | null, c: CenarioDemo): { status: number; corpo: unknown } {
  if (c === "indisponivel" || c === "jornada404") return { status: 404, corpo: { detail: "Not Found" } };
  if (c === "jornadaErro") return { status: 500, corpo: { detail: "erro fictício de demonstração" } };
  const vazio = c === "vazio" || c === "jornadaVazia";
  const parte = marca ? (PARTE_DA_MARCA[canal]?.[marca.toLowerCase()] ?? 0) : 1;
  const escala = (n: number) => (vazio ? 0 : Math.round(n * parte));
  const aplicadas = marca ? [marca.toLowerCase()] : [];
  const publicado = c === "desatualizado" ? VELHO : AGORA;
  const filaAberta = (grain: "pedido" | "shipment", base: number, sobre: unknown[], limitacoes: string[]) => ({
    grain,
    snapshot_at: publicado,
    freshness: c === "desatualizado" ? "critical" : "fresh",
    base,
    // `parcial`: a etapa publicada nao fecha a base (o servidor nao conserta).
    etapas: [{ key: "aguardando_expedicao", label: "Aguardando expedição", count: c === "parcial" && base > 3 ? base - 3 : base }],
    etapas_fecham: !(c === "parcial" && base > 3),
    sobreposicoes: sobre,
    limitacoes,
  });
  const coorte = (eixo: "paid_date" | "deadline_date", base: number, modelo: { key: string; label: string; parte: number }[]) => {
    const etapas = etapasQueFecham(base, modelo);
    const aguardando = etapas.find((e) => e.key === "aguardando")?.count ?? 0;
    const concluido = etapas.find((e) => e.key === "concluido")?.count ?? 0;
    return {
      eixo,
      evento: "coleta",
      janela: { inicio: "2026-10-19", fim: "2026-10-25", dias: 7 },
      base,
      etapas,
      etapas_fecham: true,
      sobreposicoes: [
        { key: "aguardando_vencido", count: Math.floor(aguardando * 0.14), subconjunto_de: "aguardando" },
        { key: "concluido_atrasado", count: Math.floor(concluido * 0.04), subconjunto_de: "concluido" },
      ],
      fora_da_coorte: eixo === "deadline_date" ? [{ key: "sem_prazo", count: escala(4) }] : [],
      maturidade: { dias_maduros: 6, dias_imaturos: 1 },
      limitacoes: c === "parcial" ? ["1 dia da janela sem publicação (dados fictícios)."] : [],
      publicado_em: publicado,
      desatualizada: c === "desatualizado",
    };
  };
  const status = c === "parcial" || c === "desatualizado" ? "partial" : "ok";
  const comum = { brands_applied: aplicadas, generated_at: AGORA };
  if (canal === "tiktok") {
    return {
      status: 200,
      corpo: {
        ...comum,
        channel: "tiktok",
        status,
        reason:
          c === "parcial" ? "coorte: dias_sem_publicacao (1)" : c === "desatualizado" ? "coorte: publicacao_desatualizada" : null,
        fila_aberta: null,
        coorte: coorte("paid_date", escala(4310), [
          { key: "fora_amostra", label: "Amostras (fora da taxa)", parte: 0.003 },
          { key: "cancelado_antes", label: "Cancelados antes do envio", parte: 0.019 },
          { key: "aguardando", label: "Aguardando coleta", parte: 0.07 },
          { key: "concluido", label: "Coletados", parte: 1 },
        ]),
        limitacoes: ["o TikTok não publica fila aberta em grão de pedido"],
      },
    };
  }
  if (canal === "mercadolivre") {
    const base = escala(230);
    return {
      status: 200,
      corpo: {
        ...comum,
        channel: "mercadolivre",
        status: c === "parcial" && base > 3 ? "partial" : "ok",
        reason: c === "parcial" && base > 3 ? "fila_aberta: etapas_nao_fecham" : null,
        fila_aberta: filaAberta(
          "shipment",
          base,
          [
            { key: "vencido", count: null, reason: "o Mercado Livre não publica prazo de despacho" },
            { key: "sem_prazo", count: base },
            { key: "acima_48h", count: Math.floor(base * 0.19) },
          ],
          ["substatus do shipment não publicado na fila"],
        ),
        coorte: null,
        limitacoes: ["o Mercado Livre não publica prazo de despacho"],
      },
    };
  }
  const base = escala(1820);
  // R21: a coorte nula (migration ausente) nao pode apagar a fila valida.
  const semCoorte = c === "jornadaCoorteAusente";
  return {
    status: 200,
    corpo: {
      ...comum,
      channel: "shopee",
      status: semCoorte ? "unavailable" : (c === "parcial" && base > 3) || c === "desatualizado" ? "partial" : "ok",
      reason: semCoorte
        ? "coorte: table_not_migrated"
        : c === "parcial" && base > 3
          ? "fila_aberta: etapas_nao_fecham"
          : c === "desatualizado"
            ? "coorte: desatualizada"
            : null,
      fila_aberta: filaAberta(
        "pedido",
        base,
        [
          { key: "vencido", count: Math.floor(base * 0.034) },
          { key: "vence_24h", count: Math.floor(base * 0.2) },
          { key: "acima_48h", count: Math.floor(base * 0.08) },
          { key: "travado", count: Math.floor(base * 0.02) },
        ],
        ["etapa interna (a preparar × envio organizado) não publicada na fila", "FBS não separado na fila aberta"],
      ),
      coorte: semCoorte
        ? null
        : coorte("deadline_date", escala(4310), [
            { key: "fora_fbs", label: "FBS (envio pela Shopee)", parte: 0.049 },
            { key: "cancelado_antes", label: "Cancelados antes da coleta", parte: 0.01 },
            { key: "enviado_sem_registro", label: "Enviados sem marco de coleta", parte: 0.0005 },
            { key: "aguardando", label: "Aguardando coleta", parte: 0.088 },
            { key: "concluido", label: "Coletados", parte: 1 },
          ]),
      limitacoes: semCoorte ? ["coorte por vencimento depende da LDR operacional publicada (dados fictícios)."] : [],
    },
  };
}

// ---------------------------------------------------------------------------
// Roteador
// ---------------------------------------------------------------------------
/**
 * Resposta ficticia para uma URL da Expedicao. `null` = rota que a
 * demonstracao nao conhece (o chamador cai no `fetch` real).
 */
export function respostaFicticia(url: string, cenarioPedido: CenarioDemo): { status: number; corpo: unknown } | null {
  let cenario: CenarioDemo = cenarioPedido;
  const u = new URL(url, "http://demo.local");
  const canal = u.searchParams.get("channel");
  const caminho = u.pathname;
  if (!caminho.startsWith("/api/v1/expedicao")) return null;
  if (cenario === "erro") return { status: 500, corpo: { detail: "erro fictício de demonstração" } };
  if (caminho === "/api/v1/expedicao/jornada") return jornada(canal ?? "shopee", u.searchParams.get("brands"), cenario);
  // Os cenarios so' da Jornada deixam o resto da pagina em `ok`.
  if (cenario.startsWith("jornada")) cenario = "ok";
  const filaCanal = canal === "mercadolivre" ? "mercadolivre" : "shopee";
  if (caminho === "/api/v1/expedicao") return { status: 200, corpo: fila(filaCanal, cenario) };
  if (caminho === "/api/v1/expedicao/trend") return { status: 200, corpo: tendencia(filaCanal, cenario) };
  if (caminho === "/api/v1/expedicao/tiktok") return { status: 200, corpo: tiktok(cenario) };
  if (caminho === "/api/v1/expedicao/shopee/ldr") return { status: 200, corpo: ldrShopee(cenario) };
  return null;
}
