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
  filtrarCanais,
  formatarInstante,
  habilitado,
  lerFiltros,
  rotuloCanal,
  type CanalPerformance,
  type FiltrosPerformanceContas,
} from "@/lib/performance-contas";

import IndicadorItem from "./IndicadorItem";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

export default async function PerformanceContasPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  if (!habilitado()) notFound();

  const filtros = lerFiltros(await searchParams, ALL_BRAND_KEYS);
  const resultado = await carregarPerformanceContas(filtros);

  const escopo = resultado.ok
    ? `Gerado em ${formatarInstante(resultado.dados.gerado_em) ?? resultado.dados.gerado_em}`
    : "Sem dados nesta carga";

  return (
    <PageContainer>
      <PageHeader
        title="Performance das contas"
        subtitle="Saúde e reputação das contas por marketplace, separando métrica oficial da plataforma e cálculo da Torre."
        scopeLine={escopo}
        filters={<Filtros filtros={filtros} />}
      />

      {!resultado.ok ? (
        <StateBlock
          tipo="erro"
          titulo="Não foi possível carregar a performance das contas"
          descricao={resultado.motivo}
          acao={
            <a
              href={hrefAtual(filtros)}
              className="mt-1 inline-flex min-h-11 items-center rounded-lg border border-current/30 px-3 text-xs font-semibold hover:bg-surface/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Tentar novamente
            </a>
          }
        />
      ) : (
        <Canais canais={filtrarCanais(resultado.dados.canais, filtros.canal)} />
      )}
    </PageContainer>
  );
}

function hrefAtual(f: FiltrosPerformanceContas): string {
  const qs = new URLSearchParams();
  if (f.canal) qs.set("canal", f.canal);
  if (f.marca) qs.set("brands", f.marca);
  const s = qs.toString();
  return s ? `/performance-contas?${s}` : "/performance-contas";
}

/**
 * Filtro local por formulario GET: funciona sem JS e mantem a URL como fonte
 * de verdade. Valores fora da allowlist ja' foram descartados em `lerFiltros`.
 */
function Filtros({ filtros }: { filtros: FiltrosPerformanceContas }) {
  const campo =
    "min-h-11 rounded-md border border-line bg-surface px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
  return (
    <form method="get" action="/performance-contas" className="flex flex-wrap items-end gap-3">
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
      <button
        type="submit"
        className="min-h-11 rounded-md border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Aplicar
      </button>
    </form>
  );
}

function Canais({ canais }: { canais: CanalPerformance[] }) {
  if (canais.length === 0) {
    return (
      <StateBlock
        tipo="vazio"
        titulo="Nenhum canal no recorte"
        descricao="A API respondeu, mas não trouxe o canal selecionado."
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

function BlocoCanal({ canal }: { canal: CanalPerformance }) {
  const { cobertura } = canal;
  const marcas = cobertura.marcas.map(brandLabel).join(", ");
  return (
    <Panel
      titulo={rotuloCanal(canal.canal)}
      descricao={marcas ? `Cobertura: ${marcas}` : "Cobertura: nenhuma conta coberta"}
      aria-label={`Performance das contas: ${rotuloCanal(canal.canal)}`}
    >
      {cobertura.fora.length > 0 && (
        <div className="mb-3 rounded-lg border border-dashed border-line px-3 py-2 text-xs text-ink-muted">
          <p className="font-medium">Fora da cobertura</p>
          <ul className="mt-1 list-disc pl-4">
            {cobertura.fora.map((f) => (
              <li key={f.marca}>
                {brandLabel(f.marca)}: {f.motivo}
              </li>
            ))}
          </ul>
        </div>
      )}
      {canal.indicadores.length === 0 ? (
        <StateBlock tipo="vazio" compacto titulo="Nenhum indicador publicado para este canal" />
      ) : (
        <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {canal.indicadores.map((ind) => (
            <IndicadorItem key={ind.codigo} ind={ind} canal={canal.canal} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
