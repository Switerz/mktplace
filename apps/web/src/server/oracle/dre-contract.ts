/**
 * torre_dre_mensal — CONTRATO FUTURO (MCP-COVERAGE-2, Fase 4).
 *
 * NAO REGISTRADA. Esta tool nao aparece em `tools/list` e o path da DRE NAO
 * esta na allowlist do adapter (`ENDPOINTS`). O que existe aqui e' o contrato
 * de entrada/saida e a projecao pura, testados contra o payload do PR #66/#68
 * (`GET /api/v1/dre`), para que a ativacao seja so' ligar fios.
 *
 * Checklist de ativacao (cada passo e' um PR/autorizacao proprio):
 *   1. #66 e #68 mesclados; migration 025 aplicada; 1a publicacao do lote;
 *   2. `GET /api/v1/dre` em producao respondendo `fontes.insumos_dre.estado=ok`;
 *   3. adicionar `dre: DRE_ENDPOINT_PATH` em `ENDPOINTS` e a allowlist de
 *      parametros (`channels`, `brands`, `ref_month`) por endpoint;
 *   4. registrar a tool em `ORACLE_TOOLS` e atualizar os testes de contagem.
 *
 * REGRAS (nenhum valor ausente vira zero):
 * - cada linha da DRE sai como `{ valor: number|null, status }`, com status
 *   CALCULADO, NAO_CALCULAVEL ou NAO_APLICAVEL. So' `disponivel` na fonte vira
 *   CALCULADO; `nao_aplicavel` vira NAO_APLICAVEL; QUALQUER outro status
 *   (parcial, indisponivel, snapshot_*, provisorio, nao_reconciliado,
 *   nao_confirmado, sinal_inesperado) vira NAO_CALCULAVEL com `valor=null` e o
 *   status original preservado em `status_fonte`;
 * - insumo publicado ha mais de 48 h (ou lote nao `ok`) torna CMV, taxas e
 *   frete que dependem dele NAO_CALCULAVEL — mesmo que a fonte diga outra coisa;
 * - margem (resultado e %) so' existe quando o CMV esta CALCULADO;
 * - custos conhecidos so' quando TODOS os custos usados na margem estao
 *   CALCULADOS.
 */
import { z } from "zod";

import { buildEnvelope, type Envelope } from "./envelope.ts";
import { BRANDS, CHANNELS, isoMonth, noDuplicates } from "./schemas.ts";
import { ageHours, envelopeSchema, limitationSchema } from "./tool-kit.ts";
import { numOrNull } from "./torre-client.ts";
import { parseUpstream } from "./upstream.ts";

export const DRE_TOOL_NAME = "torre_dre_mensal";
/** Path da API. Fora de `ENDPOINTS` de proposito ate a ativacao. */
export const DRE_ENDPOINT_PATH = "/api/v1/dre";
/** Idade maxima do lote de insumos (mesmo valor do backend). */
export const DRE_MAX_SNAPSHOT_HOURS = 48;

export const dreInput = z
  .object({
    mes: isoMonth,
    canais: noDuplicates(z.array(z.enum(CHANNELS)).min(1).max(3), "canais").optional(),
    marcas: noDuplicates(z.array(z.enum(BRANDS)).min(1).max(5), "marcas").optional(),
  })
  .strict();
export type DreInput = z.infer<typeof dreInput>;

// ---------------------------------------------------------------------------
// Upstream (subconjunto consumido de `DREResponse`)
// ---------------------------------------------------------------------------

const STATUS_FONTE = [
  "disponivel", "parcial", "indisponivel", "snapshot_ausente", "snapshot_vencido",
  "nao_reconciliado", "nao_confirmado", "provisorio", "sinal_inesperado", "nao_aplicavel",
] as const;

const componente = z.object({
  valor: z.number().finite().nullish(),
  pct_receita: z.number().finite().nullish(),
  status: z.enum(STATUS_FONTE),
  motivo: z.string().nullish(),
  somavel: z.boolean(),
  publicado_em: z.string().nullish(),
});

