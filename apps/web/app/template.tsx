/**
 * ANIMACOES-1 — entrada da rota.
 *
 * O `template` remonta a cada troca de ROTA (e nao a cada troca de filtro, que
 * so' muda a query), entao a entrada escalonada de `.tc-entrada` acontece uma
 * vez por abertura de tela. `display: contents` mantem o layout identico: a
 * pagina continua sendo filha direta do `<main>` para efeito de caixa.
 */
export default function Template({ children }: { children: React.ReactNode }) {
  return <div className="contents tc-entrada">{children}</div>;
}
