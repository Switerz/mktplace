import { notFound } from "next/navigation";

import { modoB2BRevenda } from "@/lib/b2b-revenda-flag";
import RevendedoresClient from "./RevendedoresClient";

/**
 * Aba "B2B / Revendedores" — Gate TORRE-B2B-OCTAPRICE.
 *
 * GUARD NO SERVIDOR: com `NEXT_PUBLIC_B2B_REVENDA_MODE` desligada esta rota
 * devolve 404 e o componente nunca monta — nenhuma requisicao acontece.
 */
export default function RevendedoresPage() {
  const modo = modoB2BRevenda();
  if (modo === "off") notFound();
  return <RevendedoresClient modo={modo} />;
}
