/**
 * T-0014 — tela "Performance das contas", INERTE (fail-closed).
 *
 * A checagem da flag acontece no SERVIDOR, antes de qualquer render. Com
 * `NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED` diferente de "true" o Next devolve
 * 404 e nenhuma requisicao a API acontece. Ainda nao ha item de menu.
 *
 * Contrato: docs/performance_contas_contrato.md. Persona: gestor/operador de
 * marketplace. Decisao: qual loja esta' perto de ser penalizada e onde agir.
 *
 * Nenhum dado demonstrativo: com a API fora ou payload invalido a tela inteira
 * mostra erro, nunca numero.
 */
import { notFound } from "next/navigation";

import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import Panel from "@/components/ui/Panel";
import StateBlock from "@/components/ui/StateBlock";
import { ALL_BRAND_KEYS, ALL_BRAND_OPTIONS, brandLabel } from "@/lib/filters/brands";
import {
  CANAIS,
  carregarPerformanceContas,
  formatarInstante,
  habilitado,
  lerFiltros,
  montarCanais,
  type FiltrosPerformanceContas,
  type VisaoCanal,
} from "@/lib/performance-contas";

import {
  ABAS,
  JANELAS_DIAS,
  atendimentoHabilitado,
  carregarAtendimento,
  lerAba,
  lerDias,
  montarCanaisAtendimento,
  textoJanela,
  type Aba,
  type JanelaDias,
} from "@/lib/performance-contas-atendimento";

import AtendimentoCanal from "./AtendimentoCanal";
import IndicadorItem from "./IndicadorItem";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

export default async function PerformanceContasPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  if (!habilitado()) notFound();

  const params = await searchParams;
  const filtros = lerFiltros(params, ALL_BRAND_KEYS);
  // CX-MKT-1: a aba "Atendimento" tem flag propria; desligada, `?aba=` e' ignorado.
  const abaHabilitada = atendimentoHabilitado();
  const nav: Navegacao = { ...filtros, aba: lerAba(params, abaHabilitada), dias: lerDias(params) };

  if (nav.aba === "atendimento") {
    return <AbaAtendimento nav={nav} />;
  }

  const resultado = await carregarPerformanceContas(filtros);

  const escopo = resultado.ok
    ? `Gerado em ${formatarInstante(resultado.dados.gerado_em) ?? resultado.dados.gerado_em}`
    : "Sem dados nesta carga";

  return (
    <PageContainer>
      <PageHeader
        title="Performance das contas"
        scopeLine={escopo}
        filters={<Filtros nav={nav} />}
      />
      {abaHabilitada && <Abas nav={nav} />}

      {!resultado.ok ? (
        <StateBlock
          tipo="erro"
          titulo="Não foi possível carregar a performance das contas"
          descricao={resultado.motivo}
          acao={
            <a
              href={hrefAtual(nav)}
              className="mt-1 inline-flex min-h-11 items-center rounded-lg border border-current/30 px-3 text-xs font-semibold hover:bg-surface/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Tentar novamente
            </a>
          }
        />
      ) : (
        <Canais canais={montarCanais(resultado.dados, filtros.canal, brandLabel)} />
      )}
    </PageContainer>
  );
}

interface Navegacao extends FiltrosPerformanceContas {
  aba: Aba;
  dias: JanelaDias;
}

function hrefAtual(f: Navegacao, aba: Aba = f.aba): string {
  const qs = new URLSearchParams();
  if (aba === "atendimento") qs.set("aba", "atendimento");
  if (f.canal) qs.set("canal", f.canal);
  if (f.marca) qs.set("brands", f.marca);
  if (aba === "atendimento" && f.dias !== 30) qs.set("dias", String(f.dias));
  const s = qs.toString();
  return s ? `/performance-contas?${s}` : "/performance-contas";
}

/**
 * Abas por LINK (server component, sem JS): a URL e' a fonte de verdade e a
 * aba pode ser compartilhada. Canal e marca seguem ao trocar de aba.
 */
