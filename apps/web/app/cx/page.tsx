/**
 * Aba CX (menu principal, 04/11/2026) — atendimento ao cliente nos
 * marketplaces. Primeira versao: so' Shopee.
 *
 * Ordem de leitura: chat (Saude da Conta, oficial e com a meta da Shopee) →
 * cancelamentos e devolucoes (pedidos) → o que ainda falta, dito na tela para
 * a ausencia nunca parecer zero.
 *
 * Server component: le a API uma vez (`/api/v1/cx/shopee`) e so' desenha a
 * visao montada por funcoes puras (`src/lib/cx.ts`, testadas em node --test).
 * Nenhum dado demonstrativo: API fora ou payload invalido = erro na tela.
 */
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import Panel from "@/components/ui/Panel";
import StateBlock from "@/components/ui/StateBlock";
import {
  JANELAS_DIAS,
  PROXIMAS_FONTES,
  carregarCxShopee,
  hrefCx,
  lerFiltrosCx,
  montarChat,
  montarDevolucoes,
  type CelulaChat,
  type FiltrosCx,
  type VisaoChat,
  type VisaoDevolucoes,
} from "@/lib/cx";
import { ALL_BRAND_KEYS, ALL_BRAND_OPTIONS, brandLabel } from "@/lib/filters/brands";
import { formatarInstante } from "@/lib/performance-contas";
import { montarCanaisAtendimento, textoJanela } from "@/lib/performance-contas-atendimento";

import AtendimentoCanal, { Motivos } from "./AtendimentoCanal";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function CxPage({ searchParams }: { searchParams: SearchParams }) {
  const filtros = lerFiltrosCx(await searchParams, ALL_BRAND_KEYS);
  const resultado = await carregarCxShopee(filtros);

  if (!resultado.ok) {
    return (
      <PageContainer>
        <PageHeader title="CX" subtitle="Atendimento ao cliente · Shopee" filters={<Filtros f={filtros} />} />
        <StateBlock
          tipo="erro"
          titulo="Não foi possível carregar a aba CX"
          descricao={resultado.motivo}
          acao={
            <a
              href={hrefCx(filtros)}
              className="mt-1 inline-flex min-h-11 items-center rounded-lg border border-current/30 px-3 text-xs font-semibold hover:bg-surface/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Tentar novamente
            </a>
          }
        />
      </PageContainer>
    );
  }

  const { dados } = resultado;
  const chat = montarChat(dados.chat, brandLabel);
  const devolucoes = montarDevolucoes(dados.devolucoes, brandLabel);
  const [atendimento] = montarCanaisAtendimento(
    { gerado_em: dados.gerado_em, janela: dados.janela, canais: [dados.atendimento] },
    null,
    brandLabel,
  );
  const escopo = `${textoJanela(dados.janela)} · gerado em ${formatarInstante(dados.gerado_em) ?? dados.gerado_em}`;

  return (
    <PageContainer>
      <PageHeader
        title="CX"
        subtitle="Atendimento ao cliente · Shopee"
        scopeLine={escopo}
        filters={<Filtros f={filtros} />}
      />
      <Chat v={chat} />
      <Devolucoes v={devolucoes} />
      {atendimento ? <AtendimentoCanal canal={{ ...atendimento, titulo: "Cancelamentos e devoluções", pendencias: [] }} /> : null}
      <ProximasFontes />
    </PageContainer>
  );
}

