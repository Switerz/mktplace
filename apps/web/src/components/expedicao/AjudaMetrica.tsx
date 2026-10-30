/**
 * T-0010 — o "ⓘ" das metricas da Expedicao.
 *
 * Duas frases do catalogo (`expedicao-definicoes.ts`): a definicao e a
 * limitacao — a ressalva que muda a leitura ("limiar interno, não é SLA").
 * Formula, fonte, janela e cobertura ficam no catalogo, fora da tela.
 *
 * E' o mesmo `InfoDica` do resto da Torre: focavel (Tab ou toque), com o
 * texto no `aria-label` e no balao. So' `<span>`: o "ⓘ" mora dentro de `<p>`
 * (a linha de comparacao do KpiTile).
 *
 * EXPEDICAO-DICA-LEGIVEL: no balao, o nome da metrica em negrito, a definicao
 * e a ressalva separada embaixo. O leitor de tela continua lendo
 * `textoDaAjuda` (definicao + limitacao).
 */
import InfoDica from "@/components/ui/InfoDica";
import { definicaoDe, type ChaveMetrica } from "@/lib/expedicao-definicoes";

/** O texto do "ⓘ": definicao + limitacao, sem a metodologia. */
export function textoDaAjuda(chave: ChaveMetrica): string {
  const def = definicaoDe(chave);
  return `${def.definicao} ${def.limitacoes}`;
}

export default function AjudaMetrica({ chave, className = "" }: { chave: ChaveMetrica; className?: string }) {
  const def = definicaoDe(chave);
  return (
    <InfoDica
      titulo={def.nome}
      texto={def.definicao}
      nota={def.limitacoes}
      className={`align-middle ${className}`}
    />
  );
}
