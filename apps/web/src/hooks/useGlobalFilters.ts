"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  computeDefaultFilters,
  filtersToSearchParams,
  hasExplicitFilterParams,
  parseFiltersFromSearchParams,
  validateDateRange,
  type DefaultFiltersOptions,
} from "@/lib/filters/url-state";
import type { GlobalFilters } from "@/lib/filters/types";

export type UseGlobalFiltersOptions = DefaultFiltersOptions;

function sameStringArray(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** Igualdade por valor (nao por referencia) — channels/brands sao arrays
 * recriados a cada parse da URL; sem isso, o eco do proprio router.replace
 * (materializacao do default, ou o setFilters do usuario) produziria um
 * objeto novo com os MESMOS valores e dispararia um refetch espurio nas
 * telas, que dependem de filters.channels/filters.brands por referencia. */
function filtersEqual(a: GlobalFilters, b: GlobalFilters): boolean {
  return (
    sameStringArray(a.channels, b.channels) &&
    sameStringArray(a.brands, b.brands) &&
    a.dateFrom === b.dateFrom &&
    a.dateTo === b.dateTo &&
    a.compare === b.compare
  );
}

/**
 * Fonte unica de verdade dos filtros globais (canal, marca, periodo,
 * comparacao).
 *
 * Regras:
 * - Querystring explicita (qualquer parametro de filtro presente) sempre
 *   vence sobre o default da tela — inclusive parcialmente: se so
 *   date_from/date_to vierem (ex: navegacao entre telas), compare cai para
 *   `false` (nunca para `defaultCompare`), o que e o que faz um
 *   `compare=false` explicito sobreviver a reload.
 * - URL vazia (nenhum parametro de filtro) materializa `defaultPreset`/
 *   `defaultCompare` na URL uma unica vez via router.replace, para que o
 *   periodo efetivamente exibido fique explicito e compartilhavel (nao
 *   mude silenciosamente se o link for reaberto dias depois).
 * - O estado local so muda de valor (e so entao propaga para os efeitos de
 *   fetch das telas, que dependem de filters.channels/brands por
 *   referencia) quando o CONTEUDO realmente muda — o eco do proprio
 *   router.replace nunca dispara um segundo fetch nem um loop.
 */
export function useGlobalFilters(
  options: UseGlobalFiltersOptions = {},
): [GlobalFilters, (partial: Partial<GlobalFilters>) => void] {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  const [filters, setFiltersState] = useState<GlobalFilters>(() =>
    hasExplicitFilterParams(searchParams)
      ? parseFiltersFromSearchParams(searchParams)
      : computeDefaultFilters(options),
  );

  useEffect(() => {
    if (!hasExplicitFilterParams(searchParams)) {
      const defaults = computeDefaultFilters(options);
      setFiltersState((prev) => (filtersEqual(prev, defaults) ? prev : defaults));
      const params = filtersToSearchParams(defaults, new URLSearchParams(searchParams.toString()));
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
      return;
    }
    const parsed = parseFiltersFromSearchParams(searchParams);
    setFiltersState((prev) => (filtersEqual(prev, parsed) ? prev : parsed));
    // Reage apenas a mudancas reais de querystring (navegacao, back/forward,
    // ou o proprio replace abaixo/de setFilters — nesses casos o valor
    // resolvido bate com o estado atual e o bail-out acima evita qualquer
    // efeito colateral). `options` normalmente chega como objeto literal
    // inline do chamador (nova referencia a cada render) — deliberadamente
    // fora das deps para nao rodar este efeito a toda renderizacao; quando
    // ele de fato roda (searchParams mudou), usa o `options` mais recente
    // via closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, pathname, router]);

  const setFilters = useCallback(
    (partial: Partial<GlobalFilters>) => {
      const next = { ...filters, ...partial };
      // Segunda camada de validacao (defesa em profundidade): DateRangeFilter
      // ja valida antes de chamar onChange, mas nenhum estado invalido deve
      // conseguir chegar na URL mesmo se outro chamador futuro pular essa
      // validacao. Em caso de intervalo invalido, ignora so a parte de data
      // da mudanca (mantem o restante do partial, ex: troca de canal/marca).
      if ((partial.dateFrom !== undefined || partial.dateTo !== undefined)
          && !validateDateRange(next.dateFrom, next.dateTo).valid) {
        next.dateFrom = filters.dateFrom;
        next.dateTo = filters.dateTo;
      }
      setFiltersState(next);
      const params = filtersToSearchParams(next, new URLSearchParams(searchParams.toString()));
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [filters, pathname, router, searchParams],
  );

  return [filters, setFilters];
}
