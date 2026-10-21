/**
 * Dados FICTICIOS da aba "B2B / Revendedores" — so' para o modo `fixture`.
 *
 * Nenhum numero aqui veio da Torre B2B. Os valores sao inventados, mas
 * COERENTES entre si (violando <= anuncios, cobertura <= total, Amazon fora
 * dos KPIs) — `tests/b2b-revenda.test.ts` trava essas invariantes, para que o
 * QA visual nao valide uma tela sobre um payload impossivel.
 */
import type {
  CanaisMarcasOk,
  Escopo,
  Frescor,
  FrescorOk,
  Indisponivel,
  PainelB2B,
  ResumoOk,
  SkusOk,
} from "./b2b-revenda.ts";

const LIMITACOES = [
  "Violação, desvio e PMA vêm prontos do relatório do OctaPrice; a Torre não aplica tolerância.",
  "Amazon fora dos KPIs enquanto a deduplicação da fonte estiver incorreta.",
  "Seller é identificado pelo nome normalizado: nomes iguais em canais diferentes contam como um.",
  "Cliente B2B depende da identificação feita na Torre B2B; seller sem vínculo não tem cliente.",
  "Histórico limitado à retenção da fonte (cerca de 90 dias).",
  "Horário da coleta é exibido como a fonte o gravou, sem fuso declarado no relatório.",
];

export const FIXTURE_FRESCOR: Frescor = {
  data_ref_mais_recente: "2026-10-21",
  coleta_inicio: "2026-10-20T19:03:00+00:00",
  coleta_fim: "2026-10-20T19:21:00+00:00",
  importado_inicio: "2026-10-21T11:41:00+00:00",
  importado_fim: "2026-10-21T11:42:00+00:00",
  linhas_na_carga: 1200,
  situacao: "em_dia",
  data_ref_esperada: "2026-10-21",
  dias_de_atraso: 0,
  consultado_em: "2026-10-21T15:00:00+00:00",
  datas_disponiveis: ["2026-10-21", "2026-10-20", "2026-10-19"],
};

export const FIXTURE_ESCOPO: Escopo = {
  piso: "pma_octaprice",
  aviso_piso:
    "Não é o preço sugerido de revenda (PDV) usado na visão Lojas próprias, e os dois não se comparam.",
  canais_nos_kpis: ["Mercadolivre", "Shopee"],
  canais_excluidos: [
    {
      canal: "Amazon",
      motivo:
        "Fora dos KPIs: a deduplicação da fonte guarda um vendedor por anúncio e descarta os demais vendedores do mesmo anúncio de catálogo.",
    },
  ],
};

function base(dataRef: string) {
  return {
    status: "ok" as const,
    data_ref: dataRef,
    consulta_historica: dataRef !== FIXTURE_FRESCOR.data_ref_mais_recente,
    frescor: FIXTURE_FRESCOR,
    escopo: FIXTURE_ESCOPO,
    filtros: {
      data_ref: dataRef, marcas: [], canais: ["Mercadolivre", "Shopee"],
      severidades: [], skus: [], ufs: [],
    },
    limitacoes: LIMITACOES,
  };
}

export const FIXTURE_FRESCOR_OK: FrescorOk = {
  status: "ok", frescor: FIXTURE_FRESCOR, escopo: FIXTURE_ESCOPO, limitacoes: LIMITACOES,
};

export function fixtureResumo(dataRef = FIXTURE_FRESCOR.data_ref_mais_recente): ResumoOk {
  return {
    ...base(dataRef),
    kpis: {
      anuncios: 1000,
      anuncios_violando: 240,
      pct_violacao: "24.0",
      sellers_violando: 60,
      skus_monitorados: 40,
      desvio_medio_pma: "-0.090",
      buy_box: { anuncios_com_buy_box: 300, violando_com_buy_box: 45 },
      severidade: { leve: 120, media: 90, grave: 30 },
    },
    cobertura_identificacao: {
      sellers_violando: 60,
      sellers_violando_identificados: 18,
      pct_sellers: "30.0",
      anuncios_violando: 240,
      anuncios_violando_identificados: 96,
      pct_anuncios: "40.0",
      consistente: true,
    },
  };
}

