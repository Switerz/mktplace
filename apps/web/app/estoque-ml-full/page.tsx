import MLFullEstoqueClient from "./MLFullEstoqueClient";

/**
 * Tela "Estoque Full Mercado Livre" — ML-FULL-STOCK-API-1.
 *
 * SEM FLAG DE WEB (04/11/2026). A tela ficava atras de
 * `NEXT_PUBLIC_ML_FULL_STOCK_ENABLED`, flag de BUILD que sumia com a aba
 * quando a variavel faltava na Vercel. Quem decide se ha' dado e' a API
 * (`ML_FULL_STOCK_API_ENABLED`): desligada, ela responde
 * `status="unavailable"` com o motivo e a tela desenha "indisponivel" -- nunca
 * zero.
 *
 * Rota SEPARADA de `/full-ml`: aquela mede MODALIDADE DE ENVIO do pedido,
 * esta mede ESTOQUE fisico no CD do ML, por inventario.
 */
export default function MLFullEstoquePage() {
  return <MLFullEstoqueClient />;
}
