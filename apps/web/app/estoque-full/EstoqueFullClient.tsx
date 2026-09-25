"use client";

/**
 * Tela "Estoque Full Shopee" — Gate FULL-SOURCE-3.
 *
 * PARA QUEM E' E QUE DECISAO SUSTENTA
 * ------------------------------------
 * Gestor de operacao decidindo REPOSICAO no CD da Shopee: o que esta acabando,
 * o que esta parado, quanto tempo o estoque atual aguenta. Por isso a ordem
 * padrao e' operacional (ruptura no topo), e nao alfabetica.
 *
 * AUSENCIA NUNCA VIRA ZERO
 * -------------------------
 * Tres estados de "sem numero" sao desenhados DIFERENTE:
 *   - indisponivel  -> painel proprio, com causa e proximo passo;
 *   - zero medido   -> "0", porque medimos e deu zero;
 *   - sem valor     -> travessao, via `fmtOpcional`.
 * Um "0" manda repor; "nao sabemos" nao manda nada. Confundir os dois e' a
 * unica forma desta tela causar prejuizo.
 *
 * A TELA NAO RECALCULA NADA
 * --------------------------
 * Classificacao, cobertura e media diaria chegam prontas da API. Aqui so' se
 * formata, filtra pela URL e rotula.
 *
 * ALERTAS SO' AQUI
 * ----------------
 * Nada e' enviado ao marketplace e nada dispara reposicao automatica. A tela
 * diz isso por escrito, para que ninguem assuma que "ruptura" ja' virou pedido.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import KpiCard from "@/components/KpiCard";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { EstoqueFullError, fetchEstoqueFull } from "@/lib/api-client";
import {
  AVISO_ALERTAS_SO_NA_TORRE,
  AVISO_CARGA_MANUAL,
  AVISO_CONTEXTO,
  AVISO_COBERTURA_TORRE,
  AVISO_KITS,
  CLASSIFICACOES,
  CLASS_LABEL,
  CLASS_TOM,
  FILTROS_VAZIOS,
  PROXIMO_PASSO,
  SEM_DADO,
  TITULO_INDISPONIVEL,
  buildQuery,
  fmtCobertura,
  fmtData,
  fmtDecimal,
  fmtInt,
  fmtOpcional,
  fotografiaEstaVelha,
  frescorLabel,
  isIndisponivel,
  nomeExibido,
  skuExibido,
  temFiltroAtivo,
  type Classificacao,
  type EstoqueFullResponse,
  type FiltrosEstoque,
  type ProdutoEstoque,
} from "@/lib/estoque-full";

const MARCAS = ["apice", "barbours", "lescent", "rituaria"] as const;

/** Rotulo humano da marca. A API trabalha em caixa baixa. */
function tituloMarca(m: string): string {
  return m.charAt(0).toUpperCase() + m.slice(1);
}

function Aviso({
  tom,
  children,
}: {
  tom: "info" | "alerta";
  children: React.ReactNode;
}) {
  const cor =
    tom === "alerta"
      ? "bg-amber-50 border-amber-200 text-amber-900"
      : "bg-slate-50 border-slate-200 text-slate-700";
  return (
    <p className={`text-sm rounded-xl border px-4 py-3 ${cor}`}>{children}</p>
  );
}

/**
 * Painel de INDISPONIBILIDADE.
 *
 * Deliberadamente NAO parece uma tela de dados: sem cartoes, sem tabela, sem
 * numero algum. Um esqueleto cinza com zeros seria lido como "tudo zerado".
 */