function Abas({ nav }: { nav: Navegacao }) {
  return (
    <nav aria-label="Seções da Performance das contas" className="border-b border-line">
      <ul className="flex gap-1">
        {ABAS.map((a) => {
          const ativa = a.valor === nav.aba;
          return (
            <li key={a.valor}>
              <a
                href={hrefAtual(nav, a.valor)}
                aria-current={ativa ? "page" : undefined}
                className={`inline-flex min-h-11 items-center border-b-2 px-3 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                  ativa ? "border-accent text-accent" : "border-transparent text-ink-muted hover:text-ink"
                }`}
              >
                {a.texto}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Aba "Atendimento": so' e' chamada com a flag ligada (`lerAba`). */
async function AbaAtendimento({ nav }: { nav: Navegacao }) {
  const resultado = await carregarAtendimento({ marca: nav.marca, dias: nav.dias });
  const escopo = resultado.ok
    ? `${textoJanela(resultado.dados.janela)} · gerado em ${
        formatarInstante(resultado.dados.gerado_em) ?? resultado.dados.gerado_em
      } · cálculo Torre`
    : "Sem dados nesta carga";
  const canais = resultado.ok ? montarCanaisAtendimento(resultado.dados, nav.canal, brandLabel) : [];

  return (
    <PageContainer>
      <PageHeader title="Performance das contas" scopeLine={escopo} filters={<Filtros nav={nav} />} />
      <Abas nav={nav} />

      {!resultado.ok ? (
        <StateBlock
          tipo="erro"
          titulo="Não foi possível carregar o atendimento"
          descricao={resultado.motivo}
          acao={
            <a
              href={hrefAtual(nav)}
              className="mt-1 inline-flex min-h-11 items-center rounded-lg border border-current/30 px-3 text-xs font-semibold hover:bg-surface/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Tentar novamente
            </a>
          }
        />
      ) : canais.length === 0 ? (
        <StateBlock tipo="vazio" titulo="Nenhum canal no recorte" />
      ) : (
        canais.map((c) => <AtendimentoCanal key={c.canal} canal={c} />)
      )}
    </PageContainer>
  );
}

/**
 * Filtro local por formulario GET: funciona sem JS e mantem a URL como fonte
 * de verdade. Valores fora da allowlist ja' foram descartados em `lerFiltros`.
 */
function Filtros({ nav }: { nav: Navegacao }) {
  const filtros = nav;
  const campo =
    "min-h-11 rounded-md border border-line bg-surface px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
  return (
    <form method="get" action="/performance-contas" className="flex flex-wrap items-end gap-3">
      {nav.aba === "atendimento" && <input type="hidden" name="aba" value="atendimento" />}
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Canal
        <select name="canal" defaultValue={filtros.canal ?? ""} className={campo}>
          <option value="">Todos os canais</option>
          {CANAIS.map((c) => (
            <option key={c.valor} value={c.valor}>
              {c.texto}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Marca
        <select name="brands" defaultValue={filtros.marca ?? ""} className={campo}>
          <option value="">Todas as marcas</option>
          {ALL_BRAND_OPTIONS.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
      </label>
      {nav.aba === "atendimento" && (
        <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
          Período
          <select name="dias" defaultValue={String(nav.dias)} className={campo}>
            {JANELAS_DIAS.map((d) => (
              <option key={d} value={d}>
                Últimos {d} dias
              </option>
            ))}
          </select>
        </label>
      )}
      <button
        type="submit"
        className="min-h-11 rounded-md border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Aplicar
      </button>
    </form>
  );
}

function Canais({ canais }: { canais: VisaoCanal[] }) {
  if (canais.length === 0) {
    return (
      <StateBlock
        tipo="vazio"
        titulo="Nenhum canal no recorte"
      />
    );
  }
  return (
    <>
      {canais.map((c) => (
        <BlocoCanal key={c.canal} canal={c} />
      ))}
    </>
  );
}

/** Desenha so' o que `montarCanais` ja' resolveu (testado em node --test). */
function BlocoCanal({ canal }: { canal: VisaoCanal }) {
  return (
    <Panel titulo={canal.titulo} descricao={canal.descricao} aria-label={`Performance das contas: ${canal.titulo}`}>
      {canal.fora.length > 0 && (
        <div className="mb-3 rounded-lg border border-dashed border-line px-3 py-2 text-xs text-ink-muted">
          <p className="font-medium">Fora da cobertura</p>
          <ul className="mt-1 list-disc pl-4">
            {canal.fora.map((f) => (
              <li key={f.chave}>{f.texto}</li>
            ))}
          </ul>
        </div>
      )}
      {canal.indicadores.length === 0 ? (
        <StateBlock tipo="vazio" compacto titulo="Nenhum indicador publicado para este canal" />
      ) : (
        <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {canal.indicadores.map((v) => (
            <IndicadorItem key={v.chave} v={v} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
