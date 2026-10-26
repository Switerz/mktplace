"use client";

/**
 * Tela "Estoque Full Mercado Livre" — ML-FULL-STOCK-API-1.
 *
 * PARA QUEM E' E QUE DECISAO SUSTENTA
 * ------------------------------------
 * Gestor de operacao decidindo REPOSICAO no Full do ML. Grao: INVENTARIO (o
 * estoque fisico), nunca anuncio. Ordem padrao operacional (vem da API):
 * ruptura, baixo e "sem leitura" no topo.
 *
 * AUSENCIA NUNCA VIRA ZERO
 * -------------------------
 *   - indisponivel        -> painel proprio, com causa e proximo passo;
 *   - zero medido         -> "0";
 *   - sem leitura / marca sem fotografia -> travessao, via `fmtOpcional`,
 *     e o total e' rotulado PARCIAL com os motivos da API.
 *
 * A TELA NAO RECALCULA NADA: classificacao, cobertura, totais e parcialidade
 * chegam prontos da API.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import KpiCard from "@/components/KpiCard";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { MLFullEstoqueError, fetchMLFullEstoque } from "@/lib/api-client";
import {
  AVISO_ALERTAS_SO_NA_TORRE_ML,
  AVISO_AUSENCIA_ML,
  AVISO_GRAO_ML,
  AVISO_NAO_E_MODALIDADE_ML,
  AVISO_TRANSFER_ML,
  CLASS_LABEL_ML,
  CLASS_TOM_ML,
  FILTROS_ML_VAZIOS,
  MARCAS_ML,
  PROXIMO_PASSO_ML,
  SEM_DADO,
  TITULO_INDISPONIVEL_ML,
  buildQueryML,
  coberturaMarcasLabel,
  eDataHistorica,
  fmtCobertura,
  fmtData,
  fmtDecimal,
  fmtFracao,
  fmtInt,
  fmtOpcional,
  fotografiaVelhaML,
  frescorLabelML,
  isIndisponivelML,
  leituraLabel,
  temFiltroAtivoML,
  tituloMarca,
  type ClassificacaoML,
  type FiltrosML,
  type InventarioML,
  type MarcaML,
  type MLFullEstoqueResponse,
} from "@/lib/ml-full-estoque";

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

/** Sem cartoes, sem tabela, sem numero: nada que pareca "tudo zerado". */
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

function Etiqueta({ classe }: { classe: ClassificacaoML }) {
  return (
    <span
      className={`inline-block text-xs font-semibold border rounded-full px-2 py-0.5 whitespace-nowrap ${CLASS_TOM_ML[classe]}`}
    >
      {CLASS_LABEL_ML[classe]}
    </span>
  );
}