const NOMES = ["receita", "devolucoes", "taxas", "afiliados", "ads", "frete", "cmv", "impostos"] as const;
type Nome = (typeof NOMES)[number];

const celula = z.object({
  mes: z.string().nullish(),
  canal: z.string().nullish(),
  marca: z.string().nullish(),
  classificacao: z.enum(["COMPLETA", "PARCIAL", "NAO_CALCULAVEL"]),
  componentes: z.object(Object.fromEntries(NOMES.map((n) => [n, componente])) as Record<Nome, typeof componente>),
  margem_contribuicao_conhecida: z.object({
    valor: z.number().finite().nullish(),
    pct_receita: z.number().finite().nullish(),
    rotulo: z.string(),
    componentes_usados: z.array(z.string()),
    faltam: z.array(z.string()),
  }),
  data_maxima: z.object({ insumo_publicado_em: z.string().nullish() }),
});

export const dreUpstreamContract = z.object({
  periodo: z.object({ meses: z.array(z.string()) }),
  fontes: z.object({
    insumos_dre: z.object({
      estado: z.enum(["ok", "tabela_ausente", "nao_publicado", "vencido", "parcialmente_vencido"]),
      publicado_em: z.string().nullish(),
      motivo: z.string().nullish(),
    }),
  }),
  por_canal_marca: z.array(celula),
});

// ---------------------------------------------------------------------------
// Saida
// ---------------------------------------------------------------------------

export const DRE_STATUS = ["CALCULADO", "NAO_CALCULAVEL", "NAO_APLICAVEL"] as const;
type DreStatus = (typeof DRE_STATUS)[number];

const linha = z.object({
  valor: z.number().nullable(),
  status: z.enum(DRE_STATUS),
  status_fonte: z.string(),
  motivo: z.string().nullable(),
});

const celulaOut = z.object({
  mes: z.string(),
  canal: z.string(),
  marca: z.string(),
  classificacao_fonte: z.string(),
  receita: linha,
  cmv: linha,
  ads: linha,
  afiliados: linha,
  taxas: linha,
  frete: linha,
  devolucoes: linha,
  impostos: linha,
  custos_conhecidos: linha,
  /** Margem de contribuicao CONHECIDA — nao e' lucro nem margem liquida. */
  resultado: linha,
  margem_pct: z.number().nullable(),
  componentes_faltando: z.array(z.string()),
});

export const dreOutput = envelopeSchema(
  z.object({
    snapshot: z.object({
      estado: z.string(),
      /** O contrato da API nao expoe id de lote: o instante de publicacao o identifica. */
      lote_publicado_em: z.string().nullable(),
      idade_horas: z.number().nullable(),
      insumos_disponiveis: z.boolean(),
    }),
    celulas: z.array(celulaOut),
    limitations: z.array(limitationSchema),
  }),
);

type Linha = z.infer<typeof linha>;
type CelulaOut = z.infer<typeof celulaOut>;

/** Componentes cujo valor depende do lote de insumos publicado. */
const DEPENDEM_DO_LOTE: readonly Nome[] = ["cmv", "taxas", "frete"];

function mapStatus(s: (typeof STATUS_FONTE)[number]): DreStatus {
  if (s === "disponivel") return "CALCULADO";
  if (s === "nao_aplicavel") return "NAO_APLICAVEL";
  return "NAO_CALCULAVEL";
}

function linhaDe(
  c: z.infer<typeof componente>,
  loteVencido: boolean,
  dependeDoLote: boolean,
): Linha {
  // Insumo vencido derruba o componente MESMO que a fonte ainda o diga
  // disponivel: o conector nao confia num lote fora da janela.
  if (dependeDoLote && c.publicado_em && loteVencido && c.status === "disponivel") {
    return { valor: null, status: "NAO_CALCULAVEL", status_fonte: "snapshot_vencido", motivo: `insumo publicado ha mais de ${DRE_MAX_SNAPSHOT_HOURS} h` };
  }
  const status = mapStatus(c.status);
  return {
    valor: status === "CALCULADO" ? numOrNull(c.valor) : null,
    status,
    status_fonte: c.status,
    motivo: c.motivo ?? null,
  };
}

