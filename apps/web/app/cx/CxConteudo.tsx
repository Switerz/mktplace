"use client";

/**
 * Aba CX — o corpo da pagina, buscado PELO NAVEGADOR.
 *
 * Desde o login Google (AUTH-GOOGLE, PR #172) a API e' chamada pela mesma
 * origem (`/torre-api/*`, `NEXT_PUBLIC_API_URL` relativa) para o cookie de
 * sessao chegar a ela. Um fetch no servidor nao resolve esse caminho relativo
 * (04/11/2026: "Não foi possível contatar a API"), por isso a busca e' aqui,
 * com `torreFetch`.
 */
import { useCallback, useEffect, useState } from "react";

import Panel from "@/components/ui/Panel";
import StateBlock from "@/components/ui/StateBlock";
import {
  PROXIMAS_FONTES,
  carregarCxShopee,
  montarChat,
  montarDevolucoes,
  type CelulaChat,
  type FiltrosCx,
  type ResultadoCx,
  type VisaoChat,
  type VisaoDevolucoes,
} from "@/lib/cx";
import { brandLabel } from "@/lib/filters/brands";
import { formatarInstante } from "@/lib/performance-contas";
import { montarCanaisAtendimento, textoJanela } from "@/lib/performance-contas-atendimento";
import { torreFetch } from "@/lib/torre-fetch";

import AtendimentoCanal, { Motivos } from "./AtendimentoCanal";

export default function CxConteudo({ filtros }: { filtros: FiltrosCx }) {
  const { marca, dias } = filtros;
  const [resultado, setResultado] = useState<ResultadoCx | null>(null);

  const carregar = useCallback(() => {
    let vivo = true;
    setResultado(null);
    carregarCxShopee({ marca, dias }, torreFetch as typeof fetch).then((r) => {
      if (vivo) setResultado(r);
    });
    return () => {
      vivo = false;
    };
  }, [marca, dias]);

  useEffect(() => carregar(), [carregar]);

  if (!resultado) {
    return <StateBlock tipo="carregando" titulo="Carregando a aba CX" />;
  }
  if (!resultado.ok) {
    return (
      <StateBlock
        tipo="erro"
        titulo="Não foi possível carregar a aba CX"
        descricao={resultado.motivo}
        acao={
          <button
            type="button"
            onClick={() => carregar()}
            className="mt-1 inline-flex min-h-11 items-center rounded-lg border border-current/30 px-3 text-xs font-semibold hover:bg-surface/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Tentar novamente
          </button>
        }
      />
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
    <>
      <p className="text-xs text-ink-muted" data-testid="cx-escopo">{escopo}</p>
      <Chat v={chat} />
      <Devolucoes v={devolucoes} />
      {atendimento ? <AtendimentoCanal canal={{ ...atendimento, titulo: "Cancelamentos e devoluções", pendencias: [] }} /> : null}
      <ProximasFontes />
    </>
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
          <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              ["Pedidos de devolução", v.total],
              ["Aceitas", v.aceitas],
              ["Em andamento", v.emAndamento],
              ["Reembolsado (aceitas)", v.reembolsado],
            ].map(([rotulo, valor]) => (
              <div key={rotulo} className="rounded-lg border border-line bg-surface p-3">
                <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{rotulo}</dt>
                <dd className="mt-1 text-2xl font-semibold tabular-nums text-ink">{valor}</dd>
              </div>
            ))}
          </dl>
          <p className="-mt-2 text-xs text-ink-faint">{v.canceladas} pedido(s) cancelado(s) pelo próprio comprador.</p>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {v.motivos && <Motivos m={v.motivos} cor="bg-canal-shopee" />}
            <section aria-label="Devoluções por marca" className="flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-surface p-3">
              <h3 className="text-sm font-semibold text-ink">Por marca</h3>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs font-semibold text-ink-faint">
                      <th scope="col" className="py-2 pr-3">Marca</th>
                      <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Pedidos</th>
                      <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Aceitas</th>
                      <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Em andamento</th>
                      <th scope="col" className="whitespace-nowrap px-2 py-2 text-right">Reembolsado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.linhas.map((l) => (
                      <tr key={l.chave} className="border-b border-line last:border-b-0">
                        <th scope="row" className="py-2 pr-3 text-left font-medium text-ink">{l.marca}</th>
                        <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink-muted">{l.devolucoes}</td>
                        <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink-muted">{l.aceitas}</td>
                        <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink-muted">{l.emAndamento}</td>
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
