import Chip from "@/components/ui/Chip";
import KpiTile from "@/components/ui/KpiTile";
import { ClassificacaoChip } from "@/components/dre/DreChips";
import {
  ROTULO_MARGEM,
  STATUS_CHIP,
  TEXTO_NAO_CALCULAVEL_AGREGADO,
  completudeExibida,
  custosAusentes,
  fmtReais,
  juntarNomes,
  rotuloAusentes,
  semMargem,
  textoMargem,
  textoMargemPct,
  type CelulaDRE,
} from "@/lib/dre";

/**
 * DRE-UX-1/2 — resumo da selecao: receita, margem de contribuicao conhecida, %
 * e classificacao.
 *
 * Margem ausente nao vira "R$ 0" nem travessao: os textos saem de
 * `textoMargem`/`textoMargemPct`, que devolvem o status. O resultado nunca e'
 * chamado de lucro nem de margem liquida.
 *
 * DRE-UX-2: agregado NAO CALCULAVEL nao mostra "0 de 6 custos" (a completude
 * do backend ali e' a intersecao entre celulas e leria "nenhum custo
 * conhecido"); diz "Não calculável — veja os canais" e nomeia os canais que
 * bloqueiam. Margem calculavel traz "Parcial" e o que falta no PROPRIO tile,
 * para o numero grande nunca parecer margem completa.
 */
export default function DreResumo({
  total,
  competencia,
  bloqueadores,
}: {
  total: CelulaDRE;
  competencia: string;
  /** Rotulos dos canais nao calculaveis que impedem o agregado. */
  bloqueadores: string[];
}) {
  const receita = total.componentes.receita;
  const sem = semMargem(total);
  const faltam = custosAusentes(total);
  const motivo = total.motivos_classificacao[0] ?? null;
  const completude = completudeExibida(total);
  const bloqueio = bloqueadores.length
    ? `Bloqueado por ${juntarNomes(bloqueadores)}`
    : motivo
      ? `Motivo: ${motivo}`
      : "Sem margem para esta seleção";

  const leituraMargem = sem ? (
    bloqueio
  ) : (
    <span className="flex flex-wrap items-center gap-1.5">
      <Chip tom={total.classificacao === "COMPLETA" ? "bom" : "atencao"} ponto>
        {total.classificacao === "COMPLETA" ? "Completa" : "Parcial"}
      </Chip>
      <span>
        {faltam.length
          ? `${rotuloAusentes(total)}: ${faltam.join(", ")}`
          : "Todos os custos aplicáveis na conta"}
      </span>
    </span>
  );

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <KpiTile
        rotulo="Receita"
        valor={receita.valor !== null ? fmtReais(receita.valor) : STATUS_CHIP[receita.status]?.rotulo ?? "Sem valor"}
        tom={receita.valor === null ? "ausente" : "neutro"}
        comparacao={
          receita.status === "parcial" ? `Parcial: ${receita.motivo ?? "cobertura incompleta"}` : `Competência ${competencia}`
        }
      />
      <KpiTile
        rotulo={ROTULO_MARGEM}
        valor={textoMargem(total)}
        tom={sem ? "ausente" : "neutro"}
        destaque={!sem}
        comparacao={leituraMargem}
      />
      <KpiTile
        rotulo="% da receita"
        valor={textoMargemPct(total)}
        tom={sem ? "ausente" : "neutro"}
        comparacao={sem ? "Depende da margem ao lado" : "Margem conhecida ÷ receita"}
      />
      <KpiTile
        rotulo="Classificação"
        valor={
          <span className="flex flex-wrap items-center gap-2 text-base">
            <ClassificacaoChip classificacao={total.classificacao} completude={completude} />
            {completude === null ? (
              <span className="text-sm font-semibold text-ink-muted">{TEXTO_NAO_CALCULAVEL_AGREGADO.replace("Não calculável ", "")}</span>
            ) : (
              <span className="text-sm font-semibold text-ink-muted">{completude}</span>
            )}
          </span>
        }
        comparacao={
          completude === null
            ? bloqueio
            : faltam.length
              ? `${rotuloAusentes(total)}: ${faltam.join(", ")}`
              : "Todos os custos aplicáveis presentes"
        }
      />
    </div>
  );
}
