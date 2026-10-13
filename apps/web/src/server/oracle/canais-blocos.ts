/**
 * MCP-COVERAGE-2 — blocos de afiliados e de descontos TikTok na tool de Canais.
 *
 * Os dois blocos JA vem no payload de `/performance/canais`; nao ha chamada
 * nova. Cada bloco e' validado ISOLADO: um bloco fora do contrato vira
 * `formato_inesperado` naquele bloco e nao derruba ROAS, custos e medianas.
 *
 * Regras:
 * - snapshot manual e' identificado (`snapshot_manual`);
 * - bloco disponivel e sem linhas e' AUSENCIA de registro, nunca custo/desconto
 *   zero nem "nenhum pedido";
 * - zero medido (linha presente com 0) continua 0; ausente continua null;
 * - receita atribuida e retorno/ROAS de afiliados nao existem na fonte: nulos,
 *   com o motivo da propria fonte.
 */
import { z } from "zod";

import {
  AFFILIATE_NO_RETURN, AFFILIATE_NO_ROWS, DISCOUNTS_NO_ROWS, type Limitation,
} from "./limitations.ts";
import { safeText, safeTexts } from "./tool-kit.ts";
import { numOrNull } from "./torre-client.ts";
import { affiliateCostsContract, tiktokDiscountsContract } from "./upstream-operacional.ts";

const ESTADO_BLOCO = z.enum([
  "available", "unavailable_no_source", "no_eligible_brand", "error",
  /** A fonte nao publicou o bloco nesta resposta. */
  "ausente",
  /** O bloco veio fora do contrato: descartado, sem numero. */
  "formato_inesperado",
]);

export const afiliadosSchema = z.object({
  estado: ESTADO_BLOCO,
  periodo_status: z.string().nullable(),
  cobertura_status: z.string().nullable(),
  frescor_status: z.string().nullable(),
  snapshot_manual: z.boolean(),
  atualizado_em: z.string().nullable(),
  marca_d_agua_fonte: z.string().nullable(),
  meses: z.array(z.string()),
  por_canal: z.array(z.object({ canal: z.string(), disponibilidade: z.string(), nota: z.string() })),
  /** Valores com o SINAL da fonte (custo negativo). Os tres nao sao somados. */
  linhas: z.array(
    z.object({
      canal: z.string(),
      brand: z.string(),
      mes: z.string(),
      comissao_criador: z.number().nullable(),
      comissao_parceiro: z.number().nullable(),
      comissao_ads_afiliado: z.number().nullable(),
    }),
  ),
  /** Sempre null: a fonte nao tem receita atribuida a afiliados. */
  receita_atribuida: z.null(),
  /** Sempre null: sem numerador, nao ha retorno/ROAS. */
  retorno_roas: z.null(),
  retorno_disponibilidade: z.string().nullable(),
  notas: z.array(z.string()),
});

export const descontosSchema = z.object({
  estado: ESTADO_BLOCO,
  periodo_status: z.string().nullable(),
  cobertura_status: z.string().nullable(),
  chaves_faltando: z.number().nullable(),
  frescor_status: z.string().nullable(),
  atualizado_em: z.string().nullable(),
  data_maxima_fonte: z.string().nullable(),
  janela: z.object({ start: z.string().nullable(), end: z.string().nullable() }),
  /** Desconto da marca e subsidio da plataforma NUNCA sao somados. */
  linhas: z.array(
    z.object({
      brand: z.string(),
      pedidos_comerciais: z.number(),
      gmv_oficial: z.number().nullable(),
      valor_cheio_produtos: z.number().nullable(),
      desconto_marca: z.number().nullable(),
      subsidio_plataforma: z.number().nullable(),
      desconto_marca_pct: z.number().nullable(),
      subsidio_plataforma_pct: z.number().nullable(),
      pedidos_cancelados: z.number(),
    }),
  ),
  avisos_da_fonte: z.array(z.string()),
  notas: z.array(z.string()),
});

export type AfiliadosOut = z.infer<typeof afiliadosSchema>;
export type DescontosOut = z.infer<typeof descontosSchema>;

function semAfiliados(estado: "ausente" | "formato_inesperado"): AfiliadosOut {
  return {
    estado, periodo_status: null, cobertura_status: null, frescor_status: null,
    snapshot_manual: false, atualizado_em: null, marca_d_agua_fonte: null, meses: [],
    por_canal: [], linhas: [], receita_atribuida: null, retorno_roas: null,
    retorno_disponibilidade: null, notas: [],
  };
}

function semDescontos(estado: "ausente" | "formato_inesperado"): DescontosOut {
  return {
    estado, periodo_status: null, cobertura_status: null, chaves_faltando: null,
    frescor_status: null, atualizado_em: null, data_maxima_fonte: null,
    janela: { start: null, end: null }, linhas: [], avisos_da_fonte: [], notas: [],
  };
}

export type CanaisBlocos = {
  afiliados: AfiliadosOut;
  descontos_tiktok: DescontosOut;
  limitations: Limitation[];
  warnings: string[];
};

