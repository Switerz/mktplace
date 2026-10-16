/**
 * DRE-UX-3 — fixtures SINTETICAS do cenario estimado (contrato do PR #79).
 *
 * Imitam a SAIDA da API: valores ja' prontos — a tela nao calcula nada. Taxas
 * e valores redondos e inventados; nenhuma premissa real.
 */
import type { CelulaDRE, DreResponse } from "../src/lib/dre.ts";
import type { CenarioPremissas, LinhaCenario } from "../src/lib/dre-estimada.ts";
import { ML, TIKTOK, resposta } from "./dre-fixtures.ts";

const PREMISSAS_FX: [string, string, number | null][] = [
  ["devolucoes_reembolsos", "(-) Devoluções e Reembolsos", 0.02],
  ["icms", "ICMS", 0.1],
  ["difal", "DIFAL", 0.01],
  ["pis", "PIS", 0.01],
  ["cofins", "COFINS", 0.03],
  ["iss_ipi_simples", "ISS/IPI/Simples", 0],
  ["frete_primario", "(-) Frete Primário", 0.01],
  ["bonificacoes", "(-) Bonificações", 0.005],
  ["embalagens", "(-) Embalagens", 0.01],
  ["provisao_perda_estoque", "(-) Provisão para Perda de Estoque", 0.005],
  ["publicidade_propaganda", "(-) Publicidade e Propaganda", 0.02],
  ["comissoes", "(-) Comissões", 0.125],
  ["agencia_growth", "(-) Agência de Growth", 0.01],
  ["despesas_gateway", "(-) Gateway", null], // ausente na matriz: nunca zero
  ["brindes", "(-) Brindes", 0.005],
  ["logistica_fretes", "(-) Logística — Fretes", 0.04],
  ["logistica_operador", "(-) Logística — Operador", 0.01],
];

export interface OptsCenario {
  estado: CenarioPremissas["estado"];
  previa?: boolean;
  receita?: number;
  /** `null` = CMV nao publicado (snapshot_ausente). */
  cmv?: number | null;
  margem?: number | null;
  motivos?: string[];
  agregado?: boolean;
}

