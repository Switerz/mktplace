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

import ExpedicaoShell from "./ExpedicaoShell";

export const dynamic = "force-dynamic";

export default function ExpedicaoPage() {
  if (!expedicaoHabilitado(process.env.NEXT_PUBLIC_EXPEDICAO_ENABLED)) {
    notFound();
  }
  // Gate EXP-TK-OPS-1: a casca escolhe entre a fila (Shopee/ML) e a serie do
  // TikTok. Com `NEXT_PUBLIC_EXPEDICAO_TIKTOK_ENABLED` desligada ela renderiza
  // a fila direto, sem seletor e sem nenhuma mudanca de comportamento.
  return <ExpedicaoShell />;
}
