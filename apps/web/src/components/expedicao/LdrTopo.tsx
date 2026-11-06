"use client";

import { useEffect, useState } from "react";

import Chip from "@/components/ui/Chip";
import type { Tom } from "@/components/ui/tone";
import { brandLabel } from "@/lib/filters/brands";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import {
  CANAIS_LDR,
  DICA_LDR,
  META_TETO,
  ROTULO_FAIXA,
  escalaBarra,
  inteiro,
  lerRespostaLdr,
  montarMatriz,
  pct,
  precisaReserva,
  urlLdr,
  urlLdrReserva,
  type CanalLdr,
  type Celula,
  type Faixa,
  type FonteCanal,
} from "@/lib/expedicao-ldr-topo";

import { SecaoGerencial } from "./SecoesExpedicao";

/**
 * CX-MKT-3 — "LDR · últimos 7 dias", logo abaixo de "Risco atual" na visão do
 * gestor: uma linha por marca, os três marketplaces lado a lado, cada um com
 * meta e teto. Toda decisão (faixa, escala, ausência, reserva) mora em
 * `expedicao-ldr-topo.ts`; aqui só se desenha.
 */

const MARCAS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"] as const;

const TOM_DA_FAIXA: Record<Faixa, Tom> = { dentro: "bom", entre: "atencao", acima: "critico" };
const BARRA_DA_FAIXA: Record<Faixa, string> = { dentro: "bg-ok", entre: "bg-warn", acima: "bg-crit" };
const TINTA_DA_FAIXA: Record<Faixa, string> = { dentro: "text-ink", entre: "text-warn-ink", acima: "text-crit-ink" };
const PONTO_CANAL: Record<CanalLdr, string> = {
  shopee: "bg-canal-shopee",
  mercadolivre: "bg-canal-ml",
  tiktokshop: "bg-canal-tiktok",
};

async function buscarJson(url: string): Promise<unknown> {
  const r = await buscarExpedicao(url, { headers: { Accept: "application/json" } });
  if (r.status === 404) return { availability: "unavailable", unavailable_reason: "disabled" };
  if (!r.ok) throw new Error("http");
  return r.json();
}

function useFonte(api: string, canal: CanalLdr, marca: string | null): FonteCanal {
  const url = urlLdr(api, canal, marca);
  const reserva = urlLdrReserva(api, canal, marca);
  const [fonte, setFonte] = useState<FonteCanal>({ estado: "carregando" });
  useEffect(() => {
    let vivo = true;
    setFonte({ estado: "carregando" });
    buscarJson(url)
      // Shopee: sem a publicação oficial, a mesma regra calculada no Data Mart.
      .then((json) => (reserva && precisaReserva(json) ? buscarJson(reserva) : json))
      .then((json) => vivo && setFonte(lerRespostaLdr(json)))
      .catch(() => vivo && setFonte({ estado: "indisponivel", motivo: "Não foi possível carregar agora." }));
    return () => {
      vivo = false;
    };
  }, [url, reserva]);
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
    <SecaoGerencial titulo="LDR · últimos 7 dias" dica={DICA_LDR}>
      <div className="overflow-x-auto rounded-xl border border-line bg-surface" data-testid="exp-ldr-topo">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th scope="col" className="w-36 px-4 py-2.5 text-xs font-medium text-ink-faint">Marca</th>
              {CANAIS_LDR.map((c) => {
                const mt = META_TETO[c.canal];
                const f = fontes[c.canal];
                const parcial =
                  f.estado === "dado" && f.diasMaduros !== undefined && f.diasPedidos !== undefined
                    ? ` · ${f.diasMaduros} de ${f.diasPedidos} dias`
                    : "";
                return (
                  <th key={c.canal} scope="col" className="px-4 py-2.5 align-bottom" title={c.regra}>
                    <span className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO_CANAL[c.canal]}`} />
                      {c.titulo}
                    </span>
                    <span className="block text-[11px] font-medium tabular-nums text-ink-faint">
                      meta {pct(mt.meta)} · teto {pct(mt.teto)}
                      {parcial}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {linhas.map((l) => (
              <tr key={l.marca} className="border-b border-line last:border-b-0">
                <th scope="row" className="px-4 py-3 text-left text-sm font-semibold text-ink">
                  {brandLabel(l.marca)}
                </th>
                {CANAIS_LDR.map((c) => (
                  <td key={c.canal} className="px-4 py-3 align-middle">
                    <CelulaLdr celula={l.celulas[c.canal]} canal={c.canal} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SecaoGerencial>
  );
}

function CelulaLdr({ celula, canal }: { celula: Celula; canal: CanalLdr }) {
  if (celula.tipo === "carregando") {
    return <span className="block h-9 w-40 animate-pulse rounded-md bg-raised" aria-label="Carregando" />;
  }
  if (celula.tipo !== "dado") {
    const motivo = celula.tipo === "sem_conta" ? "Sem conta neste canal" : celula.motivo;
    return (
      <span className="text-sm text-ink-faint" title={motivo} aria-label={motivo}>
        —
      </span>
    );
  }
  const mt = META_TETO[canal];
  const barra = escalaBarra(celula.taxa, mt);
  return (
    <div className="flex max-w-[260px] flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className={`text-base font-semibold tabular-nums ${TINTA_DA_FAIXA[celula.faixa]}`}>
          {pct(celula.taxa)}
        </span>
        <Chip tom={TOM_DA_FAIXA[celula.faixa]}>{ROTULO_FAIXA[celula.faixa]}</Chip>
      </div>
      <div
        className="relative h-1.5 rounded-full bg-raised"
        role="img"
        aria-label={`${pct(celula.taxa)}; meta ${pct(mt.meta)}, teto ${pct(mt.teto)}`}
      >
        <span
          className={`absolute inset-y-0 left-0 rounded-full ${BARRA_DA_FAIXA[celula.faixa]}`}
          style={{ width: `${barra.preenchido}%` }}
        />
        <span className="absolute -top-0.5 h-2.5 w-px bg-ok" style={{ left: `${barra.meta}%` }} />
        <span className="absolute -top-0.5 h-2.5 w-px bg-crit" style={{ left: `${barra.teto}%` }} />
      </div>
      <span className="text-[11px] tabular-nums text-ink-faint">
        {inteiro(celula.atrasados)} de {inteiro(celula.base)}
      </span>
    </div>
  );
}
