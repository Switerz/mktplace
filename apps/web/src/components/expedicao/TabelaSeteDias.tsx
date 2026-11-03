/**
 * EXPEDICAO-ABA-7D — "Backlog por marca", aba "Últimos 7 dias".
 *
 * Pedido do usuário (03/11/2026): separar o que é AGORA (fila aberta) do que
 * é dos ÚLTIMOS 7 DIAS, porque os números mudam. Aqui, por marca:
 *   - Pedidos: total de pedidos feitos nos 7 dias, o MESMO número do Gerencial;
 *   - Vencidos, Acima de 48 h, Travados: contados nos pedidos com prazo nesses
 *     dias, despachados pelo vendedor; o % é sobre o total de pedidos.
 * Sem "perto de vencer" (é uma medida de agora).
 *
 * Ausência é "—", nunca zero: conta sem cálculo publicado, marca com mais de
 * uma conta (o Gerencial é por marca) ou medida que o canal não sustenta
 * (travados no Mercado Livre).
 */
import { formatarInteiro } from "@/lib/expedicao-formato";
import { percentualDaFila } from "@/lib/expedicao-travados";

export interface LinhaSeteDias {
  conta: string;
  titulo: string;
  pedidos: number | null;
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

function Celula({ valor, base, critico = false }: { valor: number | null; base: number | null; critico?: boolean }) {
  if (valor == null) return <span className="text-ink-faint">—</span>;
  const pct = base != null ? percentualDaFila(valor, base) : null;
  return (
    <span className="whitespace-nowrap">
      <span className={critico && valor > 0 ? "font-semibold text-crit-ink" : undefined}>
        {formatarInteiro(valor)}
      </span>
      {pct ? (
        <>
          <span className="sr-only"> · </span>
          <span className="ml-1.5 text-xs font-normal text-ink-faint">{pct}</span>
        </>
      ) : null}
    </span>
  );
}

function Linha({ l, comTravados, total = false }: { l: LinhaSeteDias; comTravados: boolean; total?: boolean }) {
  return (
    <tr className={total ? "border-t border-line font-semibold" : undefined}>
      <th scope="row" className="text-left font-semibold text-ink">{l.titulo}</th>
      <td className="text-right tabular-nums">
        {l.pedidos == null ? <span className="text-ink-faint">—</span> : formatarInteiro(l.pedidos)}
      </td>
      <td className="text-right tabular-nums"><Celula valor={l.vencidos} base={l.pedidos} critico /></td>
      <td className="text-right tabular-nums"><Celula valor={l.acima48h} base={l.pedidos} /></td>
      <td className="text-right tabular-nums">
        {comTravados ? <Celula valor={l.travados} base={l.pedidos} /> : <span className="text-ink-faint">—</span>}
      </td>
    </tr>
  );
}

export default function TabelaSeteDias({ linhas, total, janela, comTravados }: Props) {
  return (
    <div data-testid="exp-tabela-7d">
      <p className="mb-2 text-xs text-ink-muted">
        {janela ? `Pedidos feitos de ${janela}` : "Últimos 7 dias fechados"} — o mesmo total do
        Gerencial, com Full/FBS. Vencidos, acima de 48 h e travados contam os pedidos com prazo
        nesses dias despachados pelo vendedor; o % é sobre o total de pedidos.
        {comTravados ? "" : " Travados não é medido neste canal."}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Últimos 7 dias por marca: pedidos, vencidos, acima de 48 h e travados</caption>
          <thead className="text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th scope="col" className="text-left">Marca</th>
              <th scope="col" className="text-right">Pedidos</th>
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
