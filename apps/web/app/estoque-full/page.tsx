import EstoqueFullClient from "./EstoqueFullClient";

/**
 * Tela "Estoque Full Shopee" — Gate FULL-SOURCE-3.
 *
 * SEM FLAG DE WEB (04/11/2026). A tela ficava atras de
 * `NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED`, flag de BUILD: um projeto novo na
 * Vercel sem a variavel sumia com a aba em silencio. Quem decide se ha' dado
 * e' a API (`SHOPEE_FBS_STOCK_ENABLED`): desligada, ela responde
 * `status="unavailable"` com o motivo e a tela desenha "indisponivel" -- nunca
 * zero. O acesso ja' passa pelo login Google.
 *
 * Rota SEPARADA de `/full-shopee`: aquela mede DESEMPENHO de venda por
 * modalidade, esta mede ESTOQUE FISICO no CD da Shopee.
 */
export default function EstoqueFullPage() {
  return <EstoqueFullClient />;
}
