import { notFound } from "next/navigation";

import { estoqueFullEnabled } from "@/lib/estoque-full-flag";
import EstoqueFullClient from "./EstoqueFullClient";

/**
 * Tela "Estoque Full Shopee" — Gate FULL-SOURCE-3.
 *
 * GUARD NO SERVIDOR, e nao apenas no menu.
 *
 * Com `NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED` diferente de "true" esta rota
 * devolve 404 e o componente cliente NUNCA e' montado -- logo nenhuma
 * requisicao ao endpoint acontece. Esconder so' o item de navegacao deixaria a
 * URL direta acessivel, e nem a aplicacao nem a API tem autenticacao.
 *
 * Rota SEPARADA de `/full-shopee`. As duas telas falam de coisas diferentes:
 * aquela mede DESEMPENHO de venda por modalidade, esta mede ESTOQUE FISICO no
 * CD da Shopee. Fundi-las numa abstracao de "Full" acoplaria uma fato
 * publicada (019) a duas que ainda nem existem no banco produtivo (021).
 */
export default function EstoqueFullPage() {
  if (!estoqueFullEnabled()) notFound();
  return <EstoqueFullClient />;
}