export function cenarioFx(o: OptsCenario): CenarioPremissas {
  const receita = o.receita ?? 100_000;
  const pct = (v: number | null) => (v === null ? null : Math.round((v / receita) * 10000) / 100);
  const ln = (p: Partial<LinhaCenario> & Pick<LinhaCenario, "linha" | "rotulo">): LinhaCenario => ({
    tipo: "item",
    valor: null,
    origem: "indisponivel",
    percentual: null,
    status: "indisponivel",
    motivo: null,
    somavel: true,
    pct_receita: null,
    ...p,
  });
  const premissa = (linha: string, rotulo: string, taxa: number | null): LinhaCenario => {
    if (taxa === null) {
      return ln({ linha, rotulo, status: "ausente_na_origem", motivo: "premissa ausente na matriz (ausencia nao e' zero)" });
    }
    const valor = Math.round(receita * taxa * 100) / 100;
    const comissao = linha === "comissoes";
    return ln({
      linha,
      rotulo,
      valor,
      origem: "premissa",
      percentual: o.agregado ? null : taxa,
      status: comissao ? "sobreposicao_nao_ratificada" : "ok",
      somavel: !comissao,
      motivo: comissao ? "pode se sobrepor as taxas reais de marketplace ja usadas: fora da margem ate ratificacao" : null,
      pct_receita: pct(valor),
    });
  };
  const nc = o.estado === "nao_calculavel";
  const sub = (linha: string, rotulo: string, v: number) =>
    ln({
      linha,
      rotulo,
      tipo: "subtotal",
      valor: nc ? null : v,
      origem: "calculado",
      status: nc ? "nao_calculavel" : "ok",
      somavel: false,
      pct_receita: nc ? null : pct(v),
    });
  const p = Object.fromEntries(PREMISSAS_FX.map(([k, r, t]) => [k, premissa(k, r, t)])) as Record<string, LinhaCenario>;
  const cmv = o.cmv === undefined ? 30_000 : o.cmv;
  const margem = nc ? null : o.margem ?? 20_000;
  const linhas: LinhaCenario[] = [
    ln({ linha: "receita_bruta", rotulo: "Receita Bruta", valor: receita, origem: "realizado", status: "disponivel", somavel: false, pct_receita: 100 }),
    p.devolucoes_reembolsos,
    sub("impostos_sobre_receita", "(-) Impostos sobre Receita", 15_000),
    p.icms,
    p.difal,
    p.pis,
    p.cofins,
    p.iss_ipi_simples,
    sub("receita_liquida_estimada", "(=) Receita Líquida Estimada", 83_000),
    cmv === null
      ? ln({ linha: "cmv", rotulo: "(-) Custo das Mercadorias", status: "snapshot_ausente", motivo: "competencia nao publicada em marts.fact_dre_insumo_mensal" })
      : ln({ linha: "cmv", rotulo: "(-) Custo das Mercadorias", valor: cmv, origem: "realizado", status: "disponivel", pct_receita: pct(cmv) }),
    p.frete_primario,
    p.bonificacoes,
    p.embalagens,
    p.provisao_perda_estoque,
    sub("lucro_bruto_estimado", "(=) Lucro Bruto Estimado", 50_000),
    ln({ linha: "midia_online", rotulo: "(-) Mídia Online", valor: 3_000, origem: "realizado", status: "disponivel", pct_receita: 3 }),
    ln({ linha: "influencer", rotulo: "(-) Influencer", status: "nao_aplicavel" }),
    p.publicidade_propaganda,
    p.comissoes,
    p.agencia_growth,
    p.despesas_gateway,
    ln({ linha: "despesas_marketplace", rotulo: "(-) Despesas de Marketplace", valor: 12_000, origem: "realizado", status: "disponivel", pct_receita: 12 }),
    p.brindes,
    p.logistica_fretes,
    p.logistica_operador,
    ln({
      linha: "margem_contribuicao_estimada",
      rotulo: "(=) Margem de Contribuição Estimada",
      tipo: "subtotal",
      valor: margem,
      origem: "calculado",
      status: nc ? "nao_calculavel" : "ok",
      somavel: false,
      pct_receita: pct(margem),
    }),
    ln({
      linha: "outras_receitas_operacionais",
      rotulo: "Outras Receitas Operacionais",
      tipo: "informativo",
      valor: 500,
      origem: "premissa",
      percentual: o.agregado ? null : 0.005,
      status: "sinal_nao_ratificado",
      somavel: false,
      motivo: "rotulo de receita, formula de deducao: fora da margem ate ratificacao",
      pct_receita: 0.5,
    }),
    ln({
      linha: "frete_realizado_comparacao",
      rotulo: "Frete realizado (comparação)",
      tipo: "informativo",
      valor: 7_000,
      origem: "realizado",
      status: "disponivel",
      somavel: false,
      pct_receita: 7,
    }),
  ];
  const motivosPadrao = nc
    ? ["cmv snapshot_ausente: competencia nao publicada em marts.fact_dre_insumo_mensal", "previa: competencia em andamento"]
    : o.estado === "parcial"
      ? ["premissa ausente: despesas_gateway", "previa: competencia em andamento"]
      : [];
  return {
    competencia_premissas: "2026-10",
    natureza: "estimada",
    rotulo: "Margem de contribuição estimada com premissas",
    estado: o.estado,
    previa: o.previa ?? false,
    competencia_madura: !(o.previa ?? false),
    receita_base: receita,
    fonte_cmv: "dre_realizada",
    linhas,
    componentes_ausentes: [
      ...(cmv === null ? [{ linha: "cmv", motivo: "snapshot_ausente" }] : []),
      { linha: "despesas_gateway", motivo: "premissa ausente" },
    ],
    motivos: o.motivos ?? motivosPadrao,
    avisos: [
      "Comissoes fora da margem: sobreposicao com as taxas de marketplace nao ratificada.",
      "Outras Receitas Operacionais fora da margem: sinal (receita x deducao) nao ratificado.",
      "Logistica - Fretes vem da matriz; o frete realizado aparece so' como comparacao.",
    ],
    margem_contribuicao_estimada: margem,
    margem_contribuicao_estimada_pct: pct(margem),
    fonte_premissas: {
      rotulo: "Premissas de outubro/2026",
      competencia: "2026-10",
      data_fonte: "2026-10-10",
      source_sha256: "a".repeat(64),
      carregado_em: "2026-10-14T12:00:00+00:00",
    },
    celulas: 1,
  };
}

/** Resposta com cenario em todas as celulas; `premissasEstado` dita a fonte. */
export function respostaEstimada(
  o: OptsCenario,
  premissasEstado: "ok" | "nao_publicado" | "tabela_ausente" = "ok",
): DreResponse {
  const base = resposta({}, "ok");
  const com = (c: CelulaDRE, agregado: boolean, marca?: string): CelulaDRE => ({
    ...c,
    ...(marca ? { marca, marca_label: marca.toUpperCase() } : {}),
    cenario_premissas: premissasEstado === "ok" ? cenarioFx({ ...o, agregado }) : null,
  });
  return {
    ...base,
    fontes: {
      ...base.fontes,
      premissas_dre: {
        estado: premissasEstado,
        competencias_publicadas: premissasEstado === "ok" ? ["2026-10"] : [],
        rotulo: premissasEstado === "ok" ? "Premissas de outubro/2026" : null,
        motivo:
          premissasEstado === "ok"
            ? null
            : premissasEstado === "tabela_ausente"
              ? "marts.fact_dre_premissa_mensal nao existe (migration 026 nao aplicada)"
              : "nenhuma competencia do periodo tem premissa publicada",
      },
    },
    total: base.total ? com(base.total, true) : null,
    por_canal: base.por_canal.map((c) => com(c, true)),
    por_marca: base.por_marca.map((c) => com(c, true)),
    celulas_base: [com(ML, false, "kokeshi"), com(TIKTOK, false, "apice"), com(ML, false, "apice")],
  };
}