/** Cobertura por marca: com fotografia, sem fotografia (ausencia), leitura. */
function TabelaMarcas({ marcas }: { marcas: MarcaML[] }) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200 overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Cobertura e estoque Full por marca</caption>
        <thead>
          <tr className="text-left text-xs font-semibold text-slate-600 uppercase tracking-wide border-b border-slate-200">
            <th scope="col" className="px-4 py-3">Marca</th>
            <th scope="col" className="px-4 py-3">Fotografia</th>
            <th scope="col" className="px-4 py-3 text-right">Disponível Full</th>
            <th scope="col" className="px-4 py-3 text-right">Inventários lidos</th>
            <th scope="col" className="px-4 py-3 text-right">Sem leitura</th>
            <th scope="col" className="px-4 py-3 text-right">Compartilhados</th>
            <th scope="col" className="px-4 py-3 text-right">Vendas alocadas</th>
          </tr>
        </thead>
        <tbody>
          {marcas.map((m) => (
            <tr key={m.brand} className="border-b border-slate-100 last:border-0">
              <td className="px-4 py-3 font-medium text-slate-900">{tituloMarca(m.brand)}</td>
              <td className="px-4 py-3 text-xs text-slate-600 whitespace-nowrap">
                {m.tem_fotografia_no_dia ? (
                  fmtData(m.ultima_fotografia)
                ) : (
                  <span className="text-amber-800">
                    Sem fotografia no dia
                    {m.ultima_fotografia
                      ? ` (última: ${fmtData(m.ultima_fotografia)})`
                      : " (nunca publicada)"}
                  </span>
                )}
              </td>
              <td className="px-4 py-3 text-right tabular-nums font-semibold text-slate-900">
                {fmtOpcional(m.disponivel_full)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {m.leitura
                  ? `${fmtInt(m.leitura.inventarios_lidos)} de ${fmtInt(m.leitura.inventarios)}`
                  : SEM_DADO}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {m.leitura ? fmtInt(m.leitura.inventarios_sem_leitura) : SEM_DADO}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {fmtOpcional(m.inventarios_compartilhados)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {fmtFracao(m.cobertura_da_alocacao)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TabelaInventarios({ inventarios }: { inventarios: InventarioML[] }) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200 overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">
          Estoque Full por inventário, ordenado por urgência de reposição
        </caption>
        <thead>
          <tr className="text-left text-xs font-semibold text-slate-600 uppercase tracking-wide border-b border-slate-200">
            <th scope="col" className="px-4 py-3">Marca</th>
            <th scope="col" className="px-4 py-3">Inventário</th>
            <th scope="col" className="px-4 py-3 text-right">Disponível</th>
            <th scope="col" className="px-4 py-3 text-right">Não disponível</th>
            <th scope="col" className="px-4 py-3 text-right">Demanda 28d</th>
            <th scope="col" className="px-4 py-3 text-right">Cobertura da Torre</th>
            <th scope="col" className="px-4 py-3">Classificação</th>
            <th scope="col" className="px-4 py-3 text-right">Anúncios</th>
          </tr>
        </thead>
        <tbody>
          {inventarios.map((i) => (
            <tr
              key={`${i.brand}-${i.inventory_id}`}
              className="border-b border-slate-100 last:border-0 align-top"
            >
              <td className="px-4 py-3 whitespace-nowrap text-slate-600">{tituloMarca(i.brand)}</td>
              <td className="px-4 py-3 font-mono text-xs text-slate-700">{i.inventory_id}</td>
              <td className="px-4 py-3 text-right tabular-nums font-semibold text-slate-900">
                {/* `null` = sem leitura: travessao, nunca 0. */}
                {fmtOpcional(i.disponivel)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {fmtOpcional(i.nao_disponivel)}
                {i.transfer !== null && i.transfer > 0 && (
                  <span className="block text-xs text-slate-500">
                    {fmtInt(i.transfer)} em transferência
                  </span>
                )}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {fmtInt(i.unidades_28d)}
                <span className="block text-xs text-slate-500">
                  {fmtDecimal(i.media_diaria_28d)}/dia
                </span>
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                {fmtCobertura(i.cobertura_torre_dias)}
              </td>
              <td className="px-4 py-3">
                <Etiqueta classe={i.classificacao_torre} />
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                {/* ASSOCIACAO, nao estoque: nunca multiplica o disponivel. */}
                {fmtInt(i.anuncios)}
                {i.compartilhado && (
                  <span className="block text-xs text-slate-500">compartilhado</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MLFullEstoqueClient() {
  const [filtros, setFiltros] = useState<FiltrosML>(FILTROS_ML_VAZIOS);
  const [payload, setPayload] = useState<MLFullEstoqueResponse | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  const query = useMemo(() => buildQueryML(filtros), [filtros]);
  const abortRef = useRef<AbortController | null>(null);

  const carregar = useCallback(async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setCarregando(true);
    setErro(null);
    try {
      setPayload(await fetchMLFullEstoque(query, ctrl.signal));
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      // Numero velho nunca convive com mensagem de falha.
      setPayload(null);
      setErro(
        e instanceof MLFullEstoqueError
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

  const limpar = () => setFiltros(FILTROS_ML_VAZIOS);

  const filtrosUI = (
    <div className="flex flex-col gap-3 w-full">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
          Marca
        </span>
        {MARCAS_ML.map((m) => (
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
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          Data da fotografia
          <input
            type="date"
            value={filtros.refDate}
            onChange={(e) => setFiltros((f) => ({ ...f, refDate: e.target.value }))}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={filtros.incluirForaDoFull}
            onChange={(e) =>
              setFiltros((f) => ({ ...f, incluirForaDoFull: e.target.checked }))
            }
            className="rounded border-slate-300"
          />
          Listar também inventários fora do Full
        </label>
        {temFiltroAtivoML(filtros) && (
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
    payload && !isIndisponivelML(payload)
      ? `${payload.scope_label} · ${frescorLabelML(payload.frescor)}`
      : "Estoque Full Mercado Livre — por inventário";

  return (
    <PageContainer>
      <PageHeader
        title="Estoque Full Mercado Livre"
        subtitle="Estoque físico no Full do Mercado Livre, por inventário."
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
          <button
            type="button"
            onClick={carregar}
            className="self-start text-sm font-semibold rounded-lg bg-violet-600 text-white px-4 py-2 hover:bg-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          >
            Tentar novamente
          </button>
        </section>
      )}

      {!carregando && !erro && payload && isIndisponivelML(payload) && (
        <PainelIndisponivel
          titulo={TITULO_INDISPONIVEL_ML[payload.motivo_tecnico]}
          motivo={payload.unavailable_reason}
          passo={PROXIMO_PASSO_ML[payload.motivo_tecnico]}
          limitacoes={payload.limitacoes}
        />
      )}

      {!carregando && !erro && payload && !isIndisponivelML(payload) && (
        <>
          {fotografiaVelhaML(payload.frescor) && (
            <Aviso tom="alerta">
              {frescorLabelML(payload.frescor)}. A publicação ainda não tem
              agendamento.
            </Aviso>
          )}
          {eDataHistorica(payload.frescor) && (
            <Aviso tom="info">
              Exibindo a data {fmtData(payload.frescor.ref_date)}. A última
              fotografia publicada é de{" "}
              {fmtData(payload.frescor.ultima_fotografia_disponivel)}.
            </Aviso>
          )}

          {/* Frescor e cobertura SEMPRE visiveis, antes dos numeros. */}
          <section className="bg-white rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-700 flex flex-col gap-1">
            <span>{frescorLabelML(payload.frescor)}</span>
            <span>{coberturaMarcasLabel(payload.cobertura)}</span>
            <span>{leituraLabel(payload.cobertura.leitura)}</span>
            {payload.cobertura.marcas_sem_fotografia.length > 0 && (
              <span className="text-amber-800">
                Sem fotografia no dia:{" "}
                {payload.cobertura.marcas_sem_fotografia.map(tituloMarca).join(", ")}{" "}
                — como ausência, nunca como zero.
              </span>
            )}
          </section>

          {payload.indicadores.total_parcial && (
            <Aviso tom="alerta">
              Total PARCIAL: {payload.indicadores.motivos_total_parcial.join(" ")}
            </Aviso>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
            <KpiCard
              label="Disponível Full"
              value={fmtOpcional(payload.indicadores.disponivel_full)}
              subvalue={
                payload.indicadores.total_parcial
                  ? "Parcial: ver aviso acima"
                  : `${fmtInt(payload.indicadores.inventarios_full_lidos)} inventários Full lidos`
              }
              accent="bg-emerald-500"
            />
            <KpiCard
              label="Exigem ação"
              value={fmtInt(payload.indicadores.inventarios_exigem_acao)}
              subvalue="Ruptura + estoque baixo"
              accent="bg-red-500"
            />
            <KpiCard
              label="Sem leitura"
              value={fmtInt(payload.indicadores.inventarios_sem_leitura)}
              subvalue="Inventários sem quantidade medida"
              accent="bg-orange-500"
            />
            <KpiCard
              label="Compartilhados"
              value={fmtInt(payload.indicadores.inventarios_compartilhados)}
              subvalue="Inventários com 2+ anúncios (contados uma vez)"
              accent="bg-violet-500"
            />
            <KpiCard
              label="Ruptura"
              value={fmtInt(payload.indicadores.inventarios_ruptura)}
              subvalue="Sem disponível, com venda na janela"
              accent="bg-red-500"
            />
            <KpiCard
              label="Estoque baixo"
              value={fmtInt(payload.indicadores.inventarios_baixo)}
              subvalue={`Abaixo de ${payload.limites.cobertura_baixa_dias} dias de cobertura`}
              accent="bg-amber-500"
            />
            <KpiCard
              label="Excesso"
              value={fmtInt(payload.indicadores.inventarios_excesso)}
              subvalue={`A partir de ${payload.limites.cobertura_excesso_dias} dias de cobertura`}
              accent="bg-sky-500"
            />
            <KpiCard
              label="Não disponível Full"
              value={fmtOpcional(payload.indicadores.nao_disponivel_full)}
              subvalue={`${fmtOpcional(payload.indicadores.transfer_full)} em transferência interna`}
              accent="bg-slate-400"
            />
          </div>

          <TabelaMarcas marcas={payload.marcas} />

          {payload.total_no_filtro === 0 ? (
            <section
              role="status"
              className="bg-white rounded-2xl border border-slate-200 p-8 flex flex-col gap-3"
            >
              <h3 className="text-base font-semibold text-slate-900">
                Nenhum inventário neste filtro
              </h3>
              <p className="text-sm text-slate-600">
                A fotografia de {fmtData(payload.frescor.ref_date)} tem{" "}
                {fmtInt(payload.indicadores.inventarios)} inventários; nenhum
                casa com os filtros atuais.
              </p>
            </section>
          ) : (
            <>
              <p className="text-xs text-slate-500 px-1">
                Exibindo {fmtInt(payload.inventarios.length)} de{" "}
                {fmtInt(payload.total_no_filtro)} inventários no filtro.
                {payload.truncado &&
                  " A lista foi truncada — os indicadores acima cobrem o escopo inteiro."}
              </p>
              <TabelaInventarios inventarios={payload.inventarios} />
            </>
          )}

          <section className="flex flex-col gap-2 px-1 pb-4">
            <h4 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Como ler esta tela
            </h4>
            <ul className="text-xs text-slate-500 list-disc pl-5 flex flex-col gap-1">
              <li>{AVISO_GRAO_ML}</li>
              <li>{AVISO_AUSENCIA_ML}</li>
              <li>{AVISO_NAO_E_MODALIDADE_ML}</li>
              <li>{AVISO_TRANSFER_ML}</li>
              <li>{AVISO_ALERTAS_SO_NA_TORRE_ML}</li>
              {payload.limitacoes.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </section>
        </>
      )}
    </PageContainer>
  );
}
