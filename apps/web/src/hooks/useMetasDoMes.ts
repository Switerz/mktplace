"use client";

/**
 * Metas de um mes para a Gerencial (GERENCIAL-METAS-1).
 *
 * Fonte PROPRIA, fora de `useGerencialSources`: meta nao e' dado do Data Mart,
 * e a falha dela nao pode apagar nenhum outro bloco. `mes = null` (periodo que
 * nao e' um mes) nao dispara requisicao.
 */
import { useCallback, useEffect, useState } from "react";

import { fetchMetas } from "@/lib/metas-api";
import type { CelulaMetaGmv } from "@/lib/gerencial/meta-progress";

export type MetasDoMes =
  | { status: "idle" | "loading" | "error"; celulas: CelulaMetaGmv[] }
  | { status: "fresh"; celulas: CelulaMetaGmv[] };

export function useMetasDoMes(mes: string | null): MetasDoMes & { tentarDeNovo: () => void } {
  const [estado, setEstado] = useState<{ mes: string | null; valor: MetasDoMes }>({
    mes: null,
    valor: { status: "idle", celulas: [] },
  });
  const [tentativa, setTentativa] = useState(0);
  const tentarDeNovo = useCallback(() => setTentativa((n) => n + 1), []);

  useEffect(() => {
    if (!mes) return;
    const ctrl = new AbortController();
    setEstado({ mes, valor: { status: "loading", celulas: [] } });
    fetchMetas(mes, ctrl.signal)
      .then((resp) => setEstado({ mes, valor: { status: "fresh", celulas: resp.celulas } }))
      .catch(() => {
        if (!ctrl.signal.aborted) setEstado({ mes, valor: { status: "error", celulas: [] } });
      });
    return () => ctrl.abort();
  }, [mes, tentativa]);

  if (!mes) return { status: "idle", celulas: [], tentarDeNovo };
  return { ...(estado.mes === mes ? estado.valor : { status: "loading", celulas: [] }), tentarDeNovo };
}
