import Chip from "@/components/ui/Chip";
import { CLASSIFICACAO_CHIP, STATUS_CHIP, type Classificacao, type StatusComponente } from "@/lib/dre";

/**
 * DRE-UX-1 — selos da DRE. O texto vem SEMPRE junto da cor: "Não calculável"
 * pintado so' de vermelho seria ilegivel para quem nao distingue a cor.
 */
export function ClassificacaoChip({
  classificacao,
  completude,
  motivo,
}: {
  classificacao: Classificacao;
  /** "4 de 6 custos" — acompanha o selo quando a classificacao e' parcial. */
  completude?: string;
  motivo?: string | null;
}) {
  const c = CLASSIFICACAO_CHIP[classificacao];
  return (
    <Chip tom={c.tom} ponto title={motivo ?? undefined}>
      {c.rotulo}
      {completude && classificacao !== "NAO_CALCULAVEL" ? ` · ${completude}` : ""}
    </Chip>
  );
}

export function StatusChip({ status, motivo }: { status: StatusComponente; motivo?: string | null }) {
  const c = STATUS_CHIP[status];
  if (!c) return null;
  return (
    <Chip tom={c.tom} title={motivo ?? undefined}>
      {c.rotulo}
    </Chip>
  );
}
