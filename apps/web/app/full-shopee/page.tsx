import { notFound } from "next/navigation";

import { shopeeFbsEnabled } from "@/lib/shopee-fbs-flag";
import FullShopeeClient from "./FullShopeeClient";

/**
 * Tela "Full Shopee" — Gate FULL-SH-1D.
 *
 * GUARD NO SERVIDOR, e nao apenas no menu.
 *
 * Com `NEXT_PUBLIC_SHOPEE_FBS_ENABLED` diferente de "true" esta rota devolve
 * 404 e o componente cliente NUNCA e' montado -- logo nenhuma requisicao ao
 * endpoint acontece. Esconder so' o item de navegacao deixaria a URL direta
 * acessivel, e nem a aplicacao nem a API tem autenticacao.
 *
 * O `/full-ml` continua intacto: esta e' rota SEPARADA, e as duas telas nao
 * foram fundidas numa abstracao generica de "Full". As fontes tem semantica
 * diferente (o ML tem classe `unknown` e entrega real; a Shopee nao tem
 * nenhuma das duas), e generalizar agora acoplaria contratos que divergem.
 */
export default function FullShopeePage() {
  if (!shopeeFbsEnabled()) notFound();
  return <FullShopeeClient />;
}
