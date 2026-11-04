/**
 * EXPEDICAO-ABA-7D — "Backlog por marca", aba "Últimos 7 dias".
 *
 * Pedido do usuário (03/11/2026): separar o que é AGORA (fila aberta) do que
 * é dos ÚLTIMOS 7 DIAS, porque os números mudam. Aqui, por marca:
 *   - Pedidos: total de pedidos feitos nos 7 dias, o MESMO número do Gerencial;
 *   - Prazo vencido: pedidos com prazo nesses dias despachados pelo vendedor
 *     (sem FBS/Full), a MESMA base do LDR do topo (`due_7d_count`);
 *   - Vencidos, Acima de 48 h, Travados: contados nessa base, e o % é sobre
 *     ela (04/11/2026: sobre o total de pedidos, o % não batia com o LDR).
 * Sem "perto de vencer" (é uma medida de agora).
 *
 * Ausência é "—", nunca zero: conta sem cálculo publicado, marca com mais de
 * uma conta (o Gerencial é por marca) ou medida que o canal não sustenta
 * (travados no Mercado Livre).
 */
import InfoDica from "@/components/ui/InfoDica";
import { formatarInteiro } from "@/lib/expedicao-formato";
import { percentualDaFila } from "@/lib/expedicao-travados";

export interface LinhaSeteDias {
  conta: string;
  titulo: string;
  pedidos: number | null;
  /** Pedidos com prazo vencido nos 7 dias, despachados pelo vendedor: a base do LDR. */
  baseLdr: number | null;
  vencidos: number | null;
  acima48h: number | null;
  travados: number | null;
}

interface Props {
  linhas: LinhaSeteDias[];
  total: LinhaSeteDias | null;
  /** "27/10 a 02/11" */
  janela: string | null;
  /** O canal sustenta "travados" nesta janela? (ML: não). */
  comTravados: boolean;
}

// A mesma etiqueta do "Vencidos" da aba "Agora" (EvolucaoPorMarca).
const ETIQUETA_VENCIDO = "rounded-md bg-crit-soft px-2 py-0.5 font-bold text-crit-ink ring-1 ring-crit/30";

function Celula({ valor, base, critico = false }: { valor: number | null; base: number | null; critico?: boolean }) {
  if (valor == null) return <span className="text-ink-faint">—</span>;
  const pct = base != null ? percentualDaFila(valor, base) : null;
  return (
    <span className="whitespace-nowrap">
      {/* UX-VIDRO-4: vencido com valor ganha a mesma etiqueta da aba "Agora";
          o zero fica com o mesmo recuo, para a coluna alinhar. */}
      <span className={critico ? (valor > 0 ? ETIQUETA_VENCIDO : "tc-vencido-zero") : undefined}>
        {formatarInteiro(valor)}
      </span>
      {pct ? (
        <>
          <span className="sr-only"> · </span>
          <span className="ml-1.5 tc-pct text-xs font-normal text-ink-faint">{pct}</span>
        </>
      ) : null}
    </span>
  );
}

function Linha({ l, comTravados, total = false }: { l: LinhaSeteDias; comTravados: boolean; total?: boolean }) {
  return (
    <tr className={total ? "tc-linha-total font-semibold" : undefined}>
      <th scope="row" className="text-left font-semibold text-ink">{l.titulo}</th>
      <td className="text-right tabular-nums">
        {l.pedidos == null ? <span className="text-ink-faint">—</span> : formatarInteiro(l.pedidos)}
      </td>
      <td className="text-right tabular-nums">
        {l.baseLdr == null ? <span className="text-ink-faint">—</span> : formatarInteiro(l.baseLdr)}
      </td>
      <td className="text-right tabular-nums tc-num-pct"><Celula valor={l.vencidos} base={l.baseLdr} critico /></td>
      <td className="text-right tabular-nums tc-num-pct"><Celula valor={l.acima48h} base={l.baseLdr} /></td>
      <td className="text-right tabular-nums tc-num-pct">
        {comTravados ? <Celula valor={l.travados} base={l.baseLdr} /> : <span className="text-ink-faint">—</span>}
      </td>
    </tr>
  );
}

export default function TabelaSeteDias({ linhas, total, janela, comTravados }: Props) {
  return (
    <div data-testid="exp-tabela-7d">
      {/* UX-VIDRO-4 ("quanto menos texto, melhor"): na tela, so' o periodo. A
          regra inteira continua no "ⓘ" — encurtada, nunca apagada. */}
      <p className="mb-1 flex items-center gap-1.5 text-xs text-ink-muted">
        <span>
          Pedidos de <span className="font-semibold text-ink">{janela ?? "últimos 7 dias fechados"}</span>
        </span>
        <InfoDica
          texto={`${janela ? `Pedidos feitos de ${janela}` : "Últimos 7 dias fechados"}: o mesmo total do Gerencial, com Full/FBS. Prazo vencido: os pedidos com prazo nesses dias despachados pelo vendedor, a mesma base do LDR. Vencidos, acima de 48 h e travados contam essa base, e o % é sobre ela.${comTravados ? "" : " Travados não é medido neste canal."}`}
        />
      </p>
      <div className="overflow-x-auto">
        <table className="tc-tabela-linhas tc-tabela-num-pct w-full text-sm [&_td]:pr-4 [&_th]:pr-4 [&_thead_th]:align-bottom">
          <caption className="sr-only">Últimos 7 dias por marca: pedidos, prazo vencido, vencidos, acima de 48 h e travados</caption>
          <thead className="text-xs uppercase tracking-wide text-ink-faint">
            <tr>
              <th scope="col" className="w-[30%] text-left">Marca</th>
              <th scope="col" className="text-right">Pedidos</th>
              <th scope="col" className="text-right">Prazo vencido</th>
              <th scope="col" className="text-right">Vencidos</th>
              <th scope="col" className="text-right">Acima de 48 h</th>
              <th scope="col" className="text-right">Travados</th>
            </tr>
          </thead>
          <tbody>
            {linhas.map((l) => (
              <Linha key={l.conta} l={l} comTravados={comTravados} />
            ))}
            {total ? <Linha l={total} comTravados={comTravados} total /> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
