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
import { useCallback, useEffect, useRef, useState } from "react";

import Chip from "@/components/ui/Chip";
import InfoDica from "@/components/ui/InfoDica";
import Panel from "@/components/ui/Panel";
import StateBlock from "@/components/ui/StateBlock";
import {
  AJUDA_CX,
  carregarCxShopee,
  carregarPedidosMotivo,
  csvPedidosMotivo,
  montarPedidosMotivo,
  nomeDoCsvPedidos,
  tipoDoItem,
  type ItemPareto,
  type LinhaFilaCx,
  type ResultadoPedidos,
  montarAtendimentoCx,
  montarPareto,
  montarStatusMarcas,
  type CelulaMeta,
  type StatusMarca,
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
      {!marca && <StatusMarcas itens={montarStatusMarcas(dados, brandLabel)} />}
      <Atendimento v={montarAtendimentoCx(dados, brandLabel)} />
      <Pareto v={montarPareto(dados, brandLabel)} filtros={filtros} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Pecas
// ---------------------------------------------------------------------------
/** [rotulo, valor, ajuda do "ⓘ"] */
function Numeros({ itens }: { itens: [string, string, string?][] }) {
  return (
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {itens.map(([rotulo, valor, ajuda]) => (
        <div key={rotulo} className="rounded-lg border border-line bg-surface p-3">
          <dt className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            {rotulo}
            {ajuda && <InfoDica texto={ajuda} titulo={rotulo} />}
          </dt>
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

/** Cabecalho de coluna com "ⓘ". */
function ThAjuda({ children, ajuda }: { children: string; ajuda: string }) {
  return (
    <th scope="col" className={TH}>
      <span className="inline-flex items-center justify-end gap-1">
        {children}
        <InfoDica texto={ajuda} titulo={children} />
      </span>
    </th>
  );
}
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
              [v.rotuloConversas, v.totais.conversas, AJUDA_CX.conversas],
              ["Aguardando resposta", v.totais.aguardando, AJUDA_CX.aguardando],
              ["Esperando há mais de 24 h", v.totais.mais24h, AJUDA_CX.mais24h],
              ["Lojas fora da meta", v.totais.foraDaMeta.length ? v.totais.foraDaMeta.join(", ") : "Nenhuma"],
            ]}
          />
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Atendimento do chat por loja Shopee</caption>
              <thead>
                <tr className="border-b border-line text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
                  <th scope="col" className="py-2 pr-3">Marca</th>
                  <ThAjuda ajuda={AJUDA_CX.conversas}>Conversas</ThAjuda>
                  <ThAjuda ajuda={AJUDA_CX.aguardando}>Aguardando</ThAjuda>
                  <ThAjuda ajuda={AJUDA_CX.naoLidas}>Não lidas</ThAjuda>
                  <th scope="col" className={TH}>% respondido</th>
                  <th scope="col" className={TH}>Nota</th>
                  <ThAjuda ajuda={AJUDA_CX.tempoResposta}>Tempo resp.</ThAjuda>
                  <ThAjuda ajuda={AJUDA_CX.contactRate}>Contact rate</ThAjuda>
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
                    <td className={`${TD} ${l.tempoResposta === "—" ? "text-ink-faint" : ""}`} title={l.tempoDetalhe ?? undefined}>
                      {l.tempoResposta}
                    </td>
                    <td className={TD}>{l.contactRate}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {v.fila.length > 0 && <FilaChat linhas={v.fila} />}
          <Notas notas={v.notas} />
        </div>
      )}
    </Panel>
  );
}