/** Projeta os dois blocos a partir do payload CRU de `/canais`. */
export function projectCanaisBlocos(raw: unknown): CanaisBlocos {
  const obj = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const limitations: Limitation[] = [];
  const warnings: string[] = [];

  // ---- Afiliados ----------------------------------------------------------
  let afiliados: AfiliadosOut;
  if (obj.affiliate_costs === undefined || obj.affiliate_costs === null) {
    afiliados = semAfiliados("ausente");
  } else {
    const r = affiliateCostsContract.safeParse(obj.affiliate_costs);
    if (!r.success) {
      afiliados = semAfiliados("formato_inesperado");
      warnings.push("Bloco de afiliados em formato inesperado: descartado, nenhum valor de afiliado e' informado.");
    } else {
      const a = r.data;
      afiliados = {
        estado: a.availability_status,
        periodo_status: a.period_status,
        cobertura_status: a.coverage_status,
        frescor_status: a.freshness_status,
        snapshot_manual: a.freshness_status === "manual_snapshot",
        atualizado_em: a.affiliate_refreshed_at ?? null,
        marca_d_agua_fonte: a.source_watermark ?? null,
        meses: a.months_included,
        por_canal: a.channels.map((c) => ({
          canal: c.channel, disponibilidade: safeText(c.availability_status) ?? "", nota: safeText(c.reason_note) ?? "",
        })),
        linhas: a.rows.map((x) => ({
          canal: x.channel,
          brand: x.brand,
          mes: x.ref_month,
          comissao_criador: numOrNull(x.creator_commission_signed),
          comissao_parceiro: numOrNull(x.partner_commission_signed),
          comissao_ads_afiliado: numOrNull(x.affiliate_ads_commission_signed),
        })),
        receita_atribuida: null,
        retorno_roas: null,
        retorno_disponibilidade: a.return_availability,
        notas: safeTexts([a.return_note, a.limitation_note], 400),
      };
      limitations.push(AFFILIATE_NO_RETURN);
      if (a.availability_status === "available" && a.rows.length === 0) {
        limitations.push(AFFILIATE_NO_ROWS);
        warnings.push(`Afiliados: nenhuma linha neste periodo (period_status=${a.period_status}) — ausencia de registro, nao custo zero.`);
      }
      if (afiliados.snapshot_manual) {
        warnings.push("Afiliados: carga MANUAL (manual_snapshot) — cite a data de atualizacao junto do valor.");
      }
      if (a.coverage_status === "incomplete_brand_coverage") {
        warnings.push("Afiliados: cobertura de marcas incompleta no periodo.");
      }
    }
  }

  // ---- Descontos TikTok ---------------------------------------------------
  let descontos: DescontosOut;
  if (obj.tiktok_order_discounts === undefined || obj.tiktok_order_discounts === null) {
    descontos = semDescontos("ausente");
  } else {
    const r = tiktokDiscountsContract.safeParse(obj.tiktok_order_discounts);
    if (!r.success) {
      descontos = semDescontos("formato_inesperado");
      warnings.push("Bloco de descontos do TikTok em formato inesperado: descartado, nenhum valor de desconto e' informado.");
    } else {
      const d = r.data;
      descontos = {
        estado: d.availability_status,
        periodo_status: d.period_status,
        cobertura_status: d.coverage_status,
        chaves_faltando: d.coverage_missing_keys,
        frescor_status: d.freshness_status,
        atualizado_em: d.discounts_refreshed_at ?? null,
        data_maxima_fonte: d.source_max_date ?? null,
        janela: { start: d.date_from ?? null, end: d.date_to ?? null },
        linhas: d.rows.map((x) => ({
          brand: x.brand,
          pedidos_comerciais: x.commercial_orders,
          gmv_oficial: numOrNull(x.official_gmv),
          valor_cheio_produtos: numOrNull(x.full_product_value),
          desconto_marca: numOrNull(x.seller_discount_signed),
          subsidio_plataforma: numOrNull(x.platform_subsidy_amount),
          desconto_marca_pct: numOrNull(x.seller_discount_rate),
          subsidio_plataforma_pct: numOrNull(x.platform_subsidy_rate),
          pedidos_cancelados: x.cancelled_orders,
        })),
        avisos_da_fonte: safeTexts(d.warnings, 400),
        notas: safeTexts([d.seller_note, d.subsidy_note, d.limitation_note], 400),
      };
      if (d.availability_status === "available" && d.rows.length === 0) {
        limitations.push(DISCOUNTS_NO_ROWS);
      }
      if (d.freshness_status === "stale_load") {
        warnings.push("Descontos TikTok: ultima carga antiga (stale_load) — revisoes recentes da fonte podem faltar.");
      }
      if (d.coverage_missing_keys > 0) {
        warnings.push(`Descontos TikTok: ${d.coverage_missing_keys} combinacao(oes) dia x marca sem registro na janela.`);
      }
    }
  }

  return { afiliados, descontos_tiktok: descontos, limitations, warnings };
}
