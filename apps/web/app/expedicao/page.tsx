/**
 * Gate EXP-2B — rota da Expedicao, FAIL-CLOSED.
 *
 * A checagem da flag acontece no SERVIDOR, antes de qualquer render. Com ela
 * desligada o Next devolve 404 e o componente cliente nem chega ao navegador —
 * portanto nao existe requisicao para a API, nem bundle com a tela dentro.
 *
 * Esconder so' o item de menu nao bastaria: a rota continuaria acessivel por
 * URL direta, e a API nao tem autenticacao nenhuma.
 */
import { notFound } from "next/navigation";

import { expedicaoHabilitado } from "@/lib/expedicao-contract";

import ExpedicaoClient from "./ExpedicaoClient";

export const dynamic = "force-dynamic";

export default function ExpedicaoPage() {
  if (!expedicaoHabilitado(process.env.NEXT_PUBLIC_EXPEDICAO_ENABLED)) {
    notFound();
  }
  return <ExpedicaoClient />;
}