function FilaChat({ linhas }: { linhas: LinhaFilaCx[] }) {
  return (
    <section aria-labelledby="cx-fila-titulo" className="flex flex-col gap-2" data-testid="cx-fila">
      <h3 id="cx-fila-titulo" className="flex items-center gap-1 text-sm font-semibold text-ink">
        Fila do chat: há quanto tempo o comprador espera
        <InfoDica texto={AJUDA_CX.fila} titulo="Fila do chat" />
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Conversas aguardando resposta por tempo de espera</caption>
          <thead>
            <tr className="border-b border-line text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
              <th scope="col" className="py-2 pr-3">Marca</th>
              <th scope="col" className={TH}>Até 1 h</th>
              <th scope="col" className={TH}>1 a 4 h</th>
              <th scope="col" className={TH}>4 a 24 h</th>
              <th scope="col" className={TH}>Mais de 24 h</th>
              <th scope="col" className={TH}>Mais antiga</th>
            </tr>
          </thead>
          <tbody>
            {linhas.map((l) => (
              <tr key={l.chave} className="border-b border-line last:border-b-0">
                <th scope="row" className="py-2 pr-3 text-left font-semibold text-ink">{l.marca}</th>
                <td className={TD}>{l.ate1h}</td>
                <td className={TD}>{l.de1a4h}</td>
                <td className={TD}>{l.de4a24h}</td>
                <td className={`${TD} ${l.atrasada ? "font-semibold text-crit-ink" : ""}`}>{l.mais24h}</td>
                <td className={TD}>{l.maisAntiga}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
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

function Pareto({ v, filtros }: { v: VisaoPareto; filtros: FiltrosCx }) {
  const [aberto, setAberto] = useState<ItemPareto | null>(null);
  // trocar de filtro fecha a lista (ela seria de outro recorte)
  useEffect(() => {
    setAberto(null);
  }, [filtros.marca, filtros.dias]);
  return (
    <Panel titulo="Pareto dos problemas" descricao="Devoluções e cancelamentos por motivo" aria-label="Pareto dos problemas">
      <div className="flex flex-col gap-4" data-testid="cx-pareto">
        <Numeros
          itens={[
            ["Reembolsado", v.totais.reembolsado],
            ["Devoluções", v.totais.devolucoes, AJUDA_CX.devolucoes],
            ["Cancelamentos", v.totais.cancelamentos, AJUDA_CX.cancelamentos],
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
                <li key={i.chave}>
                  <button
                    type="button"
                    onClick={() => setAberto(aberto?.chave === i.chave ? null : i)}
                    aria-expanded={aberto?.chave === i.chave}
                    aria-controls="cx-pedidos-motivo"
                    title="Ver os pedidos deste motivo"
                    className={`flex w-full flex-col gap-1 rounded-md px-1.5 py-1 text-left hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                      aberto?.chave === i.chave ? "bg-raised ring-1 ring-line-strong" : ""
                    }`}
                  >
                  <div className="flex w-full items-baseline justify-between gap-3 text-sm">
                    <span className="min-w-0 break-words text-ink">
                      {i.rotulo}
                      {!i.mapeado && <span className="ml-1.5 text-xs text-warn-ink">(sem tradução)</span>}
                      <span className="ml-1.5 text-xs text-ink-faint">{i.tipo} · {i.natureza}</span>
                    </span>
                    <span className="shrink-0 tabular-nums text-ink-muted">
                      {i.quantidade} <span className="text-xs">· {i.pct} · acum. {i.acumulado}</span>
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-raised" aria-hidden="true">
                    <div className={`h-full rounded-full ${COR_TIPO[i.tipo] ?? "bg-accent"}`} style={{ width: `${i.largura}%` }} />
                  </div>
                  </button>
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
                    <ThAjuda ajuda={AJUDA_CX.cancelamentos}>Cancel.</ThAjuda>
                    <ThAjuda ajuda={AJUDA_CX.devolucoes}>Devol.</ThAjuda>
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
        {aberto && <PedidosDoMotivo item={aberto} filtros={filtros} fechar={() => setAberto(null)} />}
        <Notas notas={v.notas} />
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Pedidos de um motivo (clique no Pareto)
// ---------------------------------------------------------------------------
function baixarCsv(conteudo: string, nome: string): void {
  let url: string | null = null;
  try {
    url = URL.createObjectURL(new Blob([conteudo], { type: "text/csv;charset=utf-8;" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = nome;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    if (url) URL.revokeObjectURL(url);
  }
}

function PedidosDoMotivo({ item, filtros, fechar }: { item: ItemPareto; filtros: FiltrosCx; fechar: () => void }) {
  const tipo = tipoDoItem(item);
  const [r, setR] = useState<ResultadoPedidos | null>(null);
  const caixa = useRef<HTMLElement>(null);
  // a lista abre abaixo da tabela por marca: leva o olho ate' ela
  useEffect(() => {
    // chaves: o Chrome novo devolve uma Promise de scrollIntoView, e o React
    // trataria o retorno como funcao de limpeza
    caixa.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [item.chave]);
  useEffect(() => {
    let vivo = true;
    setR(null);
    carregarPedidosMotivo(filtros, tipo, item.codigo, torreFetch as typeof fetch).then((x) => {
      if (vivo) setR(x);
    });
    return () => {
      vivo = false;
    };
  }, [filtros, tipo, item.codigo]);

  const v = r?.ok && r.dados.estado !== "erro" ? montarPedidosMotivo(r.dados, brandLabel) : null;
  return (
    <section ref={caixa} id="cx-pedidos-motivo" aria-label={`Pedidos: ${item.rotulo}`} className="rounded-lg border border-line bg-surface p-3" data-testid="cx-pedidos-motivo">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">
          {item.tipo} · {item.rotulo}
          {v && <span className="ml-2 text-xs font-normal text-ink-muted">{v.resumo}</span>}
        </h3>
        <div className="flex items-center gap-2">
          {v && v.linhas.length > 0 && r?.ok && (
            <button
              type="button"
              onClick={() => baixarCsv(csvPedidosMotivo(r.dados, item.rotulo), nomeDoCsvPedidos(tipo, item.codigo, filtros, new Date()))}
              className="min-h-11 rounded-md border border-line-strong px-3 text-xs font-semibold text-ink hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Baixar CSV
            </button>
          )}
          <button
            type="button"
            onClick={fechar}
            className="min-h-11 rounded-md px-3 text-xs font-semibold text-ink-muted hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Fechar
          </button>
        </div>
      </div>
      {!r ? (
        <StateBlock tipo="carregando" compacto titulo="Carregando os pedidos" />
      ) : !r.ok || !v ? (
        <StateBlock tipo="erro" compacto titulo="Não foi possível carregar os pedidos" descricao={r.ok ? r.dados.motivo_indisponivel ?? undefined : r.motivo} />
      ) : !v.linhas.length ? (
        <StateBlock tipo="vazio" compacto titulo={r.dados.motivo_indisponivel ?? "Nenhum pedido com este motivo no período."} />
      ) : (
        <div className="mt-2 max-h-96 overflow-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Pedidos com o motivo {item.rotulo}</caption>
            <thead className="sticky top-0 bg-surface">
              <tr className="border-b border-line text-left text-xs font-semibold text-ink-faint">
                <th scope="col" className="py-2 pr-3">Pedido</th>
                <th scope="col" className="px-2 py-2">Marca</th>
                <th scope="col" className="px-2 py-2">Dia</th>
                <th scope="col" className="px-2 py-2">Situação</th>
                {v.comValor && <th scope="col" className={TH}>Reembolsado</th>}
              </tr>
            </thead>
            <tbody>
              {v.linhas.map((l) => (
                <tr key={l.chave} className="border-b border-line last:border-b-0">
                  <td className="whitespace-nowrap py-1.5 pr-3 font-mono text-xs text-ink">{l.pedido}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-ink-muted">{l.marca}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular-nums text-ink-muted">{l.dia}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-ink-muted">{l.situacao}</td>
                  {v.comValor && <td className={TD}>{l.valor}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {v && <Notas notas={v.avisos} />}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 0. Status por marca (so' com "Todas as marcas")
// ---------------------------------------------------------------------------
const BORDA_TOM: Record<StatusMarca["tom"], string> = {
  critico: "border-crit/40",
  atencao: "border-warn/40",
  bom: "border-line",
  neutro: "border-line",
  ausente: "border-dashed border-line",
};

function StatusMarcas({ itens }: { itens: StatusMarca[] }) {
  if (!itens.length) return null;
  return (
    <Panel
      titulo={
        <span className="inline-flex items-center gap-1.5">
          Status por marca
          <InfoDica texto={AJUDA_CX.status} titulo="Status por marca" />
        </span>
      }
      descricao="Saúde da Conta da Shopee, da pior para a melhor"
      aria-label="Status por marca"
    >
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5" data-testid="cx-status-marcas">
        {itens.map((s) => (
          <li key={s.chave} className={`flex min-w-0 flex-col gap-2 rounded-lg border bg-surface p-3 ${BORDA_TOM[s.tom]}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-semibold text-ink">{s.marca}</span>
              <Chip tom={s.tom} ponto={s.tom !== "ausente"}>{s.rotulo}</Chip>
            </div>
            {s.tom === "ausente" ? (
              <p className="text-xs text-ink-faint">{s.alertas[0] ?? "Sem fotografia da Saúde da Conta."}</p>
            ) : s.alertas.length ? (
              <ul className="flex flex-col gap-0.5 text-xs font-medium text-crit-ink">
                {s.alertas.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-ok-ink">Todas as métricas na meta</p>
            )}
            <dl className="mt-auto grid grid-cols-3 gap-2 border-t border-line pt-2 text-xs">
              {(
                [
                  ["Aguardando", s.aguardando],
                  ["Cancel.", s.cancelamento],
                  ["Devol.", s.devolucoes],
                ] as const
              ).map(([r, v]) => (
                <div key={r} className="min-w-0">
                  <dt className="truncate text-ink-faint">{r}</dt>
                  <dd className="font-semibold tabular-nums text-ink">{v}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
