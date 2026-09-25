import type { ReactNode } from "react";

/**
 * UX-TORRE-1 — faixa unica de cabecalho de tela.
 *
 * UMA linha (duas no mobile) com titulo, controles e metadados. A Torre tinha
 * o padrao oposto: titulo num bloco, seletor noutro, frescor num paragrafo,
 * qualidade num banner — quatro faixas antes do primeiro numero.
 *
 * `sticky`: a identificacao do canal e o frescor precisam continuar visiveis
 * durante a rolagem de uma fila de 100 linhas. Foi ler numero de uma loja sob
 * o titulo de outra que motivou por o canal no titulo.
 */
export default function PageBar({
  titulo,
  controles,
  meta,
}: {
  titulo: ReactNode;
  controles?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="sticky top-0 z-20 -mx-3 border-b border-line bg-canvas/85 px-3 py-2 backdrop-blur-sm sm:-mx-4 sm:px-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">{titulo}</div>
        {controles}
        {meta && (
          <div className="ml-auto flex flex-wrap items-center gap-1.5">{meta}</div>
        )}
      </div>
    </div>
  );
}
