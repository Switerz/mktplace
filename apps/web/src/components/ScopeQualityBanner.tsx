import type { ScopeQuality, ScopeQualityWarning } from "@/lib/api-client";

interface Props {
  quality: ScopeQuality | null | undefined;
  loading?: boolean;
}

/**
 * Gate SH-API-2D — selo de confiabilidade do escopo (marca x competencia).
 *
 * Por que existe: ate aqui a tela de Produtos mostrava QUALQUER competencia
 * com o mesmo peso visual. Julho/2026 aparecia com R$ 5,5 mi e nenhum sinal de
 * que a fonte ainda nao havia concluido ~25% do GMV que a diaria ja registrava;
 * agosto/2026 aparecia vazio sem dizer que havia 188 linhas carregadas, todas
 * com GMV zero. Quem olhava tirava a conclusao errada com o numero certo.
 *
 * Tres regras de honestidade que esta faixa nao pode quebrar:
 *
 *  1. `quality == null` NAO e sinal verde. Significa "nao medido" (canal que
 *     ainda nao publica o selo, ou janela sem competencia unica) e por isso
 *     nao renderiza nada — em vez de renderizar um "ok" que ninguem apurou.
 *  2. Os eixos sao ORTOGONAIS e todos os avisos aparecem. Mostrar so o mais
 *     grave esconderia, por exemplo, a carga defasada atras da imaturidade —
 *     que e exatamente o defeito que este contrato veio corrigir.
 *  3. A data de publicacao do mart aparece SEMPRE que existir, inclusive no
 *     caso saudavel: "esta certo" e "esta certo e foi publicado ha 34 dias"
 *     levam a decisoes diferentes.
 */
export default function ScopeQualityBanner({ quality, loading }: Props) {
  if (loading && !quality) {
    return <div className="mx-6 mt-3 h-9 rounded-xl bg-slate-50 animate-pulse" aria-hidden />;
  }
  if (!quality) return null;

  const publicacao = publicationLine(quality);

  if (quality.definitive && quality.warnings.length === 0) {
    return (
      <p className="px-6 pt-2 text-[11px] text-slate-400" data-testid="scope-quality-ok">
        Competência fechada e conferida.{publicacao ? ` ${publicacao}` : ""}
      </p>
    );
  }

  const pior = severityRank(quality.warnings);
  const tom = pior === "critical"
    ? { box: "bg-rose-50 border-rose-200", title: "text-rose-900", body: "text-rose-800", meta: "text-rose-500" }
    : pior === "warning"
      ? { box: "bg-amber-50 border-amber-200", title: "text-amber-900", body: "text-amber-800", meta: "text-amber-600" }
      : { box: "bg-slate-50 border-slate-200", title: "text-slate-800", body: "text-slate-700", meta: "text-slate-500" };

  return (
    <div
      className={`mx-6 mt-3 rounded-xl border px-4 py-3 ${tom.box}`}
      role={pior === "critical" ? "alert" : "status"}
      data-testid="scope-quality-banner"
      data-severity={pior}
    >
      <p className={`text-xs font-semibold ${tom.title}`}>{headline(quality)}</p>
      <ul className={`mt-1.5 space-y-1 text-[11px] ${tom.body}`}>
        {quality.warnings.map((w) => (
          <li key={w.code} data-code={w.code}>
            {w.severity === "critical" ? "• " : "· "}
            {w.message}
          </li>
        ))}
      </ul>
      {publicacao && <p className={`mt-1.5 text-[11px] ${tom.meta}`}>{publicacao}</p>}
    </div>
  );
}

/** Titulo curto: diz o que FAZER com o numero, nao o nome tecnico do estado. */
function headline(q: ScopeQuality): string {
  if (q.load_status === "load_absent") return "Sem dados de produto nesta competência";
  if (q.eligibility_status === "no_eligible_rows") return "Nenhum produto concluído nesta competência";
  if (q.maturity_status === "materially_immature") return "Competência ainda em maturação — números vão subir";
  if (q.source_status === "source_not_covered") return "A fonte ainda não cobriu esta competência";
  if (q.maturity_status === "maturity_unknown") return "Confiabilidade não verificada nesta competência";
  return "Atenção ao escopo";
}

function publicationLine(q: ScopeQuality): string | null {
  if (!q.loaded_at) return null;
  const data = new Date(q.loaded_at);
  if (Number.isNaN(data.getTime())) return null;
  const quando = data.toLocaleDateString("pt-BR");
  const idade = q.load_age_days;
  // "há 0 dias" e ruido; "hoje" e informacao.
  const sufixo = idade === null || idade === undefined
    ? ""
    : idade === 0 ? " (hoje)" : idade === 1 ? " (há 1 dia)" : ` (há ${idade} dias)`;
  return `Mart de Produtos publicado em ${quando}${sufixo}.`;
}

function severityRank(warnings: ScopeQualityWarning[]): "critical" | "warning" | "info" {
  if (warnings.some((w) => w.severity === "critical")) return "critical";
  if (warnings.some((w) => w.severity === "warning")) return "warning";
  return "info";
}
