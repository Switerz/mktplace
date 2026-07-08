/** Formata um timestamp ISO (refreshed_at) para exibição pt-BR compacta. */
export function fmtRefreshedAt(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return "";
  }
}

function fmtDatePtBr(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

/** Formata um intervalo (date_from, date_to) para exibição — um único dia
 * quando as datas coincidem, "de – até" caso contrário. */
export function fmtPeriodo(dateFrom: string, dateTo: string): string {
  if (!dateFrom || !dateTo) return "—";
  if (dateFrom === dateTo) return fmtDatePtBr(dateFrom);
  return `${fmtDatePtBr(dateFrom)} – ${fmtDatePtBr(dateTo)}`;
}

/**
 * Os dados de demonstracao (fallback quando a API esta offline) sao
 * gerados/hardcoded e NAO filtram por marca nem por intervalo de datas —
 * mostrar essa mensagem evita que o usuario interprete os numeros de exemplo
 * como se refletissem os filtros que ele acabou de aplicar. `isCustomPeriod`
 * deve ser true sempre que o periodo exibido nao for o default da propria
 * tela (ex: `detectPreset(dateFrom, dateTo) !== defaultPresetDaTela`) — o
 * mock ignora completamente o intervalo, entao qualquer desvio do periodo
 * fixo de exemplo precisa do mesmo aviso que o filtro de marca ja tinha.
 * Retorna null quando nao ha nada a avisar (dado ao vivo, ou demonstracao
 * sem marca filtrada e sem periodo customizado).
 */
export function mockLimitationNote(isLive: boolean, brands: string[], isCustomPeriod: boolean): string | null {
  if (isLive) return null;
  const filtrosIgnorados: string[] = [];
  if (brands.length > 0) filtrosIgnorados.push("marca");
  if (isCustomPeriod) filtrosIgnorados.push("período");
  if (filtrosIgnorados.length === 0) return null;
  const campos = filtrosIgnorados.join(" e ");
  return `Modo demonstração (API offline) — os dados de exemplo não filtram por ${campos}; mostrando o conjunto completo/período fixo de exemplo, não o(a) ${campos} selecionado(a).`;
}
