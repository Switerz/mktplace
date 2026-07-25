"use client";

import { useState } from "react";
import { buildProdutosCsvFile, buildProdutosExportFilename, type ExportRecord, type ProdutosExportScope } from "@/lib/produtos-export";

interface Props<T> {
  /** Linhas ATUALMENTE carregadas/exibidas na pagina — nunca busca outras
   * paginas. `null` = API sem resposta ainda (botao fica desabilitado). */
  rows: T[] | null;
  loading: boolean;
  columns: string[];
  toRecord: (row: T) => ExportRecord;
  scope: ProdutosExportScope;
}

/**
 * Exporta somente as linhas ja carregadas/exibidas na pagina atual em CSV
 * (Gate U4, Task 2 — substituido de XLSX na rodada de correcao consolidada:
 * `xlsx@0.18.5` tinha vulnerabilidades de alta severidade sem correcao
 * disponivel). Nunca busca todas as paginas nem aumenta o limit. Geracao
 * 100% sincrona (sem biblioteca): `Blob` + object URL + link de download
 * sintetico, com a object URL sempre revogada.
 */
export default function ProductExportButton<T>({ rows, loading, columns, toRecord, scope }: Props<T>) {
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasRows = rows != null && rows.length > 0;
  const disabled = loading || !hasRows || exporting;

  function handleExport() {
    if (!rows || rows.length === 0) return;
    setError(null);
    setExporting(true);
    let url: string | null = null;
    try {
      const content = buildProdutosCsvFile(columns, rows.map(toRecord));
      const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
      url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = buildProdutosExportFilename(scope);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      // Erro de exportacao e local — nunca altera loading/data da pagina.
      setError("Falha ao exportar a pagina atual. Tente novamente.");
    } finally {
      if (url) URL.revokeObjectURL(url);
      setExporting(false);
    }
  }

  return (
    <div className="flex items-center gap-2 ml-auto">
      {error && (
        <span role="alert" aria-live="assertive" className="text-xs text-rose-600">
          {error}
        </span>
      )}
      <button
        type="button"
        onClick={handleExport}
        disabled={disabled}
        className="text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg px-3 py-1.5 hover:bg-violet-50 disabled:opacity-40 disabled:cursor-default transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 whitespace-nowrap"
      >
        {exporting ? "Exportando..." : "Exportar página (.csv)"}
      </button>
    </div>
  );
}
