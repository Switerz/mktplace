/**
 * Gate EXPEDICAO-UX-UNIFICADA-1 — o que os tres canais da Expedicao
 * COMPARTILHAM, e so' isso.
 *
 * A tela virou um produto so' (Shopee | Mercado Livre | TikTok Shop), mas os
 * contratos continuam dois: a fila (`expedicao-contract.ts`) e a coorte por
 * vencimento do TikTok (`expedicao-tiktok-contract.ts`). Este modulo NAO
 * conhece metrica nenhuma. Ele guarda:
 *
 *   - a profundidade da tela (`?visao=`): visao do gestor ou detalhe;
 *   - os TIPOS de apresentacao que os dois paineis preenchem cada um com o
 *     proprio contrato — fotografia, barra de qualidade, itens de atencao.
 *
 * Quem decide o que entra em cada tipo e' o contrato do canal. Um componente
 * universal com `if (canal === ...)` espalhado era exatamente o que este gate
 * nao podia criar. O canal da URL mora em `expedicao-canal.ts`.
 *
 * SEM IMPORTS, de proposito: os dois contratos importam daqui, e
 * `expedicao-janela.ts` ja' importa os dois. Qualquer import neste arquivo
 * abriria um ciclo avaliado em tempo de carga.
 */
// ---------------------------------------------------------------------------
// Profundidade — visao do gestor (padrao) ou detalhe operacional
// ---------------------------------------------------------------------------
export const VISOES = ["gestor", "detalhe"] as const;
export type Visao = (typeof VISOES)[number];
export const VISAO_PADRAO: Visao = "gestor";
export const PARAM_VISAO = "visao";

export const ROTULO_VISAO: Record<Visao, string> = {
  gestor: "Visão do gestor",
  detalhe: "Detalhe operacional",
};

/** So' `detalhe` e' lido; qualquer outra coisa abre na visao do gestor. */
export function lerVisao(search: string): Visao {
  return new URLSearchParams(search).get(PARAM_VISAO) === "detalhe" ? "detalhe" : VISAO_PADRAO;
}

/** O padrao nao suja a URL; o detalhe vira link compartilhavel. */
export function urlComVisao(href: string, visao: Visao): string {
  const url = new URL(href);
  if (visao === VISAO_PADRAO) url.searchParams.delete(PARAM_VISAO);
  else url.searchParams.set(PARAM_VISAO, visao);
  return url.toString();
}

// ---------------------------------------------------------------------------
// Tipos de apresentacao que os dois paineis preenchem
// ---------------------------------------------------------------------------

/**
 * Fotografia, frescor e automacao — o cabecalho de todo canal.
 *
 * `idade` vem PRONTA do contrato do canal. Quando a API nao publica a idade
 * (TikTok), fica `null`: a tela nao subtrai relogios para inventar uma.
 */
export interface Fotografia {
  /** "Fotografia" (fila) ou "Série" (TikTok). */
  rotulo: string;
  publicadaEm: string | null;
  idade: string | null;
  /** Decidido pelo contrato do canal (limiar da tela ou alerta da API). */
  desatualizada: boolean;
  /** Texto humano do modo de publicacao, exatamente o que a API declara. */
  automacao: string;
  /** Ultimo dado lido da fonte, quando o payload o traz. */
  fonteLidaAte?: string | null;
}

export type SeveridadeQualidade = "alerta" | "atencao" | "info";

export interface ProblemaQualidade {
  chave: string;
  severidade: SeveridadeQualidade;
  texto: string;
}

export interface BarraQualidade {
  /** O problema que o operador precisa ler primeiro. `null` = tudo em ordem. */
  principal: ProblemaQualidade | null;
  /** Todos, inclusive o principal, para o painel expansivel. */
  detalhes: ProblemaQualidade[];
  severidade: SeveridadeQualidade | null;
}

const PESO_SEVERIDADE: Record<SeveridadeQualidade, number> = {
  alerta: 0,
  atencao: 1,
  info: 2,
};

/**
 * Ordena pela severidade e mantem a ordem de insercao no empate — e' ela que
 * o contrato do canal usa para dizer qual ressalva vem primeiro.
 */
export function consolidarQualidade(itens: ProblemaQualidade[]): BarraQualidade {
  const ordenados = [...itens].sort(
    (a, b) => PESO_SEVERIDADE[a.severidade] - PESO_SEVERIDADE[b.severidade],
  );
  return {
    principal: ordenados[0] ?? null,
    detalhes: ordenados,
    severidade: ordenados[0]?.severidade ?? null,
  };
}

/**
 * Uma linha de "O que exige atenção agora".
 *
 * `bom` existe so' porque a API do TikTok publica um alerta informativo de
 * "dentro da meta". Nenhum contrato pode usar `bom` para dizer "nada a fazer"
 * sobre uma fotografia que pode estar velha.
 */
export type TomAtencao = "critico" | "atencao" | "neutro" | "bom";

export interface ItemAtencao {
  chave: string;
  tom: TomAtencao;
  texto: string;
}
