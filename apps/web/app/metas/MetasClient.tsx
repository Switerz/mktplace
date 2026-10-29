"use client";

/**
 * Metas — METAS-1.
 *
 * PARA QUEM E' E QUE DECISAO SUSTENTA
 * ------------------------------------
 * Quem planeja o mes define a meta de GMV de cada marca em cada canal. Os
 * totais (por marca, por canal e global) sao soma, nunca digitados.
 *
 * A URL E' A FONTE DE VERDADE DO MES
 * ----------------------------------
 * `?mes=AAAA-MM`. Sem ele (ou invalido), o mes corrente de Brasilia — sem
 * gravar na URL, para o link seguir o mes corrente quando ele virar.
 *
 * SEM LOGIN, COM HISTORICO
 * ------------------------
 * Qualquer pessoa com o link edita (decisao do proprietario). Toda gravacao
 * fica no historico do banco com valor anterior e novo.
 *
 * SEM MODO DEMONSTRACAO
 * ---------------------
 * Com a API fora, a tela mostra o erro: meta de exemplo seria lida como real.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronLeft, ChevronRight, Copy, Save, Target, Undo2 } from "lucide-react";

import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import Note from "@/components/ui/Note";
import StateBlock, { RetryButton } from "@/components/ui/StateBlock";
import MetasGrade from "@/components/metas/MetasGrade";
import { MetasErro, fetchMetas, fetchRealizado, salvarMetas } from "@/lib/metas-api";
import {
  analisar,
  deslocarMes,
  fmtUltimaAlteracao,
  mesAtualBrt,
  parseMes,
  podeIrPara,
  rascunhoDe,
  rascunhoParcialDe,
  realizadoDasMarcas,
  rotuloMes,
  valoresDaResposta,
  type Chave,
  type FalhaMetas,
  type Rascunho,
  type Valores,
} from "@/lib/metas";
import type { Tom } from "@/components/ui/tone";

type Carga =
  | { mes: string; salvo: Valores; ultima: string | null }
  | { mes: string; falha: FalhaMetas };

const BOTAO =
  "inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg px-3.5 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50";
const BOTAO_SECUNDARIO = `${BOTAO} border border-line-strong bg-surface text-ink hover:bg-raised`;
const BOTAO_PRIMARIO = `${BOTAO} bg-accent text-accent-on hover:opacity-90`;

const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`;

export default function MetasClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const mesCorrente = useMemo(() => mesAtualBrt(), []);
  const mes = parseMes(searchParams.get("mes")) ?? mesCorrente;
  // Mes vigente para as respostas assincronas (salvar, copiar): o mes pode
  // trocar pelo voltar/avancar do navegador enquanto a requisicao esta em voo,
  // e a resposta de outro mes nao pode cair no estado deste.
  const mesRef = useRef(mes);
  mesRef.current = mes;

  const [carga, setCarga] = useState<Carga | null>(null);
  const [rascunho, setRascunho] = useState<Rascunho>({});
  const [realizado, setRealizado] = useState<{ mes: string; valores: Valores | null | "futuro" } | null>(null);
  const [salvando, setSalvando] = useState(false);
  const [copiando, setCopiando] = useState(false);
  const [aviso, setAviso] = useState<{ tom: Tom; texto: string } | null>(null);
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setAviso(null);
    fetchMetas(mes, ctrl.signal)
      .then((resp) => {
        const salvo = valoresDaResposta(resp);
        setCarga({ mes, salvo, ultima: resp.ultima_alteracao });
        setRascunho(rascunhoDe(salvo));
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        const falha = err instanceof MetasErro ? err.falha : { tipo: "desconhecido" as const, mensagem: String(err) };
        setCarga({ mes, falha });
      });
    return () => ctrl.abort();
  }, [mes, tentativa]);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchRealizado(mes, ctrl.signal)
      .then((linhas) =>
        setRealizado({ mes, valores: linhas === "futuro" ? "futuro" : linhas ? realizadoDasMarcas(linhas) : null }),
      )
      .catch(() => {
        if (!ctrl.signal.aborted) setRealizado({ mes, valores: null });
      });
    return () => ctrl.abort();
  }, [mes]);

  const pronta = carga !== null && carga.mes === mes && "salvo" in carga ? carga : null;
  const salvo = useMemo(() => pronta?.salvo ?? {}, [pronta]);
  const analise = useMemo(() => analisar(salvo, rascunho), [salvo, rascunho]);
  const nInvalidas = Object.keys(analise.invalidas).length;
  const nAlteradas = analise.alteradas.length;
  const sujo = pronta !== null && (nAlteradas > 0 || nInvalidas > 0);

  // Fechar a aba ou recarregar com alteracao pendente pede confirmacao.
  useEffect(() => {
    if (!sujo) return;
    const segurar = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", segurar);
    return () => window.removeEventListener("beforeunload", segurar);
  }, [sujo]);

  // Navegar pelo menu (link interno do Next) tambem pede confirmacao. Captura
  // no documento roda antes do `onClick` do `<Link>`, e `preventDefault` faz o
  // Next desistir da navegacao.
  useEffect(() => {
    if (!sujo) return;
    const interceptar = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const link = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link || link.target === "_blank" || link.origin !== window.location.origin) return;
      if (link.pathname === window.location.pathname) return;
      if (!window.confirm(`Há alterações não salvas em ${rotuloMes(mes)}. Sair e descartá-las?`)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("click", interceptar, true);
    return () => document.removeEventListener("click", interceptar, true);
  }, [sujo, mes]);

  const irPara = useCallback(
    (destino: string) => {
      if (!podeIrPara(destino) || destino === mes) return;
      if (sujo && !window.confirm(`Há alterações não salvas em ${rotuloMes(mes)}. Trocar de mês e descartá-las?`)) {
        return;
      }
      const qs = destino === mesCorrente ? "" : `?mes=${destino}`;
      router.replace(`/metas${qs}`, { scroll: false });
    },
    [mes, mesCorrente, router, sujo],
  );

  const onChange = useCallback((k: Chave, texto: string) => {
    setRascunho((r) => ({ ...r, [k]: texto }));
  }, []);

  async function salvar() {
    if (nInvalidas > 0) {
      setAviso({ tom: "critico", texto: `Corrija ${plural(nInvalidas, "campo inválido", "campos inválidos")} antes de salvar.` });
      return;
    }
    if (!nAlteradas) return;
    const alvo = mes;
    setSalvando(true);
    setAviso(null);
    try {
      const resp = await salvarMetas(alvo, analise.alteradas);
      // Gravou no banco; se o mes da tela ja' e' outro, a carga dele traz o
      // estado certo quando o usuario voltar.
      if (mesRef.current !== alvo) return;
      const novo = valoresDaResposta(resp);
      setCarga({ mes, salvo: novo, ultima: resp.ultima_alteracao });
      setRascunho(rascunhoDe(novo));
      setAviso({
        tom: "bom",
        texto: `Metas de ${rotuloMes(mes)} salvas · ${plural(resp.alteracoes, "alteração", "alterações")}.`,
      });
    } catch (err) {
      if (mesRef.current !== alvo) return;
      const texto = err instanceof MetasErro ? err.falha.mensagem : "Não foi possível salvar.";
      setAviso({ tom: "critico", texto: `${texto} Nada foi perdido: os valores continuam na tela.` });
    } finally {
      setSalvando(false);
    }
  }

  async function copiarAnterior() {
    const alvo = mes;
    const anterior = deslocarMes(alvo, -1);
    if (!podeIrPara(anterior)) return;
    setCopiando(true);
    setAviso(null);
    try {
      const resp = await fetchMetas(anterior);
      if (mesRef.current !== alvo) return;
      if (!resp.celulas.length) {
        setAviso({ tom: "neutro", texto: `${capitalizar(rotuloMes(anterior))} não tem metas para copiar.` });
        return;
      }
      // So' as celulas que o mes anterior TEM: as demais ficam como estao,
      // para copiar nunca apagar uma meta ja' digitada neste mes.
      setRascunho((r) => ({ ...r, ...rascunhoParcialDe(valoresDaResposta(resp)) }));
      setAviso({
        tom: "atencao",
        texto: `Metas de ${rotuloMes(anterior)} copiadas para ${rotuloMes(alvo)} nas células que ${rotuloMes(anterior).split(" ")[0]} tinha; as demais ficaram como estavam. Revise e clique em Salvar metas.`,
      });
    } catch (err) {
      if (mesRef.current !== alvo) return;
      const texto = err instanceof MetasErro ? err.falha.mensagem : "Não foi possível copiar.";
      setAviso({ tom: "critico", texto });
    } finally {
      setCopiando(false);
    }
  }

  function descartar() {
    setRascunho(rascunhoDe(salvo));
    setAviso(null);
  }

  const ultima = pronta ? fmtUltimaAlteracao(pronta.ultima) : null;
  const falha = carga !== null && carga.mes === mes && "falha" in carga ? carga.falha : null;
  const realizadoDoMes = realizado && realizado.mes === mes ? realizado.valores : null;
  const ocupado = salvando || copiando;
  const vazio = pronta !== null && Object.keys(pronta.salvo).length === 0 && !sujo;

  return (
    <PageContainer>
      <PageHeader
        title="Metas"
        icon={Target}
        meta={
          <span className="inline-flex min-h-8 items-center rounded-lg border border-line bg-surface px-2.5 text-xs text-ink-muted">
            {ultima ? `Última alteração em ${ultima}` : pronta ? "Nenhuma meta salva neste mês" : "Carregando…"}
          </span>
        }
      />

      <nav aria-label="Mês das metas" className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={`${BOTAO_SECUNDARIO} min-w-11 px-0`}
          onClick={() => irPara(deslocarMes(mes, -1))}
          disabled={!podeIrPara(deslocarMes(mes, -1)) || ocupado}
          aria-label={`Mês anterior: ${rotuloMes(deslocarMes(mes, -1))}`}
        >
          <ChevronLeft aria-hidden="true" className="h-4 w-4" />
        </button>
        <h3 className="min-w-[180px] text-center text-lg font-semibold text-ink" aria-live="polite">
          {capitalizar(rotuloMes(mes))}
        </h3>
        <button
          type="button"
          className={`${BOTAO_SECUNDARIO} min-w-11 px-0`}
          onClick={() => irPara(deslocarMes(mes, 1))}
          disabled={!podeIrPara(deslocarMes(mes, 1)) || ocupado}
          aria-label={`Próximo mês: ${rotuloMes(deslocarMes(mes, 1))}`}
        >
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
        </button>
        {mes !== mesCorrente && (
          <button type="button" className={BOTAO_SECUNDARIO} onClick={() => irPara(mesCorrente)} disabled={ocupado}>
            Ir para o mês atual
          </button>
        )}
      </nav>

      {falha ? (
        <StateBlock
          tipo="erro"
          titulo="Não foi possível carregar as metas"
          descricao={falha.mensagem}
          acao={<RetryButton onClick={() => setTentativa((n) => n + 1)} />}
        />
      ) : !pronta ? (
        <StateBlock tipo="carregando" titulo={`Carregando metas de ${rotuloMes(mes)}…`} />
      ) : (
        <>
          {vazio && (
            <Note>Nenhuma meta definida para {rotuloMes(mes)}.</Note>
          )}

          <MetasGrade
            rascunho={rascunho}
            salvo={salvo}
            analise={analise}
            realizado={realizadoDoMes}
            desabilitado={ocupado}
            onChange={onChange}
          />

          {aviso && (
            <Note tom={aviso.tom} papel={aviso.tom === "critico" ? "alert" : "status"}>
              {aviso.texto}
            </Note>
          )}

          <div className="sticky bottom-0 z-20 -mx-4 flex flex-wrap items-center justify-between gap-3 border-t border-line bg-canvas/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
            <p className="text-sm text-ink-muted" aria-live="polite">
              {nInvalidas > 0 ? (
                <span className="font-semibold text-crit-ink">
                  {plural(nInvalidas, "campo inválido", "campos inválidos")}
                </span>
              ) : nAlteradas > 0 ? (
                <span className="font-semibold text-accent-ink">
                  {plural(nAlteradas, "alteração não salva", "alterações não salvas")}
                </span>
              ) : (
                "Tudo salvo."
              )}{" "}
              <span className="text-ink-faint">Campo vazio = sem meta (diferente de zero).</span>
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className={BOTAO_SECUNDARIO}
                onClick={copiarAnterior}
                disabled={ocupado || !podeIrPara(deslocarMes(mes, -1))}
              >
                <Copy aria-hidden="true" className="h-4 w-4" />
                Copiar de {rotuloMes(deslocarMes(mes, -1)).split(" ")[0]}
              </button>
              <button type="button" className={BOTAO_SECUNDARIO} onClick={descartar} disabled={ocupado || !sujo}>
                <Undo2 aria-hidden="true" className="h-4 w-4" />
                Descartar
              </button>
              <button
                type="button"
                className={BOTAO_PRIMARIO}
                onClick={salvar}
                disabled={ocupado || nAlteradas === 0}
              >
                <Save aria-hidden="true" className="h-4 w-4" />
                {salvando ? "Salvando…" : "Salvar metas"}
              </button>
            </div>
          </div>
        </>
      )}
    </PageContainer>
  );
}

function capitalizar(texto: string): string {
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}