const naoCalc = (motivo: string, fonte = "derivado"): Linha => ({
  valor: null, status: "NAO_CALCULAVEL", status_fonte: fonte, motivo,
});

/** Projecao pura do payload da DRE. Contrato quebrado vira INVALID_UPSTREAM_RESPONSE. */
export function projectDre(raw: unknown, input: DreInput, now: Date): Envelope<unknown> {
  const p = parseUpstream(dreUpstreamContract, raw);
  const lote = p.fontes.insumos_dre;
  const idade = ageHours(lote.publicado_em ?? null, now);
  const loteVencido = lote.estado !== "ok" || idade === null || idade > DRE_MAX_SNAPSHOT_HOURS;

  const celulas: CelulaOut[] = p.por_canal_marca
    .filter((c) => (!input.canais || input.canais.includes(c.canal as never)))
    .filter((c) => (!input.marcas || input.marcas.includes(c.marca as never)))
    .map((c) => {
      const l = Object.fromEntries(
        NOMES.map((n) => [n, linhaDe(c.componentes[n], loteVencido, DEPENDEM_DO_LOTE.includes(n))]),
      ) as Record<Nome, Linha>;

      const m = c.margem_contribuicao_conhecida;
      const custosUsados = m.componentes_usados.filter((n): n is Nome => (NOMES as readonly string[]).includes(n) && n !== "receita");
      const todosCustosCalc = custosUsados.length > 0 && custosUsados.every((n) => l[n].status !== "NAO_CALCULAVEL");
      const custos = todosCustosCalc
        ? {
            valor: custosUsados.reduce((acc, n) => acc + (l[n].valor ?? 0), 0),
            status: "CALCULADO" as const,
            status_fonte: "derivado",
            motivo: `soma de ${custosUsados.join(", ")}`,
          }
        : naoCalc("ha custo usado na margem sem calculo");

      const cmvOk = l.cmv.status === "CALCULADO";
      const resultado = cmvOk && l.receita.status === "CALCULADO" && todosCustosCalc
        ? { valor: numOrNull(m.valor), status: "CALCULADO" as const, status_fonte: "derivado", motivo: m.rotulo }
        : naoCalc(cmvOk ? "componente da margem sem calculo" : "CMV nao calculado: margem nao existe");

      return {
        mes: c.mes ?? input.mes,
        canal: c.canal ?? "?",
        marca: c.marca ?? "?",
        classificacao_fonte: c.classificacao,
        receita: l.receita, cmv: l.cmv, ads: l.ads, afiliados: l.afiliados, taxas: l.taxas,
        frete: l.frete, devolucoes: l.devolucoes, impostos: l.impostos,
        custos_conhecidos: custos,
        resultado,
        margem_pct: resultado.status === "CALCULADO" ? numOrNull(m.pct_receita) : null,
        componentes_faltando: m.faltam,
      };
    });

  const warnings: string[] = [];
  if (loteVencido) {
    warnings.push(`Insumos da DRE indisponiveis (lote ${lote.estado}${idade !== null ? `, ${idade} h` : ""}): CMV, taxas e frete dependentes do lote nao sao calculados.`);
  }

  return buildEnvelope({
    period: null,
    filtersApplied: { mes: input.mes, canais: input.canais ?? null, marcas: input.marcas ?? null },
    metricDefinition:
      "Resultado = margem de contribuicao CONHECIDA (receita menos custos conhecidos). Nao e' lucro nem margem liquida. Margem so' com CMV calculado.",
    refreshedAt: lote.publicado_em ?? null,
    coverage: "celulas mes x canal x marca",
    returnedCount: celulas.length,
    warnings,
    data: {
      snapshot: {
        estado: lote.estado,
        lote_publicado_em: lote.publicado_em ?? null,
        idade_horas: idade,
        insumos_disponiveis: !loteVencido,
      },
      celulas,
      limitations: [],
    },
  });
}
