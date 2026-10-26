import type { ReactNode } from "react";

import { modoB2BRevenda } from "@/lib/b2b-revenda-flag";
import VisaoMonitoramentoNav from "./VisaoMonitoramentoNav";

/**
 * Gate TORRE-B2B-OCTAPRICE — seletor "Lojas proprias | B2B / Revendedores".
 *
 * Com a flag desligada (o padrao) este layout devolve SO' `children`: a tela
 * de lojas proprias fica byte a byte como era, sem seletor e sem rota nova.
 *
 * As duas visoes sao ROTAS (`/monitoramento-preco` e
 * `/monitoramento-preco/revendedores`), e nao um estado dentro da pagina de
 * lojas proprias: cada uma tem fonte, piso e contrato proprios (PDV das
 * tabelas B2B x PMA do OctaPrice), e nao compartilham filtro nem numero.
 */
export default function MonitoramentoPrecoLayout({ children }: { children: ReactNode }) {
  if (modoB2BRevenda() === "off") return <>{children}</>;
  return (
    <>
      <div className="max-w-[1440px] mx-auto px-4 sm:px-6 pt-6 -mb-2">
        <VisaoMonitoramentoNav />
      </div>
      {children}
    </>
  );
}
