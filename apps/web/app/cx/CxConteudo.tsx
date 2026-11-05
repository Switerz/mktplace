"use client";

/**
 * Aba CX — o corpo da pagina, buscado PELO NAVEGADOR.
 *
 * Desde o login Google (AUTH-GOOGLE, PR #172) a API e' chamada pela mesma
 * origem (`/torre-api/*`, `NEXT_PUBLIC_API_URL` relativa) para o cookie de
 * sessao chegar a ela. Um fetch no servidor nao resolve esse caminho relativo
 * (04/11/2026: "Não foi possível contatar a API"), por isso a busca e' aqui,
 * com `torreFetch`.
 *
 * Dois blocos, um por entrega da planilha de CX (04/11/2026):
 *   1. Atendimento — nº de chats, em aberto, % respondido, tempo de resposta,
 *      contact rate;
 *   2. Pareto dos problemas — motivos + quanto reembolsamos, reenviamos e
 *      cancelamos.
 * O que falta vira uma nota curta no rodape do bloco (ausencia nunca e' zero).
 */
import { useCallback, useEffect, useState } from "react";

import Panel from "@/components/ui/Panel";
import StateBlock from "@/components/ui/StateBlock";
import {
  carregarCxShopee,
  montarAtendimentoCx,
  montarPareto,
  type CelulaMeta,
  type FiltrosCx,
  type ResultadoCx,
  type VisaoAtendimentoCx,
  type VisaoPareto,
} from "@/lib/cx";
import { brandLabel } from "@/lib/filters/brands";
import { formatarInstante } from "@/lib/performance-contas";
import { textoJanela } from "@/lib/performance-contas-atendimento";
import { torreFetch } from "@/lib/torre-fetch";

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
  const escopo = `${textoJanela(dados.janela)} · gerado em ${formatarInstante(dados.gerado_em) ?? dados.gerado_em}`;

  return (
    <>
      <p className="text-xs text-ink-muted" data-testid="cx-escopo">{escopo}</p>
      <Atendimento v={montarAtendimentoCx(dados, brandLabel)} />
      <Pareto v={montarPareto(dados, brandLabel)} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Pecas
// ---------------------------------------------------------------------------
function Numeros({ itens }: { itens: [string, string][] }) {
  return (
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {itens.map(([rotulo, valor]) => (
        <div key={rotulo} className="rounded-lg border border-line bg-surface p-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{rotulo}</dt>
          <dd className="mt-1 break-words text-2xl font-semibold tabular-nums text-ink">{valor}</dd>
        </div>
      ))}
    </dl>
  );
}

function Notas({ notas }: { notas: string[] }) {
  if (!notas.length) return null;
  return (
    <ul className="flex flex-col gap-0.5 text-xs text-ink-faint" data-testid="cx-notas">
      {notas.map((n) => (
        <li key={n}>{n}</li>
      ))}
    </ul>
  );
}

const TH = "whitespace-nowrap px-2 py-2 text-right";
const TD = "whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink-muted";

/** Valor oficial com a meta da Shopee; fora da meta em vermelho. */
function ComMeta({ c }: { c: CelulaMeta | null }) {
  if (!c) return <span className="text-ink-faint">—</span>;
  return (
    <span
      title={c.meta ?? undefined}
      className={c.fora ? "rounded-md bg-crit-soft px-1.5 py-0.5 font-bold text-crit-ink ring-1 ring-crit/30" : "text-ink"}
    >
      {c.valor}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 1. Atendimento
// ---------------------------------------------------------------------------
function Atendimento({ v }: { v: VisaoAtendimentoCx }) {
  return (
    <Panel titulo="Atendimento" descricao="Chat da Shopee por loja" aria-label="Atendimento">
      {v.estado !== "dado" ? (
        <StateBlock
          tipo={v.estado === "erro" ? "erro" : "indisponivel"}
          compacto
          titulo={v.estado === "erro" ? "Atendimento indisponível nesta carga" : "Sem dados de atendimento"}
          descricao={v.motivo ?? undefined}
        />
      ) : (
        <div className="flex flex-col gap-4" data-testid="cx-atendimento">
          <Numeros
            itens={[
              [v.rotuloConversas, v.totais.conversas],
              ["Aguardando resposta", v.totais.aguardando],
              ["Não lidas agora", v.totais.naoLidas],
              ["Lojas fora da meta", v.totais.foraDaMeta.length ? v.totais.foraDaMeta.join(", ") : "Nenhuma"],
            ]}
          />
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Atendimento do chat por loja Shopee</caption>
              <thead>
                <tr className="border-b border-line text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
                  <th scope="col" className="py-2 pr-3">Marca</th>
                  <th scope="col" className={TH} title={v.rotuloConversas}>Conversas</th>
                  <th scope="col" className={TH} title="Última mensagem é do comprador">Aguardando</th>
                  <th scope="col" className={TH}>Não lidas</th>
                  <th scope="col" className={TH}>% respondido</th>
                  <th scope="col" className={TH}>Nota</th>
                  <th scope="col" className={TH}>Tempo resp.</th>
                  <th scope="col" className={TH}>Contact rate</th>
                </tr>
              </thead>
              <tbody>
                {v.linhas.map((l) => (
                  <tr key={l.chave} className="border-b border-line last:border-b-0">
                    <th scope="row" className="py-2 pr-3 text-left font-semibold text-ink">{l.marca}</th>
                    <td className={TD}>{l.conversas}</td>
                    <td className={TD}>{l.aguardando}</td>
                    <td className={TD}>{l.naoLidas}</td>
                    <td className={TD}><ComMeta c={l.respondido} /></td>
                    <td className={TD}><ComMeta c={l.nota} /></td>
                    <td className={`${TD} text-ink-faint`}>—</td>
                    <td className={TD}>{l.contactRate}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Notas notas={["Aguardando = a última mensagem é do comprador. Contact rate = conversas ÷ pedidos do período.", ...v.notas]} />
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// 2. Pareto dos problemas
// ---------------------------------------------------------------------------
/** Classes LITERAIS: o Tailwind so' gera o que encontra escrito. */
const COR_TIPO: Record<string, string> = {
  "Devolução": "bg-canal-shopee",
  Cancelamento: "bg-accent",
};

function Pareto({ v }: { v: VisaoPareto }) {
  return (
    <Panel titulo="Pareto dos problemas" descricao="Devoluções e cancelamentos por motivo" aria-label="Pareto dos problemas">
      <div className="flex flex-col gap-4" data-testid="cx-pareto">
        <Numeros
          itens={[
            ["Reembolsado", v.totais.reembolsado],
            ["Devoluções", v.totais.devolucoes],
            ["Cancelamentos", v.totais.cancelamentos],
            ["Reenviados", v.totais.reenviados],
          ]}
        />
        {v.estado !== "dado" ? (
          <StateBlock
            tipo={v.estado === "erro" ? "erro" : v.estado === "vazio_legitimo" ? "vazio" : "indisponivel"}
            compacto
            titulo={v.motivo ?? "Sem dados"}
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
            <ol className="flex min-w-0 flex-col gap-2.5 xl:col-span-3" aria-label="Motivos, do maior para o menor">
              {v.itens.map((i) => (
                <li key={i.chave} className="flex flex-col gap-1">
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="min-w-0 break-words text-ink">
                      {i.rotulo}
                      {!i.mapeado && <span className="ml-1.5 text-xs text-warn-ink">(sem tradução)</span>}
                      <span className="ml-1.5 text-xs text-ink-faint">{i.tipo} · {i.natureza}</span>
                    </span>
                    <span className="shrink-0 tabular-nums text-ink-muted">
                      {i.quantidade} <span className="text-xs">· {i.pct} · acum. {i.acumulado}</span>
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-raised" aria-hidden="true">
                    <div className={`h-full rounded-full ${COR_TIPO[i.tipo] ?? "bg-accent"}`} style={{ width: `${i.largura}%` }} />
                  </div>
                </li>
              ))}
              {v.resto && <li className="text-xs text-ink-faint">{v.resto}</li>}
            </ol>
            <div className="min-w-0 overflow-x-auto xl:col-span-2">
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">Problemas por marca</caption>
                <thead>
                  <tr className="border-b border-line text-left text-xs font-semibold text-ink-faint">
                    <th scope="col" className="py-2 pr-3">Marca</th>
                    <th scope="col" className={TH}>Pedidos</th>
                    <th scope="col" className={TH}>Cancel.</th>
                    <th scope="col" className={TH}>Devol.</th>
                    <th scope="col" className={TH}>Reembolsado</th>
                  </tr>
                </thead>
                <tbody>
                  {v.linhas.map((l) => (
                    <tr key={l.chave} className="border-b border-line last:border-b-0">
                      <th scope="row" className="py-2 pr-3 text-left font-medium text-ink">{l.marca}</th>
                      <td className={TD}>{l.pedidos}</td>
                      <td className={TD}>{l.cancelamentos}</td>
                      <td className={TD}>{l.devolucoes}</td>
                      <td className={TD}>{l.reembolsado}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <Notas notas={v.notas} />
      </div>
    </Panel>
  );
}
