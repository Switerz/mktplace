"use client";

/**
 * T-0010 — secao "Jornada": quantos pedidos ha em cada etapa do fluxo.
 *
 * Duas leituras, SEPARADAS e ROTULADAS:
 *   - "Agora — fila aberta": a fotografia dos pedidos pagos ainda nao
 *     expedidos (Shopee, Mercado Livre). O TikTok nao publica fila;
 *   - "Coorte": os pedidos de uma janela de dias FECHADOS (termina ontem),
 *     por etapa (TikTok; Shopee com a LDR ativa). O ML nao tem coorte.
 *
 * Etapas sao exclusivas e aparecem como barras com contagem e percentual
 * sobre a base. Sobreposicoes aparecem a' parte, como subconjunto
 * ("62 vencidos dentro de Aguardando"), e NUNCA sao somadas.
 *
 * A rota ainda nao existe em producao: 404, erro de rede e `disabled` viram
 * "Jornada indisponível" discreto — sem alerta vermelho, sem quebrar a tela.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import Chip from "@/components/ui/Chip";
import Panel from "@/components/ui/Panel";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import {
  avisoDeMarcaNaoAplicada,
  coorteDesatualizada,
  estadoDaJornada,
  explicarMotivo,
  motivoDaSecao,
  motivoDoTopo,
  filaDesatualizada,
  larguraDaEtapa,
  lerJornada,
  queryDaJornada,
  rotuloDaCoorte,
  rotuloEtapa,
  rotuloSobreposicao,
  textoDaSobreposicao,
  type CanalJornada,
  type CargaJornada,
  type EtapaJornada,
  type RespostaJornada,
  type SobreposicaoJornada,
} from "@/lib/expedicao-jornada";
import { formatarInteiro, formatarPercentualComBase } from "@/lib/expedicao-formato";

import AjudaMetrica from "./AjudaMetrica";
import { ChipsFotografia } from "./CabecalhoExpedicao";
import { EstadoSecao } from "./LayoutExpedicao";
import { SecaoGerencial } from "./SecoesExpedicao";

/** "dd/mm hh:mm", no fuso do navegador (o mesmo dos selos do cabecalho). */
function dataHoraCurta(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const dd = (n: number) => String(n).padStart(2, "0");
  return `${dd(d.getDate())}/${dd(d.getMonth() + 1)} ${dd(d.getHours())}:${dd(d.getMinutes())}`;
}

const CARGA_INICIAL: CargaJornada = { carregando: true, http: null, erroDeRede: false, resultado: null };

/**
 * UMA requisicao a' rota da jornada por (canal, marca, janela, evento).
 * Resposta antiga que chega depois da nova e' descartada.
 */
export function useJornada(
  api: string,
  canal: CanalJornada,
  marca: string | null,
  opcoes: { dias?: number; evento?: "coleta" | "despacho" } = {},
): { carga: CargaJornada; tentarDeNovo: () => void } {
  const url = `${api}${queryDaJornada(canal, marca, opcoes)}`;
  const vigente = useRef(url);
  vigente.current = url;
  const [carga, setCarga] = useState<CargaJornada>(CARGA_INICIAL);
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => {
    let vivo = true;
    const minha = url;
    setCarga(CARGA_INICIAL);
    buscarExpedicao(minha, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!vivo || vigente.current !== minha) return;
        if (!r.ok) {
          setCarga({ carregando: false, http: r.status, erroDeRede: false, resultado: null });
          return;
        }
        let json: unknown = null;
        try {
          json = await r.json();
        } catch {
          json = null;
        }
        if (!vivo || vigente.current !== minha) return;
        setCarga({ carregando: false, http: r.status, erroDeRede: false, resultado: lerJornada(json, canal) });
      })
      .catch(() => {
        if (!vivo || vigente.current !== minha) return;
        setCarga({ carregando: false, http: null, erroDeRede: true, resultado: null });
      });
    return () => {
      vivo = false;
    };
  }, [url, canal, tentativa]);

  const tentarDeNovo = useCallback(() => setTentativa((n) => n + 1), []);
  return { carga, tentarDeNovo };
}

