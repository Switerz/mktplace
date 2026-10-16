import Chip from "@/components/ui/Chip";
import { CLASSIFICACAO_CHIP, STATUS_CHIP, type Classificacao, type StatusComponente } from "@/lib/dre";

/**
 * DRE-UX-1 — selos da DRE. O texto vem SEMPRE junto da cor: "Não calculável"
 * pintado so' de vermelho seria ilegivel para quem nao distingue a cor.
 *
 * DRE-UX-2: os selos nao carregam mais o motivo em `title`. Tooltip nativo nao
 * abre por toque nem por teclado; o motivo aparece escrito ao lado de cada
 * selo, onde ele importa.
 */
export function ClassificacaoChip({
  classificacao,
  completude,
}: {
  classificacao: Classificacao;
  /** "4 de 6 custos". `null` = nao exibir (agregado nao calculavel). */
  completude?: string | null;
}) {
  const c = CLASSIFICACAO_CHIP[classificacao];
  return (
    <Chip tom={c.tom} ponto>
      {c.rotulo}
      {completude && classificacao !== "NAO_CALCULAVEL" ? ` · ${completude}` : ""}
    </Chip>
  );
}

export function StatusChip({ status }: { status: StatusComponente }) {
  const c = STATUS_CHIP[status];
  if (!c) return null;
  return <Chip tom={c.tom}>{c.rotulo}</Chip>;
}
