"use client";

/**
 * Gate EXP-TK-OPS-1 — casca que escolhe entre a fila (Shopee, Mercado Livre) e
 * a serie diaria do TikTok Shop.
 *
 * POR QUE UMA CASCA E NAO UM TERCEIRO CANAL DENTRO DO `ExpedicaoClient`
 * ---------------------------------------------------------------------
 * `Canal` (em `expedicao-contract.ts`) e' o tipo da FILA, e varios contratos
 * sao `Record<Canal, ...>`: `ROTULO_CANAL`, `KPIS_PRINCIPAIS`, `canalTemPrazo`,
 * `montarMapaDeRisco`, `montarTimeline`. Acrescentar `"tiktokshop"` aquele tipo
 * obrigaria a inventar KPI de fila, mapa de risco e timeline para um canal que
 * nao publica fila nenhuma — dados que nao existem, so' para satisfazer o
 * compilador.
 *
 * Entao o TikTok e' uma ABA, nao um canal da fila. `ExpedicaoClient` continua
 * recebendo exatamente os dois canais que sempre recebeu, e nenhum caminho de
 * Shopee ou Mercado Livre passa por codigo novo.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import ExpedicaoClient from "./ExpedicaoClient";
import TikTokDispatchPanel from "@/components/expedicao/TikTokDispatchPanel";

const ML_LIGADO = process.env.NEXT_PUBLIC_EXPEDICAO_ML_ENABLED === "true";
/**
 * Flag PROPRIA do TikTok, escrita inline para o minificador conseguir eliminar
 * o painel inteiro quando ela estiver definida como algo diferente de "true".
 * Ausente, o markup viaja inerte — mesmo comportamento das outras flags da tela.
 */
const TIKTOK_LIGADO = process.env.NEXT_PUBLIC_EXPEDICAO_TIKTOK_ENABLED === "true";

const ABA_FILA = "fila" as const;
const ABA_TIKTOK = "tiktokshop" as const;
type Aba = typeof ABA_FILA | typeof ABA_TIKTOK;

/** O parametro da URL e' o mesmo `channel` ja usado pelos outros canais. */
function lerAba(): Aba {
  if (typeof window === "undefined") return ABA_FILA;
  const v = new URLSearchParams(window.location.search).get("channel");
  return v === ABA_TIKTOK && TIKTOK_LIGADO ? ABA_TIKTOK : ABA_FILA;
}

export default function ExpedicaoShell() {
  // Sem a flag do TikTok nada muda em relacao a antes deste gate: a casca
  // renderiza a fila direto e nem monta o seletor.
  if (!TIKTOK_LIGADO) return <ExpedicaoClient />;
  return <ComAbas />;
}

function ComAbas() {
  /**
   * Comeca na fila e corrige no efeito, igual ao que o `ExpedicaoClient` ja faz
   * com o canal: ler `window` durante o render faria o HTML do servidor
   * divergir do primeiro render do cliente.
   */
  const [aba, setAba] = useState<Aba>(ABA_FILA);
  const [pronta, setPronta] = useState(false);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    setAba(lerAba());
    setPronta(true);
  }, []);

  // Voltar/avancar do navegador troca a URL sem remontar.
  useEffect(() => {
    const aoVoltar = () => setAba(lerAba());
    window.addEventListener("popstate", aoVoltar);
    return () => window.removeEventListener("popstate", aoVoltar);
  }, []);

  const trocar = useCallback((nova: Aba) => {
    setAba(nova);
    const url = new URL(window.location.href);
    if (nova === ABA_TIKTOK) url.searchParams.set("channel", ABA_TIKTOK);
    else url.searchParams.delete("channel");
    window.history.replaceState(null, "", url.toString());
  }, []);

  const opcoes: { valor: Aba; texto: string }[] = [
    { valor: ABA_FILA, texto: ML_LIGADO ? "Fila (Shopee / Mercado Livre)" : "Fila (Shopee)" },
    { valor: ABA_TIKTOK, texto: "TikTok Shop" },
  ];

  const aoTeclar = (e: React.KeyboardEvent, i: number) => {
    const n = opcoes.length;
    let alvo = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") alvo = (i + 1) % n;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") alvo = (i - 1 + n) % n;
    else if (e.key === "Home") alvo = 0;
    else if (e.key === "End") alvo = n - 1;
    if (alvo < 0) return;
    e.preventDefault();
    trocar(opcoes[alvo].valor);
    refs.current[alvo]?.focus();
  };

  return (
    <div className="flex flex-col">
      <div className="mx-auto flex w-full max-w-[1400px] flex-wrap items-center gap-2 px-4 pt-4">
        <span id="exp-aba" className="text-xs text-slate-500">
          Superfície
        </span>
        <div
          role="radiogroup"
          aria-labelledby="exp-aba"
          className="flex gap-1 rounded-lg bg-white p-1 ring-1 ring-slate-200"
        >
          {opcoes.map((o, i) => {
            const ativo = o.valor === aba;
            return (
              <button
                key={o.valor}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                type="button"
                role="radio"
                aria-checked={ativo}
                tabIndex={ativo ? 0 : -1}
                onKeyDown={(e) => aoTeclar(e, i)}
                onClick={() => trocar(o.valor)}
                className={`rounded-md px-4 py-1.5 text-sm ${
                  ativo
                    ? "bg-brand-600 font-semibold text-white"
                    : "text-slate-700 hover:bg-brand-50"
                }`}
              >
                {o.texto}
              </button>
            );
          })}
        </div>
      </div>

      {/* Ate' a URL ser lida, mostra a fila: e' o estado historico da rota e
          evita um piscar de conteudo trocado. */}
      {pronta && aba === ABA_TIKTOK ? (
        <div className="mx-auto w-full max-w-[1400px] p-4">
          <TikTokDispatchPanel />
        </div>
      ) : (
        <ExpedicaoClient />
      )}
    </div>
  );
}
