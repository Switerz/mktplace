/**
 * Aba CX (menu principal, 04/11/2026) — atendimento ao cliente nos
 * marketplaces. Primeira versao: so' Shopee.
 *
 * Ordem de leitura: chat (Saude da Conta, oficial e com a meta da Shopee) →
 * devolucoes (coleta propria) → cancelamentos → o que ainda falta, dito na
 * tela para a ausencia nunca parecer zero.
 *
 * O servidor so' le os filtros da URL e desenha cabecalho e formulario; os
 * dados sao buscados pelo navegador em `CxConteudo` (a API vive atras do
 * login, via `/torre-api`). Nenhum dado demonstrativo.
 */
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { lerFiltrosCx } from "@/lib/cx";
import { ALL_BRAND_KEYS } from "@/lib/filters/brands";

import CxConteudo from "./CxConteudo";
import FiltrosCx from "./FiltrosCx";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function CxPage({ searchParams }: { searchParams: SearchParams }) {
  const filtros = lerFiltrosCx(await searchParams, ALL_BRAND_KEYS);
  return (
    <PageContainer>
      <PageHeader title="CX" subtitle="Atendimento ao cliente · Shopee" filters={<FiltrosCx f={filtros} />} />
      <CxConteudo filtros={filtros} />
    </PageContainer>
  );
}
