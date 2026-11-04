"use client";

import { useEffect, useState } from "react";

import Chip from "@/components/ui/Chip";
import type { Tom } from "@/components/ui/tone";
import { brandLabel } from "@/lib/filters/brands";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import {
  CANAIS_LDR,
  DIAS_LDR_TOPO,
  META_TETO,
  ROTULO_FAIXA,
  escalaBarra,
  inteiro,
  lerRespostaLdr,
  montarMatriz,
  pct,
  urlLdr,
  type CanalLdr,
  type Celula,
  type Faixa,
  type FonteCanal,
} from "@/lib/expedicao-ldr-topo";

/**
 * CX-MKT-3 — faixa "LDR · últimos 7 dias" no topo da Expedição: uma linha por
 * marca, os três marketplaces lado a lado, com meta e teto de cada canal.
 *
 * Busca no navegador as rotas de LDR que já existem (TikTok e Shopee). A
 * coluna do ML fica "aguardando" até a série do prazo oficial existir. Toda
 * decisão (faixa, escala, ausência) vem de `expedicao-ldr-topo.ts`.
 */

const MARCAS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"] as const;

const TOM_DA_FAIXA: Record<Faixa, Tom> = { dentro: "bom", entre: "atencao", acima: "critico" };
const BARRA_DA_FAIXA: Record<Faixa, string> = { dentro: "bg-ok", entre: "bg-warn", acima: "bg-crit" };
const PONTO_CANAL: Record<CanalLdr, string> = {
  shopee: "bg-canal-shopee",
  mercadolivre: "bg-canal-ml",
  tiktokshop: "bg-canal-tiktok",
};

function useFonte(api: string, canal: CanalLdr, marca: string | null): FonteCanal {
  const url = urlLdr(api, canal, marca);
  const [fonte, setFonte] = useState<FonteCanal>({ estado: "carregando" });
  useEffect(() => {
    let vivo = true;
    setFonte({ estado: "carregando" });
    buscarExpedicao(url, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (r.status === 404) return { availability: "unavailable", unavailable_reason: "disabled" };
        if (!r.ok) throw new Error("http");
        return r.json();
      })
      .then((json) => vivo && setFonte(lerRespostaLdr(json)))
      .catch(() => vivo && setFonte({ estado: "indisponivel", motivo: "Não foi possível carregar agora." }));
    return () => {
      vivo = false;
    };
  }, [url]);
  return fonte;
}

export default function LdrTopo({ api, marca }: { api: string; marca: string | null }) {
  const fontes: Record<CanalLdr, FonteCanal> = {
    shopee: useFonte(api, "shopee", marca),
    mercadolivre: useFonte(api, "mercadolivre", marca),
    tiktokshop: useFonte(api, "tiktokshop", marca),
  };
  const marcas = marca ? MARCAS.filter((m) => m === marca) : MARCAS;
  const linhas = montarMatriz(fontes, marcas);

  return (
    <section
      aria-labelledby="ldr-topo-titulo"
      className="flex flex-col gap-3 rounded-xl border border-accent/50 bg-surface p-4 shadow-[0_0_0_3px_rgb(var(--tc-accent-soft))]"
      data-testid="exp-ldr-topo"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="ldr-topo-titulo" className="text-base font-bold text-ink">
          LDR · atraso de envio, últimos {DIAS_LDR_TOPO} dias
        </h2>
        <p className="text-xs text-ink-faint">
          Pedidos vencidos ÷ pedidos com prazo vencendo na janela · cálculo Torre, não é a nota oficial
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th scope="col" className="py-2 pr-3 text-xs font-semibold text-ink-faint">Marca</th>
              {CANAIS_LDR.map((c) => {
                const mt = META_TETO[c.canal];
                return (
                  <th key={c.canal} scope="col" className="min-w-[210px] px-2 py-2 align-bottom">
                    <span className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO_CANAL[c.canal]}`} />
                      {c.titulo}
                    </span>
                    <span className="block text-[11px] font-medium text-ink-faint">
                      {c.regra} · meta {pct(mt.meta)} · teto {pct(mt.teto)}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {linhas.map((l) => (
              <tr key={l.marca} className="border-b border-line last:border-b-0">
                <th scope="row" className="py-2.5 pr-3 text-left font-semibold text-ink">
                  {brandLabel(l.marca)}
                </th>
                {CANAIS_LDR.map((c) => (
                  <td key={c.canal} className="px-2 py-2.5 align-middle">
                    <CelulaLdr celula={l.celulas[c.canal]} canal={c.canal} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-0.5 w-3 rounded bg-ok" /> Meta: onde queremos estar
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-0.5 w-3 rounded bg-crit" /> Teto: limite antes de
          risco de penalização
        </span>
      </div>
    </section>
  );
}

function CelulaLdr({ celula, canal }: { celula: Celula; canal: CanalLdr }) {
  if (celula.tipo === "carregando") {
    return <span className="block h-10 animate-pulse rounded-md bg-raised" aria-label="Carregando" />;
  }
  if (celula.tipo === "sem_conta") {
    return <span className="text-xs text-ink-faint">Sem conta neste canal</span>;
  }
  if (celula.tipo !== "dado") {
    return (
      <span className="block rounded-md border border-dashed border-line-strong px-2 py-1.5 text-xs text-ink-faint">
        {celula.motivo}
      </span>
    );
  }
  const mt = META_TETO[canal];
  const barra = escalaBarra(celula.taxa, mt);
  return (
    <div className="flex min-w-[190px] flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xl font-semibold tabular-nums text-ink">{pct(celula.taxa)}</span>
        <Chip tom={TOM_DA_FAIXA[celula.faixa]}>{ROTULO_FAIXA[celula.faixa]}</Chip>
      </div>
      <div
        className="relative h-2 rounded-full bg-raised"
        role="img"
        aria-label={`${pct(celula.taxa)}; meta ${pct(mt.meta)}, teto ${pct(mt.teto)}`}
      >
        <span
          className={`absolute inset-y-0 left-0 rounded-full ${BARRA_DA_FAIXA[celula.faixa]}`}
          style={{ width: `${barra.preenchido}%` }}
        />
        <span className="absolute -top-1 h-4 w-0.5 rounded bg-ok" style={{ left: `${barra.meta}%` }} />
        <span className="absolute -top-1 h-4 w-0.5 rounded bg-crit" style={{ left: `${barra.teto}%` }} />
      </div>
      <span className="text-[11px] tabular-nums text-ink-faint">
        {inteiro(celula.atrasados)} atrasados de {inteiro(celula.base)} com prazo vencido
        {celula.parcial ? ` · ${celula.parcial}` : ""}
      </span>
    </div>
  );
}
