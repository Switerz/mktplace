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
 * que o mart estava materialmente atras da diaria; agosto/2026 aparecia vazio
 * sem dizer que havia 188 linhas carregadas, todas com GMV zero. Quem olhava
 * tirava a conclusao errada com o numero certo.
 *
 * Quatro regras de honestidade que esta faixa nao pode quebrar:
 *
 *  1. `quality == null` NAO e sinal verde. Significa "nao medido" (canal que
 *     ainda nao publica o selo, ou janela sem competencia unica) e por isso
 *     nao renderiza nada — em vez de renderizar um "ok" que ninguem apurou.
 *  2. Os eixos sao ORTOGONAIS e todos os avisos aparecem. Mostrar so o mais
 *     grave esconderia, por exemplo, a carga atras da diaria atras da
 *     maturacao — que e exatamente o defeito que este contrato veio corrigir.
 *  3. DOIS RELOGIOS, sempre nomeados. `loaded_at` e a publicacao NO MART,
 *     nunca a atualizacao do dado na Shopee. Um mart publicado ha 34 dias
 *     pode conter um mes fechado perfeito — e tambem pode nao ter visto a
 *     venda de ontem. A faixa diz qual relogio esta lendo, sempre.
 *  4. O indice operacional de maturacao NUNCA aparece sozinho. Ele chega
 *     dentro da mensagem do backend, que ja carrega a explicacao de que nao e
 *     percentual de conclusao (`maturation_index_note`). Esta faixa nao
 *     formata o numero por conta propria, justamente para nao poder exibi-lo
 *     como "%".
 */
export default function ScopeQualityBanner({ quality, loading }: Props) {
  if (loading && !quality) {
    return <div className="mx-6 mt-3 h-9 rounded-xl bg-slate-50 animate-pulse" aria-hidden />;
  }
  if (!quality) return null;

  const publicacao = publicationLine(quality);
  const pior = severityRank(quality.warnings);

  // Competencia definitiva NAO vira caixa de alerta. Achado do QA em Chromium:
  // junho/2026 — madura, definitiva, com um unico aviso `info` de 9 linhas em
  // 470 fora da exibicao por GMV = 0 — saia com a caixa ambar e o titulo
  // generico "Atencao ao escopo". Isso e o erro espelhado do que este contrato
  // veio corrigir: alarmar um mes bom gasta a credibilidade do sinal, e quem ve
  // alarme em todo mes para de ler o alarme no mes que importa. O aviso `info`
  // continua visivel, em tom calmo, porque ele explica a diferenca entre
  // `total_count` e `eligible_count` nos cards.
  const calmo = quality.definitive && pior === "info";

  if (calmo || quality.warnings.length === 0) {
    return (
      <div className="px-6 pt-2" data-testid="scope-quality-ok">
        <p className="text-xs text-slate-500">
          Competência madura nos seis eixos.{publicacao ? ` ${publicacao}` : ""}
        </p>
        {quality.warnings.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-xs text-slate-400">
            {quality.warnings.map((w) => (
              <li key={w.code} data-code={w.code}>· {w.message}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  const tom = pior === "critical"
    ? { box: "bg-rose-50 border-rose-200", title: "text-rose-900", body: "text-rose-800", meta: "text-rose-600" }
    : pior === "warning"
      ? { box: "bg-amber-50 border-amber-200", title: "text-amber-900", body: "text-amber-800", meta: "text-amber-700" }
      : { box: "bg-slate-50 border-slate-200", title: "text-slate-800", body: "text-slate-700", meta: "text-slate-600" };

  return (
    <div
      className={`mx-6 mt-3 rounded-xl border px-4 py-3 ${tom.box}`}
      role={pior === "critical" ? "alert" : "status"}
      data-testid="scope-quality-banner"
      data-severity={pior}
    >
      <p className={`text-xs font-semibold ${tom.title}`}>{headline(quality)}</p>
      <ul className={`mt-1.5 space-y-1 text-xs ${tom.body}`}>
        {quality.warnings.map((w) => (
          <li key={w.code} data-code={w.code}>
            {w.severity === "critical" ? "• " : "· "}
            {w.message}
          </li>
        ))}
      </ul>
      {publicacao && <p className={`mt-1.5 text-xs ${tom.meta}`}>{publicacao}</p>}
    </div>
  );
}

/** Titulo curto: diz o que FAZER com o numero, nao o nome tecnico do estado. */
function headline(q: ScopeQuality): string {
  if (q.load_status === "load_absent") return "Sem dados de produto nesta competência";
  if (q.eligibility_status === "no_eligible_rows") return "Nenhum produto concluído nesta competência";
  if (q.maturity_status === "materially_immature") return "Competência ainda em maturação — números vão subir";
  if (q.source_status === "source_never_loaded") return "Esta competência nunca foi carregada da fonte";
  if (q.load_status === "load_behind_daily") return "O mart está atrás da diária nesta competência";
  if (q.maturity_status === "maturity_unknown") return "Maturação não verificada nesta competência";
  return "Atenção ao escopo";
}

/**
 * Linha da publicacao. Nomeia o relogio de forma explicita: nunca diz
 * "atualizado", que seria lido como frescor da fonte.
 */
function publicationLine(q: ScopeQuality): string | null {
  if (!q.loaded_at) return null;
  const data = new Date(q.loaded_at);
  if (Number.isNaN(data.getTime())) return null;
  const quando = data.toLocaleDateString("pt-BR");
  const idade = q.load_age_days;
  // "há 0 dias" e ruido; "hoje" e informacao.
  const sufixo = idade === null || idade === undefined
    ? ""
    : idade === 0 ? ", hoje" : idade === 1 ? ", há 1 dia" : `, há ${idade} dias`;
  return `Publicado no mart em ${quando}${sufixo} — esta é a data da publicação no mart, não da última atualização do dado na Shopee.`;
}

function severityRank(warnings: ScopeQualityWarning[]): "critical" | "warning" | "info" {
  if (warnings.some((w) => w.severity === "critical")) return "critical";
  if (warnings.some((w) => w.severity === "warning")) return "warning";
  return "info";
}
