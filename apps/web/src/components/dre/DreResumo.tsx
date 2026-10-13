import KpiTile from "@/components/ui/KpiTile";
import { ClassificacaoChip } from "@/components/dre/DreChips";
import {
  CLASSIFICACAO_CHIP,
  ROTULO_MARGEM,
  STATUS_CHIP,
  custosAusentes,
  fmtPctDre,
  fmtReais,
  type CelulaDRE,
} from "@/lib/dre";

/**
 * DRE-UX-1 — resumo da selecao: receita, margem de contribuicao conhecida, %
 * e classificacao com completude.
 *
 * Margem ausente nao vira "R$ 0" nem travessao: o tile diz "Não calculável" e
 * a primeira razao do backend aparece embaixo. O resultado nunca e' chamado de
 * lucro nem de margem liquida.
 */
export default function DreResumo({ total, competencia }: { total: CelulaDRE; competencia: string }) {
  const receita = total.componentes.receita;
  const m = total.margem_contribuicao_conhecida;
  const naoCalc = total.classificacao === "NAO_CALCULAVEL" || m.valor === null;
  const pct = fmtPctDre(m.pct_receita);
  const faltam = custosAusentes(total);
  const motivo = total.motivos_classificacao[0] ?? null;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <KpiTile
        rotulo="Receita"
        valor={receita.valor !== null ? fmtReais(receita.valor) : STATUS_CHIP[receita.status]?.rotulo ?? "Sem valor"}
        tom={receita.valor === null ? "ausente" : "neutro"}
        comparacao={
          receita.status === "parcial" ? `Parcial: ${receita.motivo ?? "cobertura incompleta"}` : `Competência ${competencia}`
        }
        contexto="GMV do mart por competência. No TikTok inclui o frete pago pelo comprador."
      />
      <KpiTile
        rotulo={ROTULO_MARGEM}
        valor={naoCalc || m.valor === null ? "Não calculável" : fmtReais(m.valor)}
        tom={naoCalc ? "ausente" : "neutro"}
        destaque={!naoCalc}
        comparacao={naoCalc ? (motivo ? `Motivo: ${motivo}` : "Sem margem para esta seleção") : "Receita menos os custos conhecidos"}
        contexto="Não é lucro nem margem líquida: impostos e devoluções ainda não têm fonte."
      />
      <KpiTile
        rotulo="% da receita"
        valor={naoCalc || pct === null ? "Não calculável" : pct}
        tom={naoCalc || pct === null ? "ausente" : "neutro"}
        comparacao={naoCalc ? "Depende da margem acima" : "Margem conhecida ÷ receita"}
      />
      <KpiTile
        rotulo="Classificação"
        valor={
          <span className="flex flex-wrap items-center gap-2 text-base">
            <ClassificacaoChip classificacao={total.classificacao} motivo={motivo} />
            <span className="text-sm font-semibold text-ink-muted">{total.completude.texto}</span>
          </span>
        }
        comparacao={faltam.length ? `Faltam: ${faltam.join(", ")}` : "Todos os custos aplicáveis presentes"}
        contexto={`${CLASSIFICACAO_CHIP[total.classificacao].rotulo}: ${total.motivos_classificacao.join(" · ") || "todos os requisitos atendidos"}`}
      />
    </div>
  );
}