/** Filtro por formulario GET: funciona sem JS e mantem a URL como fonte de verdade. */
function Filtros({ f }: { f: FiltrosCx }) {
  const campo =
    "min-h-11 rounded-md border border-line bg-surface px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
  return (
    <form method="get" action="/cx" className="flex flex-wrap items-end gap-3" data-testid="cx-filtros">
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Marca
        <select name="brands" defaultValue={f.marca ?? ""} className={campo}>
          <option value="">Todas as marcas</option>
          {ALL_BRAND_OPTIONS.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Período
        <select name="dias" defaultValue={String(f.dias)} className={campo}>
          {JANELAS_DIAS.map((d) => (
            <option key={d} value={d}>
              Últimos {d} dias
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className="min-h-11 rounded-md border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Aplicar
      </button>
    </form>
  );
}

/** Classes LITERAIS: o Tailwind so' gera o que encontra escrito. */
const COR_SITUACAO: Record<string, string> = {
  ok: "text-ok-ink",
  fora: "rounded-md bg-crit-soft px-1.5 py-0.5 font-bold text-crit-ink ring-1 ring-crit/30",
};

function Chat({ v }: { v: VisaoChat }) {
  return (
    <Panel titulo="Chat" descricao="Saúde da Conta da Shopee · fotografia diária oficial" aria-label="Chat">
      {v.estado !== "dado" ? (
        <StateBlock
          tipo={v.estado === "erro" ? "erro" : "indisponivel"}
          compacto
          titulo={v.estado === "erro" ? "Saúde da Conta indisponível nesta carga" : "Sem fonte"}
          descricao={v.motivo ?? undefined}
        />
      ) : v.linhas.length === 0 ? (
        <StateBlock tipo="vazio" compacto titulo="Nenhuma loja Shopee no filtro" />
      ) : (
        <div className="flex flex-col gap-3" data-testid="cx-chat">
          {v.foraDaMeta.length > 0 && (
            <p className="text-sm text-crit-ink" data-testid="cx-chat-alerta">
              Fora da meta da Shopee: <span className="font-semibold">{v.foraDaMeta.join(", ")}</span>
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Métricas de atendimento por loja Shopee</caption>
              <thead>
                <tr className="border-b border-line text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
                  <th scope="col" className="py-2 pr-3">Marca</th>
                  {v.colunas.map((c) => (
                    <th key={c.codigo} scope="col" className="whitespace-nowrap px-2 py-2 text-right">
                      {c.rotulo}
                    </th>
                  ))}
                  {v.comNaoLidas && (
                    <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Conversas não lidas</th>
                  )}
                  <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Foto de</th>
                </tr>
              </thead>
              <tbody>
                {v.linhas.map((l) => (
                  <tr key={l.marca} className="border-b border-line last:border-b-0">
                    <th scope="row" className="py-2 pr-3 text-left font-semibold text-ink">{l.marca}</th>
                    {l.celulas.map((c, i) => (
                      <td key={v.colunas[i].codigo} className="whitespace-nowrap px-2 py-2 text-right tabular-nums">
                        <Celula c={c} />
                      </td>
                    ))}
                    {v.comNaoLidas && (
                      <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink">{l.naoLidas}</td>
                    )}
                    <td className="whitespace-nowrap px-2 py-2 text-right text-xs tabular-nums text-ink-faint">{l.dia}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {v.motivoNaoLidas && <p className="text-xs text-ink-faint">{v.motivoNaoLidas}</p>}
          {v.semFoto.length > 0 && (
            <ul className="text-xs text-ink-faint">
              {v.semFoto.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}

function Devolucoes({ v }: { v: VisaoDevolucoes }) {
  return (
    <Panel titulo="Devoluções e reembolsos" descricao="Criadas no período · API de devoluções da Shopee" aria-label="Devoluções">
      {v.estado !== "dado" ? (
        <StateBlock
          tipo={v.estado === "erro" ? "erro" : v.estado === "vazio_legitimo" ? "vazio" : "indisponivel"}
          compacto
          titulo={v.estado === "erro" ? "Devoluções indisponíveis nesta carga" : v.estado === "vazio_legitimo" ? "Nenhuma loja Shopee no filtro" : "Ainda sem dados"}
          descricao={v.motivo ?? undefined}
        />
      ) : (
        <div className="flex flex-col gap-4" data-testid="cx-devolucoes">
          <dl className="grid grid-cols-2 gap-3 sm:max-w-md">
            <div className="rounded-lg border border-line bg-surface p-3">
              <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Devoluções</dt>
              <dd className="mt-1 text-2xl font-semibold tabular-nums text-ink">{v.total}</dd>
            </div>
            <div className="rounded-lg border border-line bg-surface p-3">
              <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Valor reembolsado</dt>
              <dd className="mt-1 text-2xl font-semibold tabular-nums text-ink">{v.reembolsado}</dd>
            </div>
          </dl>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {v.motivos && <Motivos m={v.motivos} cor="bg-canal-shopee" />}
            <section aria-label="Devoluções por marca" className="flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-surface p-3">
              <h3 className="text-sm font-semibold text-ink">Por marca</h3>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs font-semibold text-ink-faint">
                      <th scope="col" className="py-2 pr-3">Marca</th>
                      <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Devoluções</th>
                      <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Reembolsado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.linhas.map((l) => (
                      <tr key={l.chave} className="border-b border-line last:border-b-0">
                        <th scope="row" className="py-2 pr-3 text-left font-medium text-ink">{l.marca}</th>
                        <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink-muted">{l.devolucoes}</td>
                        <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink-muted">{l.reembolsado}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </div>
      )}
    </Panel>
  );
}

function Celula({ c }: { c: CelulaChat | null }) {
  if (!c) return <span className="text-ink-faint">—</span>;
  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <span className={c.situacao ? COR_SITUACAO[c.situacao] ?? "text-ink" : "text-ink"}>{c.valor}</span>
      <span className="text-xs font-normal text-ink-faint">
        {[c.meta, c.anterior ? `antes ${c.anterior}` : null].filter(Boolean).join(" · ")}
      </span>
    </span>
  );
}

function ProximasFontes() {
  return (
    <section aria-label="Ainda não publicado" className="rounded-lg border border-dashed border-line px-3 py-2.5">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Ainda não publicado</h2>
      <ul className="mt-2 flex flex-col gap-1.5 text-xs text-ink-muted">
        {PROXIMAS_FONTES.map((p) => (
          <li key={p.titulo}>
            <span className="font-semibold text-ink">{p.titulo}.</span> {p.motivo}
          </li>
        ))}
      </ul>
    </section>
  );
}