function ListaDeEtapas({
  etapas,
  base,
  evento,
  rotulo,
}: {
  etapas: EtapaJornada[];
  base: number;
  evento: "coleta" | "despacho" | null;
  rotulo: string;
}) {
  if (base === 0) {
    return (
      <p role="status" className="text-sm text-ink-muted">
        Nenhum pedido nesta leitura.
      </p>
    );
  }
  return (
    <ol aria-label={rotulo} className="flex flex-col gap-2">
      {etapas.map((e) => (
        <li key={e.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-1">
          <span className="min-w-0 text-sm text-ink">{rotuloEtapa(e, evento)}</span>
          <span className="text-right text-xs tabular-nums text-ink-muted">
            <span className="text-sm font-semibold text-ink">{formatarInteiro(e.count)}</span>{" "}
            · {formatarPercentualComBase(e.count, base)}
          </span>
          <span aria-hidden="true" className="col-span-2 block h-2 w-full overflow-hidden rounded bg-raised">
            <span className="block h-full rounded bg-accent/70" style={{ width: `${larguraDaEtapa(e.count, base)}%` }} />
          </span>
        </li>
      ))}
    </ol>
  );
}

function Sobreposicoes({
  itens,
  etapas,
  base,
  evento,
}: {
  itens: SobreposicaoJornada[];
  etapas: EtapaJornada[];
  base: number;
  evento: "coleta" | "despacho" | null;
}) {
  if (!itens.length) return null;
  return (
    <div className="mt-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
        Dentro dessas etapas — não se somam
      </h4>
      <ul className="mt-1.5 flex flex-col gap-1 text-xs text-ink-muted">
        {itens.map((s) => (
          <li key={s.key} data-sobreposicao={s.key} title={rotuloSobreposicao(s.key)}>
            {textoDaSobreposicao(s, etapas, base, evento)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Limitacoes({ itens }: { itens: string[] }) {
  if (!itens.length) return null;
  return (
    <ul className="mt-3 list-disc pl-4 text-xs text-ink-muted" aria-label="Limitações">
      {itens.map((l) => (
        <li key={l}>{l}</li>
      ))}
    </ul>
  );
}

/**
 * R21: secao que veio `null`. Com motivo no `reason` desta secao (ex.:
 * `coorte: table_not_migrated`), a ausencia e' de AGORA e o motivo vai
 * traduzido; sem ele, e' estrutural do canal ("Indisponível neste canal").
 * A outra secao continua na tela.
 */
function SecaoNula({ titulo, secao, r }: { titulo: string; secao: "fila_aberta" | "coorte"; r: RespostaJornada }) {
  const motivo = motivoDaSecao(r, secao);
  if (motivo) {
    return (
      <Panel titulo={titulo}>
        <div data-testid={`exp-jornada-${secao}-indisponivel`}>
          <EstadoSecao tipo="indisponivel" titulo={`Indisponível agora: ${motivo}.`} />
        </div>
        <Limitacoes itens={r.limitacoes} />
      </Panel>
    );
  }
  return (
    <Panel titulo={titulo}>
      <p role="status" className="text-sm text-ink-muted" data-testid={`exp-jornada-${secao}-indisponivel`}>
        Indisponível neste canal{r.limitacoes.length ? `: ${r.limitacoes[0]}` : "."}
      </p>
      <Limitacoes itens={r.limitacoes.slice(1)} />
    </Panel>
  );
}

/** A fila aberta tem etapa REAL (T-0015), nao so' "aguardando expedicao". */
export function filaTemEtapasReais(r: RespostaJornada): boolean {
  const f = r.fila_aberta;
  return Boolean(
    f && f.etapas.some((e) => e.key !== "aguardando_expedicao" && e.key !== "nao_publicada"),
  );
}

function BlocoFilaAberta({ r, compacta = false }: { r: RespostaJornada; compacta?: boolean }) {
  const f = r.fila_aberta;
  if (!f) return <SecaoNula titulo="Agora — fila aberta" secao="fila_aberta" r={r} />;
  return (
    <Panel
      titulo="Agora — fila aberta"
      descricao={`${formatarInteiro(f.base)} ${f.grain === "shipment" ? "envios" : "pedidos"} pagos aguardando expedição.`}
      // O "ⓘ" fica fora do h3: o nome do titulo continua so' o titulo.
      acao={
        <span className="flex flex-wrap items-center gap-1.5">
          <AjudaMetrica chave="jornada.fila_aberta" />
          {f.snapshot_at ? (
            <ChipsFotografia
              fotografia={{ rotulo: "Fotografia", publicadaEm: f.snapshot_at, idade: null, desatualizada: filaDesatualizada(f) }}
            />
          ) : null}
        </span>
      }
    >
      {!f.etapas_fecham ? (
        <div className="mb-2">
          <EstadoSecao tipo="parcial" titulo="as etapas não fecham com a base." />
        </div>
      ) : null}
      <ListaDeEtapas etapas={f.etapas} base={f.base} evento={null} rotulo="Etapas da fila aberta" />
      {/* Compacta (fila da Shopee/ML): vencidos, travados etc. ja' estao no
          "Risco atual" — repeti-los aqui seria o mesmo numero duas vezes. */}
      {compacta ? null : (
        <Sobreposicoes itens={f.sobreposicoes} etapas={f.etapas} base={f.base} evento={null} />
      )}
      {/* As limitacoes (ex.: "FBS nao separado na fila aberta") ficam sempre:
          sao elas que dizem que esta base nao e' a do "Risco atual". */}
      <Limitacoes itens={f.limitacoes} />
    </Panel>
  );
}

function BlocoCoorte({ r }: { r: RespostaJornada }) {
  const c = r.coorte;
  if (!c) return <SecaoNula titulo="Coorte" secao="coorte" r={r} />;
  const evento = c.evento;
  // R21: selo pelo booleano OU pelo `reason` da secao (fonte/publicacao velha).
  const velha = coorteDesatualizada(r);
  return (
    <Panel
      titulo={`Coorte — ${rotuloDaCoorte(c)}`}
      descricao={`${formatarInteiro(c.base)} pedidos · dias fechados · evento: ${
        evento === "despacho" ? "despacho" : "coleta"
      }.`}
      acao={
        // Mesmo padrao de frescor do cabecalho: instante + estado ESCRITO.
        // O "ⓘ" fica fora do h3, como na fila aberta.
        <span className="flex flex-wrap items-center gap-1.5">
          <AjudaMetrica chave="jornada.coorte" />
          {c.publicado_em ? (
            <Chip title="Última publicação dos dias desta coorte">
              publicado em <time dateTime={c.publicado_em}>{dataHoraCurta(c.publicado_em)}</time>
            </Chip>
          ) : null}
          {c.desatualizada !== null || velha ? (
            <Chip ponto tom={velha ? "atencao" : "bom"}>
              {velha ? "Desatualizada" : "Atualizada"}
            </Chip>
          ) : null}
        </span>
      }
    >
      {!c.etapas_fecham ? (
        <div className="mb-2">
          <EstadoSecao tipo="parcial" titulo="as etapas não fecham com a base." />
        </div>
      ) : null}
      {velha ? (
        <div className="mb-2" data-testid="exp-jornada-coorte-desatualizada">
          <EstadoSecao tipo="desatualizado" titulo="publicação ou fonte fora do prazo esperado." />
        </div>
      ) : null}
      <ListaDeEtapas etapas={c.etapas} base={c.base} evento={evento} rotulo="Etapas da coorte" />
      <Sobreposicoes itens={c.sobreposicoes} etapas={c.etapas} base={c.base} evento={evento} />
      {c.fora_da_coorte.length ? (
        <p className="mt-2 text-xs text-ink-muted">
          Fora da coorte:{" "}
          {c.fora_da_coorte
            .map((f) =>
              f.count === null ? `${rotuloSobreposicao(f.key)}: indisponível` : `${formatarInteiro(f.count)} ${rotuloSobreposicao(f.key)}`,
            )
            .join(" · ")}
          .
        </p>
      ) : null}
      {c.maturidade ? (
        <p className="mt-1 text-xs text-ink-muted">
          {c.maturidade.dias_maduros} dia(s) fechado(s), {c.maturidade.dias_imaturos} ainda mudando.
        </p>
      ) : null}
      <Limitacoes itens={c.limitacoes} />
    </Panel>
  );
}

export default function JornadaPedidos({
  carga,
  aoTentarDeNovo,
  marca = null,
  compacta = false,
}: {
  carga: CargaJornada;
  aoTentarDeNovo: () => void;
  /** Marca pedida (`?marca=`), para conferir `brands_applied`. */
  marca?: string | null;
  /**
   * EXPEDICAO-TRAVADOS-1 — so' o que o "Risco atual" nao diz. A fila aberta
   * entra apenas com etapa REAL publicada (T-0015) e sem as sobreposicoes; a
   * coorte, quando existe. Sem nenhuma das duas, a secao inteira sai.
   */
  compacta?: boolean;
}) {
  const estado = estadoDaJornada(carga);
  const resposta = carga.resultado?.ok ? carga.resultado.resposta : null;
  if (compacta) {
    if (estado !== "pronta" || !resposta) return null;
    const comFila = filaTemEtapasReais(resposta);
    const comCoorte = Boolean(resposta.coorte);
    if (!comFila && !comCoorte) return null;
    return (
      <SecaoGerencial
        titulo="Jornada"
        dica="Em que etapa estão os pedidos: agora (fila aberta) e nos dias fechados do período (coorte)."
      >
        {avisoDeMarcaNaoAplicada(marca, resposta) ? (
          <div data-testid="exp-jornada-marca-nao-aplicada">
            <EstadoSecao tipo="parcial" titulo={avisoDeMarcaNaoAplicada(marca, resposta)!} />
          </div>
        ) : null}
        {resposta.status === "partial" && motivoDoTopo(resposta) ? (
          <EstadoSecao tipo="parcial" titulo={motivoDoTopo(resposta)!} />
        ) : null}
        <div className={`grid gap-3 ${comFila && comCoorte ? "lg:grid-cols-2" : ""}`} data-testid="exp-jornada">
          {comFila ? <BlocoFilaAberta r={resposta} compacta /> : null}
          {comCoorte ? <BlocoCoorte r={resposta} /> : null}
        </div>
      </SecaoGerencial>
    );
  }
  return (
    <SecaoGerencial
      titulo="Jornada"
      dica="Quantos pedidos há em cada etapa. A fila aberta (agora) e a coorte (dias fechados) são leituras separadas."
    >
      {estado === "carregando" ? <EstadoSecao tipo="carregando" titulo="Carregando a jornada…" /> : null}
      {estado === "indisponivel" ? (
        <p role="status" className="text-sm text-ink-muted" data-testid="exp-jornada-indisponivel">
          Jornada indisponível.
          {carga.erroDeRede ? (
            <button
              type="button"
              onClick={aoTentarDeNovo}
              className="ml-2 min-h-11 rounded-md px-2 text-xs font-medium text-ink-muted underline underline-offset-4 hover:text-ink"
            >
              Tentar novamente
            </button>
          ) : null}
        </p>
      ) : null}
      {estado === "erro" ? (
        <EstadoSecao
          tipo="erro"
          titulo="Não foi possível carregar a jornada."
          descricao={carga.resultado && !carga.resultado.ok ? "Resposta fora do contrato." : undefined}
          aoTentarDeNovo={aoTentarDeNovo}
        />
      ) : null}
      {estado === "fora_do_ar" && resposta ? (
        <EstadoSecao
          tipo="indisponivel"
          titulo="Jornada indisponível agora."
          descricao={explicarMotivo(resposta.reason) ?? undefined}
        />
      ) : null}
      {estado === "pronta" && resposta ? (
        <>
          {avisoDeMarcaNaoAplicada(marca, resposta) ? (
            <div data-testid="exp-jornada-marca-nao-aplicada">
              <EstadoSecao tipo="parcial" titulo={avisoDeMarcaNaoAplicada(marca, resposta)!} />
            </div>
          ) : null}
          {/* QA T-0010: "etapas não fecham" ja' aparece no bloco da secao;
              o topo so' diz o que o bloco nao diz (sem aviso duplicado). */}
          {resposta.status === "partial" && motivoDoTopo(resposta) ? (
            <EstadoSecao tipo="parcial" titulo={motivoDoTopo(resposta)!} />
          ) : null}
          <div className="grid gap-3 lg:grid-cols-2" data-testid="exp-jornada">
            <BlocoFilaAberta r={resposta} />
            <BlocoCoorte r={resposta} />
          </div>
        </>
      ) : null}
    </SecaoGerencial>
  );
}
