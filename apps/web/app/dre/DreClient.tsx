"use client";

/**
 * DRE gerencial — DRE-UX-1.
 *
 * PARA QUEM E' E QUE DECISAO SUSTENTA
 * ------------------------------------
 * Gestor comercial/financeiro: quanto cada canal e cada marca deixa depois
 * dos custos CONHECIDOS, e o que ainda falta para confiar no numero.
 *
 * Mostra o que foi calculado com seguranca, nomeia o que falta e bloqueia so'
 * o que nao pode ser calculado. A Shopee nao calculavel nao esconde ML e
 * TikTok; impostos e devolucoes ausentes nao viram zero.
 *
 * A URL E' A FONTE DE VERDADE
 * ----------------------------
 * `ref_month`, `channels` e `brands` vivem na querystring; a requisicao sai da
 * MESMA serializacao (`dreQuery`), entao o que esta na barra de endereco e' o
 * que foi pedido a API. Sem `ref_month`, a API escolhe a competencia madura
 * mais recente e a tela mostra qual foi — sem gravar na URL, para o link
 * continuar seguindo a competencia madura quando ela avancar.
 *
 * SEM MODO DEMONSTRACAO
 * ---------------------
 * Com a API fora, a tela mostra o erro. Numero de exemplo numa DRE seria lido
 * como margem real.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Calculator, Filter } from "lucide-react";

import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import Chip from "@/components/ui/Chip";
import Disclosure from "@/components/ui/Disclosure";
import Note from "@/components/ui/Note";
import { PeriodChip } from "@/components/ui/Freshness";
import StateBlock, { RetryButton } from "@/components/ui/StateBlock";
import DreFiltros from "@/components/dre/DreFiltros";
import DreResumo from "@/components/dre/DreResumo";
import DreVertical, { type OpcaoRecorte } from "@/components/dre/DreVertical";
import DreCanais from "@/components/dre/DreCanais";
import { DreMarcaCanalMatriz, DreMarcasTabela } from "@/components/dre/DreMarcas";
import BotaoRecorteCalculavel from "@/components/dre/BotaoRecorteCalculavel";
import { DreErro, fetchDre } from "@/lib/dre-api";
import {
  FILTROS_PADRAO,
  ROTULO_CANAL,
  avisoFotografia,
  canaisBloqueadores,
  canaisCalculaveisOcultos,
  classificarFalha,
  dreQuery,
  fmtInstanteBrt,
  juntarNomes,
  parseDreFiltros,
  queryCanonica,
  rotuloPeriodo,
  selecaoVazia,
  type DreFiltros as Filtros,
  type DreResponse,
  type FalhaDre,
} from "@/lib/dre";

type Resultado = { chave: string; resp: DreResponse } | { chave: string; falha: FalhaDre };

export default function DreClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  // Recorte aplicado = so' o que e' valido na URL; `chave` e' a forma canonica.
  const { filtros, query: chave, mudou } = useMemo(() => queryCanonica(searchParams.toString()), [searchParams]);

  // DRE-UX-2: URL com parametro invalido, parcialmente invalido, fora da ordem
  // ou de outra tela e' reescrita NA HORA para a forma canonica. Barra de
  // endereco, controles e requisicao dizem sempre o MESMO recorte — nunca uma
  // URL falsa sobre um escopo ampliado em silencio.
  useEffect(() => {
    if (mudou) router.replace(chave ? `/dre?${chave}` : "/dre", { scroll: false });
  }, [mudou, chave, router]);

  const [resultado, setResultado] = useState<Resultado | null>(null);
  const [tentativa, setTentativa] = useState(0);
  const [recorte, setRecorte] = useState("total");

  useEffect(() => {
    const ac = new AbortController();
    fetchDre(parseDreFiltros(new URLSearchParams(chave)), ac.signal)
      .then((resp) => setResultado({ chave, resp }))
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setResultado({ chave, falha: err instanceof DreErro ? err.falha : classificarFalha(null, null) });
      });
    return () => ac.abort();
  }, [chave, tentativa]);

  // So' vale o resultado da requisicao ATUAL: durante a troca de filtro, o
  // numero do filtro anterior nunca aparece sob o rotulo do novo.
  const atual = resultado && resultado.chave === chave ? resultado : null;
  const resp = atual && "resp" in atual ? atual.resp : null;
  const falha = atual && "falha" in atual ? atual.falha : null;
  const carregando = atual === null;

  const aplicar = useCallback(
    (parcial: Partial<Filtros>) => {
      const q = dreQuery({ ...filtros, ...parcial });
      router.replace(q ? `/dre?${q}` : "/dre", { scroll: false });
    },
    [filtros, router],
  );

  const tentarDeNovo = () => {
    setResultado(null);
    setTentativa((n) => n + 1);
  };

  const mesPadrao = resp && resp.periodo.origem === "padrao_competencia_madura" ? resp.periodo.meses[0] ?? null : null;
  const competencia = resp ? rotuloPeriodo(resp.periodo.meses) : null;
  const aviso = resp ? avisoFotografia(resp.fontes.insumos_dre, fmtInstanteBrt) : null;
  const ocultos = resp ? canaisCalculaveisOcultos(resp) : [];
  const bloqueadores = resp ? canaisBloqueadores(resp) : [];
  const vazio = resp ? selecaoVazia(resp) : false;

  const opcoes: OpcaoRecorte[] = useMemo(() => {
    if (!resp?.total) return [];
    return [
      { valor: "total", rotulo: "Total da seleção", celula: resp.total },
      ...resp.por_canal.map((c) => ({
        valor: `canal:${c.canal}`,
        rotulo: `Canal: ${c.canal_label ?? ROTULO_CANAL[c.canal ?? ""] ?? c.canal}`,
        celula: c,
      })),
      ...resp.por_marca.map((c) => ({
        valor: `marca:${c.marca}`,
        rotulo: `Marca: ${c.marca_label ?? c.marca}`,
        celula: c,
      })),
    ];
  }, [resp]);

  const atualizadoEm = resp?.total?.data_maxima.mart_carregado_em ?? null;
  const receitaAte = resp?.total?.data_maxima.receita_ultimo_dia ?? null;
  const insumoEm = resp?.fontes.insumos_dre.publicado_em ?? null;

  return (
    <PageContainer>
      <PageHeader
        title="DRE"
        subtitle="Margem de contribuição conhecida por canal e marca — o que falta aparece nomeado."
        icon={Calculator}
        meta={
          resp ? (
            <>
              <PeriodChip label={`Competência ${competencia}${mesPadrao ? " · madura" : ""}`} />
              {atualizadoEm && (
                <Chip>Carga do mart: {fmtInstanteBrt(atualizadoEm)}</Chip>
              )}
              <Chip tom={aviso ? "atencao" : "neutro"} ponto>
                {aviso ? aviso.curto : `Fotografia ${insumoEm ? fmtInstanteBrt(insumoEm) : "ok"}`}
              </Chip>
            </>
          ) : undefined
        }
        filters={<DreFiltros filtros={filtros} mesPadrao={mesPadrao} onChange={aplicar} carregando={carregando} />}
      />

      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {carregando ? "Carregando a DRE…" : falha ? `Falha: ${falha.titulo}` : vazio ? "Sem dados para a seleção." : "DRE carregada."}
      </span>

      {carregando ? (
        <StateBlock tipo="carregando" titulo="Carregando a DRE…" descricao="Lendo receita, custos e insumos publicados." />
      ) : falha ? (
        <StateBlock
          tipo="erro"
          titulo={falha.titulo}
          descricao={falha.mensagem}
          acao={
            falha.tipo === "invalido" ? (
              <button
                type="button"
                onClick={() => aplicar(FILTROS_PADRAO)}
                className="mt-1 inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-current/30 px-3 text-xs font-semibold hover:bg-surface/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Filter aria-hidden="true" className="h-3.5 w-3.5" />
                Limpar filtros
              </button>
            ) : (
              <RetryButton onClick={tentarDeNovo} />
            )
          }
        />
      ) : resp && vazio ? (
        <>
          {aviso && (
            <Note tom={aviso.tom} papel="status">
              <strong className="font-semibold">{aviso.titulo}.</strong> {aviso.detalhe}
            </Note>
          )}
          <StateBlock
            tipo="vazio"
            titulo="Sem dados para esta seleção"
            descricao={`Nenhuma venda em ${competencia} para os canais e marcas escolhidos. Isto não é receita zero: não há célula para calcular.`}
          />
        </>
      ) : resp && resp.total ? (
        <>
          {aviso && (
            <Note tom={aviso.tom} papel="status">
              <strong className="font-semibold">{aviso.titulo}.</strong> {aviso.detalhe}
            </Note>
          )}

          <section aria-labelledby="dre-resumo" className="flex flex-col gap-3">
            <h2 id="dre-resumo" className="sr-only">Resumo da seleção</h2>
            <DreResumo total={resp.total} competencia={competencia ?? ""} bloqueadores={bloqueadores} />
            <p className="text-xs text-ink-muted">
              {receitaAte && <>Receita até {fmtInstanteBrt(receitaAte)}. </>}
              {resp.total.data_maxima.cmv_ultima_emissao && (
                <>CMV: última emissão fiscal {fmtInstanteBrt(resp.total.data_maxima.cmv_ultima_emissao)}. </>
              )}
              Não é lucro nem margem líquida: impostos e devoluções ainda não têm fonte.
            </p>
            {ocultos.length > 0 && (
              <Note
                tom="neutro"
                acao={<BotaoRecorteCalculavel canais={ocultos} aoAplicar={(canais) => aplicar({ canais })} />}
              >
                {/* DRE-UX-2: o canal que bloqueia vem NOMEADO (de `por_canal`). */}
                O total não é calculável porque {juntarNomes(bloqueadores)}{" "}
                {bloqueadores.length > 1 ? "não são calculáveis" : "não é calculável"}.{" "}
                {juntarNomes(ocultos.map((c) => ROTULO_CANAL[c]))} {ocultos.length > 1 ? "têm" : "tem"} margem conhecida —
                veja abaixo, ou filtre só {ocultos.length > 1 ? "esses canais" : "esse canal"} para o agregado calculado
                pela API.
              </Note>
            )}
          </section>

          <DreCanais canais={resp.por_canal} total={resp.total} />

          <DreVertical opcoes={opcoes} selecionado={recorte} aoEscolher={setRecorte} />

          <DreMarcasTabela
            marcas={resp.por_marca}
            total={resp.total}
            bloqueadores={bloqueadores}
            calculaveis={ocultos}
            aoFiltrar={(canais) => aplicar({ canais })}
          />
          <DreMarcaCanalMatriz
            celulas={resp.por_canal_marca}
            marcas={resp.por_marca}
            canais={resp.por_canal.map((c) => c.canal ?? "").filter(Boolean)}
          />

          <Disclosure rotulo="Como ler esta DRE">
            <ul className="flex list-disc flex-col gap-1 pl-4">
              <li>
                Margem de contribuição conhecida = receita menos os custos disponíveis. Não é lucro nem margem líquida:
                impostos e devoluções ainda não têm fonte ratificada.
              </li>
              <li>Valor ausente nunca vira zero: a linha mostra o status e o motivo. Zero aparece só quando a fonte mediu zero.</li>
              <li>Afiliados do TikTok já estão dentro de Taxas; a linha é decomposição e não é somada de novo.</li>
              <li>
                Classificação de um agregado é a pior entre as células. Por isso canal e célula marca × canal mostram cada
                uma a sua.
              </li>
              <li>Share = receita do recorte ÷ receita total da seleção, com os valores sem arredondamento.</li>
              <li>
                Ranking de margem só entre marcas comparáveis: calculáveis, com taxas e CMV na conta e o mesmo conjunto de
                custos.
              </li>
              <li>Uma competência só fecha depois de dois meses completos (maturação M+2). Mês imaturo sai não calculável.</li>
            </ul>
          </Disclosure>
        </>
      ) : null}
    </PageContainer>
  );
}
