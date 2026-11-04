"use client";

import { useEffect, useState } from "react";

import Chip from "@/components/ui/Chip";
import StateBlock from "@/components/ui/StateBlock";
import { brandLabel } from "@/lib/filters/brands";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import {
  ROTULO_OFICIAL,
  ROTULO_SITUACAO,
  diaCurto,
  problemaNaPerformance,
  quantidadeMetrica,
  urlPerformance,
  valorMetrica,
  type CanalOficial,
  type ContaOficial,
  type PerformanceOficialResponse,
  type Situacao,
} from "@/lib/expedicao-performance";

/**
 * CX-MKT-2 — seção "Performance" da Expedição: a nota OFICIAL de cada conta do
 * canal escolhido, como a plataforma mostra no painel do vendedor (ML:
 * reputação; Shopee: Saúde da Conta). A cor de cada métrica vem SÓ do limite
 * oficial; sem limite, sem cor.
 */

type Estado =
  | { tipo: "carregando" }
  | { tipo: "erro"; motivo: string }
  | { tipo: "ok"; dados: PerformanceOficialResponse };

const TOM_SITUACAO: Record<Situacao, "bom" | "critico" | "neutro"> = { ok: "bom", fora: "critico", sem_meta: "neutro" };
const COR_TERMOMETRO_ML = ["bg-crit", "bg-[#f97316]", "bg-[#facc15]", "bg-[#84cc16]", "bg-ok"];
const COR_TERMOMETRO_SHOPEE = ["bg-crit", "bg-warn", "bg-[#84cc16]", "bg-ok"];

export default function PerformanceOficial({
  api,
  canal,
  marca,
}: {
  api: string;
  canal: CanalOficial;
  marca: string | null;
}) {
  const url = urlPerformance(api, canal, marca);
  const [estado, setEstado] = useState<Estado>({ tipo: "carregando" });

  useEffect(() => {
    let vivo = true;
    setEstado({ tipo: "carregando" });
    buscarExpedicao(url, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "desligada" : "http");
        const json: unknown = await r.json();
        const problema = problemaNaPerformance(json);
        if (problema) throw new Error("forma");
        return json as PerformanceOficialResponse;
      })
      .then((dados) => vivo && setEstado({ tipo: "ok", dados }))
      .catch((e: Error) =>
        vivo &&
        setEstado({
          tipo: "erro",
          motivo: e.message === "desligada" ? "A seção Performance ainda está desligada na API." : "Não foi possível carregar a performance agora.",
        }),
      );
    return () => {
      vivo = false;
    };
  }, [url]);

  return (
    <section aria-labelledby="perf-oficial-titulo" className="flex flex-col gap-3" data-testid="exp-performance">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="perf-oficial-titulo" className="text-sm font-semibold text-ink">
          Performance · nota oficial de cada conta
        </h3>
        <p className="text-xs text-ink-faint">O que a plataforma usa para reputação e penalidade · fotografia diária</p>
      </div>

      {estado.tipo === "carregando" && <StateBlock tipo="carregando" titulo="Carregando a performance oficial" compacto />}
      {estado.tipo === "erro" && <StateBlock tipo="erro" titulo={estado.motivo} compacto />}
      {estado.tipo === "ok" && <Corpo dados={estado.dados} />}
    </section>
  );
}

function Corpo({ dados }: { dados: PerformanceOficialResponse }) {
  if (dados.estado !== "dado") {
    return (
      <StateBlock
        tipo={dados.estado === "erro" ? "erro" : "indisponivel"}
        titulo={dados.estado === "erro" ? "Performance oficial indisponível nesta carga" : "Performance oficial ainda não capturada"}
        descricao={dados.motivo ?? undefined}
        compacto
      />
    );
  }
  return (
    <>
      {dados.contas.length > 0 && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {dados.contas.map((c) => (
            <Conta key={c.marca} conta={c} canal={dados.canal} />
          ))}
        </div>
      )}
      {(dados.sem_foto.length > 0 || dados.cobertura.fora.length > 0) && (
        <ul className="flex flex-col gap-1 rounded-lg border border-dashed border-line px-3 py-2 text-xs text-ink-muted">
          {dados.sem_foto.map((s) => (
            <li key={`s-${s.marca}`}>
              {brandLabel(s.marca)}: {s.motivo}
            </li>
          ))}
          {dados.cobertura.fora.map((f) => (
            <li key={`f-${f.marca}`}>
              {brandLabel(f.marca)}: {f.motivo}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-ink-faint">Fonte: {dados.fonte}</p>
    </>
  );
}

function Termometro({ conta, canal }: { conta: ContaOficial; canal: CanalOficial }) {
  if (!conta.termometro || !conta.termometro_max) return null;
  const cores = canal === "mercadolivre" ? COR_TERMOMETRO_ML : COR_TERMOMETRO_SHOPEE;
  return (
    <div
      role="img"
      aria-label={`Nível ${conta.termometro} de ${conta.termometro_max}`}
      className="grid w-40 max-w-full gap-1"
      style={{ gridTemplateColumns: `repeat(${conta.termometro_max}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: conta.termometro_max }, (_, i) => (
        <span
          key={i}
          className={`h-1.5 rounded-sm ${cores[i] ?? "bg-ink-faint"} ${i + 1 === conta.termometro ? "" : "opacity-25"}`}
        />
      ))}
    </div>
  );
}

function Conta({ conta, canal }: { conta: ContaOficial; canal: CanalOficial }) {
  return (
    <article className="flex min-w-0 flex-col gap-3 rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-base font-semibold text-ink">{brandLabel(conta.marca)}</h4>
          {conta.resumo && <p className="text-xs text-ink-faint">{conta.resumo}</p>}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          {conta.nivel && (
            <Chip tom={conta.tom === "neutro" ? "neutro" : conta.tom} ponto>
              {conta.nivel}
            </Chip>
          )}
          <Termometro conta={conta} canal={canal} />
        </div>
      </div>

      {conta.metricas.length > 0 && (
        <ul className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {conta.metricas.map((m) => (
            <li key={m.codigo} className="flex min-w-0 flex-col gap-1 rounded-lg border border-line p-2.5" title={m.ajuda ?? undefined}>
              <span className="text-xs font-semibold text-ink-muted">{m.rotulo}</span>
              <span className="text-lg font-semibold tabular-nums text-ink">{valorMetrica(m)}</span>
              {quantidadeMetrica(m) && <span className="text-[11px] tabular-nums text-ink-faint">{quantidadeMetrica(m)}</span>}
              {m.rotulo_meta && <span className="text-[11px] text-ink-faint">{m.rotulo_meta}</span>}
              {ROTULO_SITUACAO[m.situacao] && (
                <span>
                  <Chip tom={TOM_SITUACAO[m.situacao]}>{ROTULO_SITUACAO[m.situacao]}</Chip>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {conta.destaques.length > 0 && (
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
          {conta.destaques.map((d) => (
            <div key={d.rotulo} className="flex gap-1.5">
              <dt className="text-ink-muted">{d.rotulo}:</dt>
              <dd className="font-semibold tabular-nums text-ink">{d.valor}</dd>
            </div>
          ))}
        </dl>
      )}

      {conta.avisos.map((a) => (
        <p key={a} role="note" className="rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn-ink">
          {a}
        </p>
      ))}

      <p className="text-[11px] text-ink-faint">
        <span className="mr-1.5 rounded bg-accent-soft px-1.5 py-0.5 font-semibold text-accent">{ROTULO_OFICIAL[canal]}</span>
        capturado em {diaCurto(conta.dia)}
      </p>
    </article>
  );
}