export function fixtureCanaisMarcas(dataRef = FIXTURE_FRESCOR.data_ref_mais_recente): CanaisMarcasOk {
  return {
    ...base(dataRef),
    linhas: [
      { marca_id: "AP", marca: "Ápice", canal: "Mercadolivre", anuncios: 400, anuncios_violando: 100, pct_violacao: "25.0", desvio_medio_pma: "-0.080", preco_medio: "59.90" },
      { marca_id: "AP", marca: "Ápice", canal: "Shopee", anuncios: 150, anuncios_violando: 45, pct_violacao: "30.0", desvio_medio_pma: "-0.110", preco_medio: "54.90" },
      { marca_id: "BB", marca: "Barbours", canal: "Mercadolivre", anuncios: 250, anuncios_violando: 50, pct_violacao: "20.0", desvio_medio_pma: "-0.070", preco_medio: "39.90" },
      { marca_id: "KS", marca: "Kokeshi", canal: "Shopee", anuncios: 190, anuncios_violando: 43, pct_violacao: "22.6", desvio_medio_pma: "-0.100", preco_medio: "29.90" },
      { marca_id: null, marca: "Marca não mapeada", canal: "Shopee", anuncios: 10, anuncios_violando: 2, pct_violacao: "20.0", desvio_medio_pma: "-0.040", preco_medio: "19.90" },
    ],
  };
}

export function fixtureSkus(dataRef = FIXTURE_FRESCOR.data_ref_mais_recente): SkusOk {
  return {
    ...base(dataRef),
    limite: 10,
    linhas: [
      { sku: "FIC-0001", marca_id: "AP", marca: "Ápice", exemplo_anuncio: "Produto fictício A 300 ml", anuncios: 60, anuncios_violando: 30, pct_violacao: "50.0", desvio_medio_pma: "-0.120" },
      { sku: "FIC-0002", marca_id: "KS", marca: "Kokeshi", exemplo_anuncio: "Produto fictício B", anuncios: 45, anuncios_violando: 18, pct_violacao: "40.0", desvio_medio_pma: "-0.060" },
      { sku: "FIC-0003", marca_id: "BB", marca: "Barbours", exemplo_anuncio: "Produto fictício C kit", anuncios: 30, anuncios_violando: 9, pct_violacao: "30.0", desvio_medio_pma: "-0.030" },
    ],
  };
}

/** Indisponivel de exemplo (para o QA ver o bloco). */
export const FIXTURE_INDISPONIVEL: Indisponivel = {
  status: "unavailable",
  motivo_tecnico: "credencial_ausente",
  unavailable_reason: "A credencial de leitura da Torre B2B ainda não foi provisionada.",
  frescor: null,
  limitacoes: LIMITACOES,
};

/**
 * Painel ficticio. Um dia fora de `datas_disponiveis` devolve `unavailable`
 * (sem_relatorio_na_data), como a API faz — o QA ve o estado de verdade.
 */
export function painelFicticio(dataRef: string): PainelB2B {
  const dia = dataRef || FIXTURE_FRESCOR.data_ref_mais_recente;
  if (!FIXTURE_FRESCOR.datas_disponiveis.includes(dia)) {
    const semRelatorio: Indisponivel = {
      status: "unavailable",
      motivo_tecnico: "sem_relatorio_na_data",
      unavailable_reason: "Não há relatório do OctaPrice para o dia pedido.",
      frescor: FIXTURE_FRESCOR,
      limitacoes: LIMITACOES,
    };
    return { frescor: FIXTURE_FRESCOR_OK, resumo: semRelatorio, canaisMarcas: semRelatorio, skus: semRelatorio };
  }
  return {
    frescor: FIXTURE_FRESCOR_OK,
    resumo: fixtureResumo(dia),
    canaisMarcas: fixtureCanaisMarcas(dia),
    skus: fixtureSkus(dia),
  };
}