function PainelIndisponivel({
  titulo,
  motivo,
  passo,
  limitacoes,
}: {
  titulo: string;
  motivo: string;
  passo?: string;
  limitacoes?: string[];
}) {
  return (
    <section
      role="status"
      className="bg-white rounded-2xl border border-slate-200 p-8 flex flex-col gap-3"
    >
      <h3 className="text-lg font-semibold text-slate-900">{titulo}</h3>
      <p className="text-sm text-slate-600 max-w-2xl">{motivo}</p>
      {passo && (
        <p className="text-sm text-slate-600 max-w-2xl">
          <span className="font-semibold">Próximo passo: </span>
          {passo}
        </p>
      )}
      <p className="text-sm font-semibold text-slate-900">
        Isto não significa estoque zero: não há medição publicada.
      </p>
      {limitacoes?.length ? (
        <ul className="text-xs text-slate-500 list-disc pl-5 flex flex-col gap-1">
          {limitacoes.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Etiqueta({ classe }: { classe: Classificacao }) {
  return (
    <span
      className={`inline-block text-xs font-semibold border rounded-full px-2 py-0.5 whitespace-nowrap ${CLASS_TOM[classe]}`}
    >
      {CLASS_LABEL[classe]}
    </span>
  );
}

/** Distribuicao por CD. `null` em `is_saleable` vira "não informado". */
function PorCD({ produto }: { produto: ProdutoEstoque }) {
  if (!produto.por_cd.length) return <span className="text-slate-400">{SEM_DADO}</span>;
  return (
    <ul className="flex flex-col gap-0.5">
      {produto.por_cd.map((cd) => (
        <li key={cd.location_id} className="whitespace-nowrap">
          <span className="text-slate-500">{cd.location_id}: </span>
          <span className="tabular-nums">{fmtInt(cd.full_stock)}</span>
          {cd.is_saleable === false && (
            <span className="text-amber-700"> (não vendável)</span>
          )}
          {cd.is_saleable === null && (
            <span className="text-slate-400"> (não informado)</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function Tabela({ produtos }: { produtos: ProdutoEstoque[] }) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200 overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">
          Estoque Full por produto, ordenado por urgência de reposição
        </caption>
        <thead>
          <tr className="text-left text-xs font-semibold text-slate-600 uppercase tracking-wide border-b border-slate-200">
            <th scope="col" className="px-4 py-3">Conta / marca</th>
            <th scope="col" className="px-4 py-3">SKU e produto</th>
            <th scope="col" className="px-4 py-3 text-right">Estoque Full vendável</th>
            <th scope="col" className="px-4 py-3 text-right">Demanda 28d</th>
            <th scope="col" className="px-4 py-3 text-right">Cobertura da Torre</th>
            <th scope="col" className="px-4 py-3">Classificação</th>
            <th scope="col" className="px-4 py-3 text-right">Reservado</th>
            <th scope="col" className="px-4 py-3">Por CD</th>
            <th scope="col" className="px-4 py-3">Fotografia</th>
          </tr>
        </thead>
        <tbody>
          {produtos.map((p) => (
            <tr
              key={`${p.shop_account}-${p.item_id}-${p.model_id}`}
              className="border-b border-slate-100 last:border-0 align-top"
            >
              <td className="px-4 py-3 whitespace-nowrap text-slate-600">
                {tituloMarca(p.shop_account)}
              </td>
              <td className="px-4 py-3">
                <span className="block font-medium text-slate-900">
                  {skuExibido(p)}
                </span>
                <span className="block text-xs text-slate-500">
                  {nomeExibido(p)}
                </span>
              </td>
              <td className="px-4 py-3 text-right tabular-nums font-semibold text-slate-900">
                {fmtInt(p.full_stock_saleable)}
                {p.full_stock_total !== p.full_stock_saleable && (
                  <span className="block text-xs font-normal text-slate-500">
                    {fmtInt(p.full_stock_total)} no total
                  </span>
                )}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {fmtInt(p.units_sold_28d)}
                <span className="block text-xs text-slate-500">
                  {fmtDecimal(p.avg_daily_units_28d)}/dia
                </span>
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {/* `null` = sem venda na janela. "0 dias" seria o OPOSTO:
                    zero dia e' ruptura, sem venda e' ausencia de urgencia. */}
                {fmtCobertura(p.cobertura_torre_dias)}
                {p.cobertura_torre_dias === null && (
                  <span className="block text-xs text-slate-500">
                    sem venda na janela
                  </span>
                )}
              </td>
              <td className="px-4 py-3">
                <Etiqueta classe={p.classificacao_torre} />
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-500">
                {/* CONTEXTO, nao estoque Full. */}
                {fmtOpcional(p.reserved_stock)}
              </td>
              <td className="px-4 py-3 text-xs text-slate-600">
                <PorCD produto={p} />
              </td>
              <td className="px-4 py-3 whitespace-nowrap text-xs text-slate-500">
                {fmtData(p.ref_date)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function EstoqueFullClient() {
  const [filtros, setFiltros] = useState<FiltrosEstoque>(FILTROS_VAZIOS);
  const [payload, setPayload] = useState<EstoqueFullResponse | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  // Debounce da busca: digitar "capa" nao pode disparar quatro requisicoes.
  const [buscaDigitada, setBuscaDigitada] = useState("");
  useEffect(() => {
    const t = setTimeout(
      () => setFiltros((f) => ({ ...f, busca: buscaDigitada })),
      300,
    );
    return () => clearTimeout(t);
  }, [buscaDigitada]);

  const query = useMemo(() => buildQuery(filtros), [filtros]);
  const abortRef = useRef<AbortController | null>(null);

  const carregar = useCallback(async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setCarregando(true);
    setErro(null);
    try {
      setPayload(await fetchEstoqueFull(query, ctrl.signal));
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      // 🔑 Erro NAO zera o payload anterior por acidente: limpamos de
      // proposito, para que numero velho nunca conviva com mensagem de falha.
      setPayload(null);
      setErro(
        e instanceof EstoqueFullError
          ? e.message
          : "Falha inesperada ao carregar o estoque.",
      );
    } finally {
      if (!ctrl.signal.aborted) setCarregando(false);
    }
  }, [query]);

  useEffect(() => {
    carregar();
    return () => abortRef.current?.abort();
  }, [carregar]);

  const alternarMarca = (m: string) =>
    setFiltros((f) => ({
      ...f,
      brands: f.brands.includes(m)
        ? f.brands.filter((x) => x !== m)
        : [...f.brands, m],
    }));

  const alternarClasse = (c: Classificacao) =>
    setFiltros((f) => ({
      ...f,
      classificacoes: f.classificacoes.includes(c)
        ? f.classificacoes.filter((x) => x !== c)
        : [...f.classificacoes, c],
    }));

  const limpar = () => {
    setBuscaDigitada("");
    setFiltros(FILTROS_VAZIOS);
  };

  const filtrosUI = (
    <div className="flex flex-col gap-3 w-full">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
          Conta
        </span>
        {MARCAS.map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={filtros.brands.includes(m)}
            onClick={() => alternarMarca(m)}
            className={`text-sm rounded-full border px-3 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
              filtros.brands.includes(m)
                ? "bg-violet-600 border-violet-600 text-white font-semibold"
                : "bg-white border-slate-200 text-slate-600 hover:border-violet-300"
            }`}
          >
            {tituloMarca(m)}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
          Classificação
        </span>
        {CLASSIFICACOES.map((c) => (
          <button
            key={c}
            type="button"
            aria-pressed={filtros.classificacoes.includes(c)}
            onClick={() => alternarClasse(c)}
            className={`text-sm rounded-full border px-3 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
              filtros.classificacoes.includes(c)
                ? "bg-violet-600 border-violet-600 text-white font-semibold"
                : "bg-white border-slate-200 text-slate-600 hover:border-violet-300"
            }`}
          >
            {CLASS_LABEL[c]}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <span className="sr-only">Buscar por SKU ou produto</span>
          <input
            type="search"
            value={buscaDigitada}
            onChange={(e) => setBuscaDigitada(e.target.value)}
            placeholder="Buscar SKU ou produto"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm w-64 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={filtros.somenteAcao}
            onChange={(e) =>
              setFiltros((f) => ({ ...f, somenteAcao: e.target.checked }))
            }
            className="rounded border-slate-300"
          />
          Somente itens que exigem ação
        </label>
        {temFiltroAtivo(filtros) && (
          <button
            type="button"
            onClick={limpar}
            className="text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
          >
            Limpar filtros
          </button>
        )}
      </div>
    </div>
  );

  const escopo =
    payload && !isIndisponivel(payload)
      ? `${payload.scope_label} · ${frescorLabel(payload.frescor)}`
      : "Estoque Full Shopee — cobertura API";

  return (
    <PageContainer>
      <PageHeader
        title="Estoque Full Shopee"
        subtitle="Estoque físico no centro de distribuição da Shopee (FBS), por produto."
        scopeLine={escopo}
        filters={filtrosUI}
      />

      {carregando && (
        <p role="status" className="text-sm text-slate-500 px-1 py-8">
          Carregando estoque…
        </p>
      )}

      {!carregando && erro && (
        <section
          role="alert"
          className="bg-white rounded-2xl border border-red-200 p-8 flex flex-col gap-3"
        >
          <h3 className="text-lg font-semibold text-slate-900">
            Não foi possível carregar o estoque
          </h3>
          <p className="text-sm text-slate-600">{erro}</p>
          {/* Sem numero nenhum aqui: falha nao pode ser lida como medicao. */}
          <button
            type="button"
            onClick={carregar}
            className="self-start text-sm font-semibold rounded-lg bg-violet-600 text-white px-4 py-2 hover:bg-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          >
            Tentar novamente
          </button>
        </section>
      )}

      {!carregando && !erro && payload && isIndisponivel(payload) && (
        <PainelIndisponivel
          titulo={TITULO_INDISPONIVEL[payload.motivo_tecnico]}
          motivo={payload.unavailable_reason}
          passo={PROXIMO_PASSO[payload.motivo_tecnico]}
          limitacoes={payload.limitacoes}
        />
      )}

      {!carregando && !erro && payload && !isIndisponivel(payload) && (
        <>
          {fotografiaEstaVelha(payload.frescor) && (
            <Aviso tom="alerta">
              {frescorLabel(payload.frescor)}. {AVISO_CARGA_MANUAL}
            </Aviso>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
            <KpiCard
              label="Unidades vendáveis"
              value={fmtInt(payload.indicadores.unidades_vendaveis_operacional)}
              subvalue={`${fmtInt(payload.indicadores.produtos_total)} produtos na fotografia`}
              accent="bg-emerald-500"
            />
            <KpiCard
              label="Exigem ação"
              value={fmtInt(payload.indicadores.produtos_exigem_acao)}
              subvalue="Ruptura + estoque baixo"
              accent="bg-red-500"
            />
            <KpiCard
              label="Ruptura"
              value={fmtInt(payload.indicadores.produtos_ruptura)}
              subvalue="Sem estoque vendável, com venda na janela"
              accent="bg-red-500"
            />
            <KpiCard
              label="Estoque baixo"
              value={fmtInt(payload.indicadores.produtos_baixo)}
              subvalue={`Abaixo de ${payload.limites.cobertura_baixa_dias} dias de cobertura`}
              accent="bg-amber-500"
            />
            <KpiCard
              label="Excesso"
              value={fmtInt(payload.indicadores.produtos_excesso)}
              subvalue={`A partir de ${payload.limites.cobertura_excesso_dias} dias de cobertura`}
              accent="bg-sky-500"
            />
            <KpiCard
              label="Sem giro"
              value={fmtInt(payload.indicadores.produtos_sem_giro)}
              subvalue="Com estoque, sem venda na janela"
              accent="bg-slate-400"
            />
            <KpiCard
              label="Suficiente"
              value={fmtInt(payload.indicadores.produtos_suficientes)}
              subvalue={`Entre ${payload.limites.cobertura_baixa_dias} e ${payload.limites.cobertura_excesso_dias} dias`}
              accent="bg-emerald-500"
            />
            <KpiCard
              label="Kits não conciliados"
              value={fmtInt(payload.indicadores.produtos_kit_nao_conciliado)}
              subvalue={`${fmtInt(payload.indicadores.unidades_vendaveis_kits_contexto)} unidades, fora do operacional`}
              accent="bg-violet-500"
            />
          </div>

          {payload.marcas_nao_cobertas.length > 0 && (
            <Aviso tom="info">
              Cobertura parcial:{" "}
              {payload.marcas_nao_cobertas.map(tituloMarca).join(", ")} não
              está na esteira de API e não aparece em nenhum número desta tela —
              como ausência, nunca como zero.
            </Aviso>
          )}

          {payload.total_no_filtro === 0 ? (
            /* Vazio POR FILTRO: os cartoes acima continuam de pe', porque o
               dado existe. E' diferente de indisponivel. */
            <section
              role="status"
              className="bg-white rounded-2xl border border-slate-200 p-8 flex flex-col gap-3"
            >
              <h3 className="text-base font-semibold text-slate-900">
                Nenhum produto neste filtro
              </h3>
              <p className="text-sm text-slate-600">
                A fotografia de {fmtData(payload.frescor.ref_date)} tem{" "}
                {fmtInt(payload.indicadores.produtos_total)} produtos. Nenhum
                deles casa com os filtros atuais.
              </p>
              {temFiltroAtivo(filtros) && (
                <button
                  type="button"
                  onClick={limpar}
                  className="self-start text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                >
                  Limpar filtros
                </button>
              )}
            </section>
          ) : (
            <>
              <p className="text-xs text-slate-500 px-1">
                Exibindo {fmtInt(payload.produtos.length)} de{" "}
                {fmtInt(payload.total_no_filtro)} produtos no filtro.
                {payload.truncado &&
                  " A lista foi truncada — refine o filtro para ver o restante."}
              </p>
              <Tabela produtos={payload.produtos} />
            </>
          )}

          <section className="flex flex-col gap-2 px-1 pb-4">
            <h4 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Como ler esta tela
            </h4>
            <ul className="text-xs text-slate-500 list-disc pl-5 flex flex-col gap-1">
              <li>{AVISO_COBERTURA_TORRE}</li>
              <li>{payload.limites.observacao}</li>
              <li>{AVISO_CONTEXTO}</li>
              <li>{AVISO_KITS}</li>
              <li>{AVISO_CARGA_MANUAL}</li>
              <li>{AVISO_ALERTAS_SO_NA_TORRE}</li>
            </ul>
          </section>
        </>
      )}
    </PageContainer>
  );
}
