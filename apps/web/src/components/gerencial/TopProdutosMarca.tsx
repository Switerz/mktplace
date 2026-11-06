"use client";

/**
 * Top 5 produtos por marca (Gerencial).
 *
 * Referencia: "Mix Marketplace" da Natalia (ranking por receita, com pecas,
 * preco medio e categoria · SKU sob o nome). Fonte: `/api/v1/gerencial/top-produtos`,
 * base consolidada por NF — MENSAL e com os marketplaces juntos, por isso o
 * card ignora o filtro de canal e diz quais meses entraram.
 */
import { useEffect, useState } from "react";

import { torreFetch } from "@/lib/torre-fetch";
import { MARCAS } from "@/lib/metas";
import { fmtBrlCurto, fmtNumCurto } from "@/lib/formatters";
import InfoDica from "@/components/ui/InfoDica";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

interface ProdutoTop {
  posicao: number;
  sku: string;
  produto: string;
  categoria: string | null;
  pecas: number;
  receita: number;
  preco_medio: number | null;
  share_marca: number | null;
}
interface MarcaTop {
  marca: string;
  receita_marca: number;
  produtos: ProdutoTop[];
}
interface TopProdutosResponse {
  mes_inicio: string;
  mes_fim: string;
  nf_ate: string | null;
  marcas: MarcaTop[];
}

const MES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const mesCurto = (iso: string) => `${MES[Number(iso.slice(5, 7)) - 1]}/${iso.slice(2, 4)}`;
const rotuloMarca = (id: string) => MARCAS.find((m) => m.id === id)?.label ?? id.toUpperCase();

interface Props {
  brands: string[];
  dateFrom: string;
  dateTo: string;
}

export default function TopProdutosMarca({ brands, dateFrom, dateTo }: Props) {
  const [dados, setDados] = useState<TopProdutosResponse | null>(null);
  const [erro, setErro] = useState(false);
  const marcas = brands.join(",");

  useEffect(() => {
    const ac = new AbortController();
    const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
    if (marcas) q.set("brands", marcas);
    setErro(false);
    setDados(null);
    torreFetch(`${API_URL}/api/v1/gerencial/top-produtos?${q}`, { signal: ac.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: TopProdutosResponse) => setDados(j))
      .catch((e) => {
        if (!(e instanceof DOMException && e.name === "AbortError")) setErro(true);
      });
    return () => ac.abort();
  }, [marcas, dateFrom, dateTo]);

  const meses = dados
    ? dados.mes_inicio === dados.mes_fim
      ? mesCurto(dados.mes_inicio)
      : `${mesCurto(dados.mes_inicio)} a ${mesCurto(dados.mes_fim)}`
    : null;

  return (
    <section aria-labelledby="top-produtos-titulo" className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <h2 id="top-produtos-titulo" className="text-sm font-semibold text-ink">
            Top 5 produtos por marca
          </h2>
          <InfoDica texto="Receita faturada (NF) de marketplace, ML, Shopee e TikTok juntos, por SKU canônico. A base é mensal: o período vira os meses inteiros que ele toca, e o filtro de canal não se aplica. % = parte da receita de marketplace da marca." />
        </div>
        {meses && (
          <span className="text-xs text-ink-muted">
            Marketplace · {meses}
            {dados?.nf_ate && ` · NF até ${dados.nf_ate.slice(8, 10)}/${dados.nf_ate.slice(5, 7)}`}
          </span>
        )}
      </div>

      {erro ? (
        <p className="text-sm text-crit-ink">Top produtos indisponível.</p>
      ) : !dados ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-56 animate-pulse rounded-lg bg-raised motion-reduce:animate-none" />
          ))}
        </div>
      ) : dados.marcas.length === 0 ? (
        <p className="text-sm text-ink-faint">Sem venda de produto no período e marcas selecionados.</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {dados.marcas.map((m) => (
            <article key={m.marca} className="rounded-lg border border-line p-3">
              <header className="mb-2 flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold text-ink">{rotuloMarca(m.marca)}</h3>
                <span className="text-xs tabular-nums text-ink-muted">{fmtBrlCurto(m.receita_marca)} no total</span>
              </header>
              <ol className="flex flex-col">
                {m.produtos.map((p) => (
                  <li key={p.sku} className="grid grid-cols-[1.25rem_1fr_auto] items-start gap-2 border-t border-line py-2 first:border-t-0">
                    <span className="text-xs font-semibold tabular-nums text-ink-faint">{p.posicao}</span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-ink" title={p.produto}>
                        {p.produto}
                      </span>
                      <span className="block truncate text-xs text-ink-faint">
                        {p.categoria ?? "Sem categoria"} · <span className="font-mono">{p.sku}</span>
                      </span>
                      {p.share_marca != null && (
                        <span className="mt-1 block h-1 rounded-full bg-raised" aria-hidden="true">
                          <span className="block h-1 rounded-full bg-accent" style={{ width: `${p.share_marca * 100}%` }} />
                        </span>
                      )}
                    </span>
                    <span className="flex flex-col items-end text-right tabular-nums">
                      <span className="text-sm font-semibold text-ink">{fmtBrlCurto(p.receita)}</span>
                      <span className="text-xs text-ink-muted">
                        {fmtNumCurto(p.pecas)} pç
                        {p.share_marca != null && ` · ${Math.round(p.share_marca * 100)}%`}
                      </span>
                      {p.preco_medio != null && (
                        <span className="text-xs text-ink-faint">PM {fmtBrlCurto(p.preco_medio)}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
