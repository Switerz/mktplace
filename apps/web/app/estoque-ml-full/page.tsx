import { notFound } from "next/navigation";

import { mlFullEstoqueEnabled } from "@/lib/ml-full-estoque-flag";
import MLFullEstoqueClient from "./MLFullEstoqueClient";

/**
 * Tela "Estoque Full Mercado Livre" — ML-FULL-STOCK-API-1.
 *
 * GUARD NO SERVIDOR: com `NEXT_PUBLIC_ML_FULL_STOCK_ENABLED` diferente de
 * "true" a rota devolve 404 e o cliente nunca e' montado -- nenhuma
 * requisicao ao endpoint acontece.
 *
 * Rota SEPARADA de `/full-ml`: aquela mede MODALIDADE DE ENVIO do pedido,
 * esta mede ESTOQUE fisico no CD do ML, por inventario.
 */
export default function MLFullEstoquePage() {
  if (!mlFullEstoqueEnabled()) notFound();
  return <MLFullEstoqueClient />;
}
