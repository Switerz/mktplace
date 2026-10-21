"use client";

/**
 * Gate TORRE-B2B-OCTAPRICE — /monitoramento-preco/revendedores
 *
 * Persona: Trade/Pricing e liderança comercial que precisam saber se clientes
 * B2B estão revendendo abaixo do PMA, em que canal/marca/SKU, com que
 * gravidade — e se o dado é de hoje.
 *
 * NADA é calculado aqui. A API repassa as RPCs da Torre B2B; esta tela só
 * apresenta. Piso = PMA do OctaPrice (nunca o PDV da visão Lojas próprias).
 *
 * Quatro estados distintos e nunca confundidos: carregando, erro (a API
 * falhou — com nova tentativa), indisponível (a fonte não tem o dado — com o
 * motivo) e vazio (a fonte mediu e o recorte não tem anúncio).
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, Percent, Store, Tag, TrendingDown, Trophy, UserCheck } from "lucide-react";

import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import TableScrollHint from "@/components/TableScrollHint";
import Disclosure from "@/components/ui/Disclosure";
import Note from "@/components/ui/Note";
import Panel from "@/components/ui/Panel";
import StatCard from "@/components/ui/StatCard";
import StateBlock from "@/components/ui/StateBlock";
import {
  AVISO_FIXTURE,
  AVISO_SEM_CLIENTE,
  B2BRevendaError,
  FILTROS_VAZIOS,
  MARCAS,
  SEVERIDADES,
  SEM_COLETA_IMPORTACAO,
  SEM_DADO,
  carregarPainel,
  fmtData,
  fmtDesvio,
  fmtInstante,
  fmtInt,
  fmtMoeda,
  fmtPct,
  isIndisponivel,
  participacao,
  resumoVazio,
  situacaoLabel,
  temFiltro,
  tituloIndisponivel,
  type Escopo,
  type Filtros,
  type Frescor,
  type Indisponivel,
  type PainelB2B,
  type ResumoOk,
} from "@/lib/b2b-revenda";
import { painelFicticio } from "@/lib/b2b-revenda-fixtures";

type Estado =
  | { tipo: "carregando" }
  | { tipo: "erro"; mensagem: string }
  | { tipo: "pronto"; painel: PainelB2B };

const CELULA = "px-3 py-2 text-data";
const CABECALHO = "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-ink-muted";
const NUM = "text-right tabular-nums";

export default function RevendedoresClient({ modo }: { modo: "fixture" | "api" }) {
  const [filtros, setFiltros] = useState<Filtros>(FILTROS_VAZIOS);
  const [estado, setEstado] = useState<Estado>({ tipo: "carregando" });
  const ultima = useRef<AbortController | null>(null);

  const carregar = useCallback(
    async (f: Filtros) => {
      ultima.current?.abort();
      const ac = new AbortController();
      ultima.current = ac;
      setEstado({ tipo: "carregando" });
      if (modo === "fixture") {
        // Nenhuma requisicao no modo de demonstracao.
        setEstado({ tipo: "pronto", painel: painelFicticio(f.data) });
        return;
      }
      try {
        const painel = await carregarPainel(f, ac.signal);
        if (!ac.signal.aborted) setEstado({ tipo: "pronto", painel });
      } catch (err) {
        if (ac.signal.aborted) return;
        const mensagem = err instanceof B2BRevendaError ? err.message : "Falha inesperada ao carregar.";
        setEstado({ tipo: "erro", mensagem });
      }
    },
    [modo],
  );

  useEffect(() => {
    carregar(filtros);
    return () => ultima.current?.abort();
  }, [carregar, filtros]);

  const painel = estado.tipo === "pronto" ? estado.painel : null;
  const frescorOk = painel && !isIndisponivel(painel.frescor) ? painel.frescor : null;
  const frescor: Frescor | null =
    frescorOk?.frescor ?? (painel && isIndisponivel(painel.resumo) ? painel.resumo.frescor : null);
  const escopo = frescorOk?.escopo ?? null;

  return (
    <PageContainer>
      <PageHeader
        title="Monitoramento de preços — B2B / Revendedores"
        subtitle="Preço praticado por revendedores B2B nos marketplaces comparado ao PMA informado pelo OctaPrice. Fonte: Torre B2B, somente leitura."
      />

      {modo === "fixture" && (
        <Note tom="atencao" papel="status">
          <strong className="font-semibold">Demonstração.</strong> {AVISO_FIXTURE}
        </Note>
      )}

      <FaixaFrescor frescor={frescor} carregando={estado.tipo === "carregando"} />
      {escopo && <AvisoEscopo escopo={escopo} />}

      <BarraFiltros
        filtros={filtros}
        datas={frescor?.datas_disponiveis ?? []}
        canais={escopo?.canais_nos_kpis ?? []}
        aoMudar={setFiltros}
      />

      {estado.tipo === "carregando" && <StateBlock tipo="carregando" titulo="Carregando a Torre B2B…" />}
      {estado.tipo === "erro" && (
        <StateBlock
          tipo="erro"
          titulo="Não foi possível carregar a aba"
          descricao={estado.mensagem}
          acao={
            <button
              type="button"
              onClick={() => carregar(filtros)}
              className="inline-flex min-h-11 items-center rounded-md border border-line bg-surface px-3 text-sm font-medium text-ink hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Tentar de novo
            </button>
          }
        />
      )}
      {painel && <Conteudo painel={painel} filtros={filtros} />}

      <Disclosure rotulo="Como ler esta aba">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-muted">
          <li>{AVISO_SEM_CLIENTE}</li>
          {(painel && !isIndisponivel(painel.frescor) ? painel.frescor.limitacoes : []).map((l) => (
            <li key={l}>{l}</li>
          ))}
          <li>
            Severidade: grave = mais de 15% abaixo do PMA; média = de 5% a 15%; leve = até 5%. As faixas são as da
            Torre B2B.
          </li>
        </ul>
      </Disclosure>
    </PageContainer>
  );
}

// ---------------------------------------------------------------------------
// Frescor e escopo
// ---------------------------------------------------------------------------
function FaixaFrescor({ frescor, carregando }: { frescor: Frescor | null; carregando: boolean }) {
  if (!frescor) {
    return carregando ? null : (
      <Note tom="ausente">Frescor indisponível: a Torre B2B não informou a carga mais recente.</Note>
    );
  }
  const atrasado = frescor.situacao === "atrasado";
  return (
    <section
      aria-label="Frescor do dado"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink-muted"
    >
      <span
        className={`inline-flex min-h-8 items-center gap-1.5 rounded-md px-2.5 font-medium ring-1 ring-inset ${
          atrasado ? "bg-warn-soft text-warn-ink ring-warn/30" : "bg-ok-soft text-ok-ink ring-ok/30"
        }`}
      >
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${atrasado ? "bg-warn" : "bg-ok"}`} />
        {situacaoLabel(frescor)}
      </span>
      <span>
        Relatório de <strong className="font-semibold text-ink tabular-nums">{fmtData(frescor.data_ref_mais_recente)}</strong>
      </span>
      {frescor.coleta_fim || frescor.importado_fim ? (
        <>
          <span className="tabular-nums">Coleta até {fmtInstante(frescor.coleta_fim)}</span>
          <span className="tabular-nums">Importado {fmtInstante(frescor.importado_fim)}</span>
        </>
      ) : (
        <span>{SEM_COLETA_IMPORTACAO}</span>
      )}
      <span className="tabular-nums">Consultado {fmtInstante(frescor.consultado_em)}</span>
      {atrasado && (
        <span className="text-warn-ink">Esperado: relatório de {fmtData(frescor.data_ref_esperada)}.</span>
      )}
    </section>
  );
}

function AvisoEscopo({ escopo }: { escopo: Escopo }) {
  return (
    <Note tom="neutro">
      <span className="font-semibold text-ink">Piso: PMA do OctaPrice.</span> {escopo.aviso_piso}
      {escopo.canais_excluidos.length > 0 && (
        <>
          {" "}
          {escopo.canais_excluidos.map((c) => (
            <span key={c.canal}>
              <span className="font-semibold text-ink">{c.canal}:</span> {c.motivo}{" "}
            </span>
          ))}
        </>
      )}
    </Note>
  );
}

// ---------------------------------------------------------------------------
// Filtros
// ---------------------------------------------------------------------------
function BarraFiltros({
  filtros,
  datas,
  canais,
  aoMudar,
}: {
  filtros: Filtros;
  datas: string[];
  canais: string[];
  aoMudar: (f: Filtros) => void;
}) {
  const base = useId();
  const muda = (campo: keyof Filtros) => (e: React.ChangeEvent<HTMLSelectElement>) =>
    aoMudar({ ...filtros, [campo]: e.target.value });
  const select =
    "min-h-11 rounded-md border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
  const rotulo = "text-xs font-semibold uppercase tracking-wide text-ink-muted";
  return (
    <section aria-label="Filtros" className="flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface px-3 py-2">
      <label htmlFor={`${base}-data`} className="flex flex-col gap-1">
        <span className={rotulo}>Dia do relatório</span>
        <select id={`${base}-data`} className={select} value={filtros.data} onChange={muda("data")}>
          <option value="">Mais recente</option>
          {datas.map((d) => (
            <option key={d} value={d}>
              {fmtData(d)}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={`${base}-marca`} className="flex flex-col gap-1">
        <span className={rotulo}>Marca</span>
        <select id={`${base}-marca`} className={select} value={filtros.marca} onChange={muda("marca")}>
          <option value="">Todas</option>
          {MARCAS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.nome}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={`${base}-canal`} className="flex flex-col gap-1">
        <span className={rotulo}>Canal</span>
        <select id={`${base}-canal`} className={select} value={filtros.canal} onChange={muda("canal")}>
          <option value="">Todos no escopo</option>
          {canais.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={`${base}-sev`} className="flex flex-col gap-1">
        <span className={rotulo}>Severidade</span>
        <select id={`${base}-sev`} className={select} value={filtros.severidade} onChange={muda("severidade")}>
          <option value="">Todas</option>
          {SEVERIDADES.map((s) => (
            <option key={s.id} value={s.id}>
              {s.nome} ({s.faixa})
            </option>
          ))}
        </select>
      </label>
      {temFiltro(filtros) && (
        <button
          type="button"
          onClick={() => aoMudar(FILTROS_VAZIOS)}
          className="min-h-11 rounded-md px-3 text-sm text-ink-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Limpar filtros
        </button>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Conteudo
// ---------------------------------------------------------------------------
function BlocoIndisponivel({ r, compacto = false }: { r: Indisponivel; compacto?: boolean }) {
  return <StateBlock tipo="indisponivel" titulo={tituloIndisponivel(r)} descricao={r.unavailable_reason} compacto={compacto} />;
}

function Conteudo({ painel, filtros }: { painel: PainelB2B; filtros: Filtros }) {
  const { resumo, canaisMarcas, skus } = painel;
  if (isIndisponivel(resumo)) return <BlocoIndisponivel r={resumo} />;
  if (resumoVazio(resumo)) {
    return (
      <StateBlock
        tipo="vazio"
        titulo="Nenhum anúncio neste recorte"
        descricao={
          temFiltro(filtros)
            ? "A Torre B2B respondeu e não há anúncio monitorado com estes filtros."
            : "A Torre B2B respondeu e o relatório deste dia não tem anúncio nos canais do escopo."
        }
      />
    );
  }
  return (
    <>
      {resumo.consulta_historica && (
        <Note tom="neutro" papel="status">
          Consulta histórica: relatório de {fmtData(resumo.data_ref)} (o mais recente é de{" "}
          {fmtData(resumo.frescor.data_ref_mais_recente)}).
        </Note>
      )}
      <FaixaKpis resumo={resumo} />
      <div className="grid grid-cols-1 gap-3 sm:gap-4 xl:grid-cols-3">
        <PainelSeveridade resumo={resumo} />
        <div className="xl:col-span-2 min-w-0">
          <Panel titulo="Canal × marca" descricao="Anúncios e violações do PMA no dia, nos canais do escopo." semPadding>
            {isIndisponivel(canaisMarcas) ? (
              <BlocoIndisponivel r={canaisMarcas} compacto />
            ) : canaisMarcas.linhas.length === 0 ? (
              <StateBlock tipo="vazio" titulo="Sem linhas neste recorte" compacto />
            ) : (
              <TableScrollHint>
                <table className="w-full min-w-[640px] tabular-nums">
                  <thead className="bg-raised">
                    <tr>
                      <th scope="col" className={CABECALHO}>Marca</th>
                      <th scope="col" className={CABECALHO}>Canal</th>
                      <th scope="col" className={`${CABECALHO} ${NUM}`}>Anúncios</th>
                      <th scope="col" className={`${CABECALHO} ${NUM}`}>Violando</th>
                      <th scope="col" className={`${CABECALHO} ${NUM}`}>% violação</th>
                      <th scope="col" className={`${CABECALHO} ${NUM}`}>Desvio médio</th>
                      <th scope="col" className={`${CABECALHO} ${NUM}`}>Preço médio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {canaisMarcas.linhas.map((l) => (
                      <tr key={`${l.marca_id ?? "-"}|${l.canal ?? "-"}`} className="border-t border-line">
                        <td className={CELULA}>{l.marca}</td>
                        <td className={CELULA}>{l.canal ?? SEM_DADO}</td>
                        <td className={`${CELULA} ${NUM}`}>{fmtInt(l.anuncios)}</td>
                        <td className={`${CELULA} ${NUM}`}>{fmtInt(l.anuncios_violando)}</td>
                        <td className={`${CELULA} ${NUM}`}>{fmtPct(l.pct_violacao)}</td>
                        <td className={`${CELULA} ${NUM}`}>{fmtDesvio(l.desvio_medio_pma)}</td>
                        <td className={`${CELULA} ${NUM}`}>{fmtMoeda(l.preco_medio)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScrollHint>
            )}
          </Panel>
        </div>
      </div>
      <Panel
        titulo="SKUs com mais anúncios abaixo do PMA"
        descricao="Ranking por anúncios violando. Só entram SKUs com ao menos uma violação."
        semPadding
      >
        {isIndisponivel(skus) ? (
          <BlocoIndisponivel r={skus} compacto />
        ) : skus.linhas.length === 0 ? (
          <StateBlock tipo="vazio" titulo="Nenhum SKU com violação neste recorte" compacto />
        ) : (
          <TableScrollHint>
            <table className="w-full min-w-[720px] tabular-nums">
              <thead className="bg-raised">
                <tr>
                  <th scope="col" className={CABECALHO}>SKU</th>
                  <th scope="col" className={CABECALHO}>Marca</th>
                  <th scope="col" className={CABECALHO}>Exemplo de anúncio</th>
                  <th scope="col" className={`${CABECALHO} ${NUM}`}>Anúncios</th>
                  <th scope="col" className={`${CABECALHO} ${NUM}`}>Violando</th>
                  <th scope="col" className={`${CABECALHO} ${NUM}`}>% violação</th>
                  <th scope="col" className={`${CABECALHO} ${NUM}`}>Desvio médio</th>
                </tr>
              </thead>
              <tbody>
                {skus.linhas.map((l) => (
                  <tr key={l.sku} className="border-t border-line">
                    <td className={`${CELULA} whitespace-nowrap font-medium text-ink`}>{l.sku}</td>
                    <td className={CELULA}>{l.marca}</td>
                    <td className={`${CELULA} max-w-[320px] truncate text-ink-muted`} title={l.exemplo_anuncio ?? undefined}>
                      {l.exemplo_anuncio ?? SEM_DADO}
                    </td>
                    <td className={`${CELULA} ${NUM}`}>{fmtInt(l.anuncios)}</td>
                    <td className={`${CELULA} ${NUM}`}>{fmtInt(l.anuncios_violando)}</td>
                    <td className={`${CELULA} ${NUM}`}>{fmtPct(l.pct_violacao)}</td>
                    <td className={`${CELULA} ${NUM}`}>{fmtDesvio(l.desvio_medio_pma)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScrollHint>
        )}
      </Panel>
    </>
  );
}

function FaixaKpis({ resumo }: { resumo: ResumoOk }) {
  const k = resumo.kpis;
  const cob = resumo.cobertura_identificacao;
  return (
    <section aria-label="Indicadores" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
      <StatCard rotulo="Anúncios monitorados" valor={fmtInt(k.anuncios)} icone={Tag} contexto={`${fmtInt(k.skus_monitorados)} SKUs`} />
      <StatCard
        rotulo="Violação do PMA"
        valor={fmtPct(k.pct_violacao)}
        icone={AlertTriangle}
        tom={k.anuncios_violando > 0 ? "critico" : "neutro"}
        contexto={`${fmtInt(k.anuncios_violando)} anúncios abaixo do PMA`}
      />
      <StatCard rotulo="Sellers violando" valor={fmtInt(k.sellers_violando)} icone={Store} nota="Seller pelo nome normalizado" />
      <StatCard
        rotulo="Desvio médio"
        valor={fmtDesvio(k.desvio_medio_pma)}
        icone={TrendingDown}
        tom={k.desvio_medio_pma ? "atencao" : "neutro"}
        contexto="entre os anúncios que violam"
      />
      <StatCard
        rotulo="Buy box abaixo do PMA"
        valor={fmtInt(k.buy_box.violando_com_buy_box)}
        icone={Trophy}
        tom={k.buy_box.violando_com_buy_box > 0 ? "critico" : "neutro"}
        contexto={`de ${fmtInt(k.buy_box.anuncios_com_buy_box)} anúncios com buy box`}
      />
      <StatCard
        rotulo="Sellers identificados"
        valor={fmtPct(cob.pct_sellers)}
        icone={UserCheck}
        contexto={`${fmtInt(cob.sellers_violando_identificados)} de ${fmtInt(cob.sellers_violando)} sellers violando têm cliente B2B`}
        nota={
          cob.consistente
            ? `${fmtPct(cob.pct_anuncios)} dos anúncios violando`
            : "Contagem diverge do KPI de sellers — confira a fonte"
        }
        tom={cob.consistente ? "neutro" : "atencao"}
      />
    </section>
  );
}

function PainelSeveridade({ resumo }: { resumo: ResumoOk }) {
  const s = resumo.kpis.severidade;
  const total = s.leve + s.media + s.grave;
  const linhas = [
    { ...SEVERIDADES[0], n: s.grave, cor: "bg-crit" },
    { ...SEVERIDADES[1], n: s.media, cor: "bg-warn" },
    { ...SEVERIDADES[2], n: s.leve, cor: "bg-ink-faint" },
  ];
  return (
    <Panel titulo="Severidade das violações" descricao="Faixa do desvio em relação ao PMA." icone={Percent}>
      {total === 0 ? (
        <StateBlock tipo="vazio" titulo="Nenhuma violação neste recorte" compacto />
      ) : (
        <ul className="space-y-3">
          {linhas.map((l) => {
            const pct = participacao(l.n, total);
            return (
              <li key={l.id}>
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="text-ink">
                    <span className="font-semibold">{l.nome}</span>{" "}
                    <span className="text-xs text-ink-muted">{l.faixa}</span>
                  </span>
                  <span className="tabular-nums text-ink">
                    {fmtInt(l.n)} <span className="text-xs text-ink-muted">({pct.toLocaleString("pt-BR")}%)</span>
                  </span>
                </div>
                <div className="mt-1 h-2 overflow-hidden rounded-full bg-raised" aria-hidden="true">
                  <div className={`h-full rounded-full ${l.cor}`} style={{ width: `${pct}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
