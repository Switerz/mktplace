"use client";

/**
 * EXPEDICAO-SHOPEE-LDR-1 — LDR operacional da Shopee, dentro da secao "Prazo".
 *
 * A rota (`/api/v1/expedicao/shopee/ldr`) e' lida UMA vez por `useLdrShopee`,
 * na tela da fila, e repartida entre o "Risco atual" (fila do vendedor x FBS)
 * e este painel. Tudo so' liga com `NEXT_PUBLIC_EXPEDICAO_SHOPEE_LDR_ENABLED`.
 *
 * SEM COR DE SEVERIDADE NA TAXA: a Shopee nao nos da' a referencia dela para
 * este recorte, e pintar 3% de verde ou de vermelho seria regra inventada. A
 * taxa fica neutra; o que tem regra (cobertura, fonte vencida, pendente
 * vencido) vem como alerta da API.
 */
import { useEffect, useRef, useState } from "react";

import { FaixaKpis } from "@/components/expedicao/SecoesExpedicao";
import { KpiComAjuda, TaxaComBase } from "@/components/expedicao/KpiComAjuda";
import InfoDica from "@/components/ui/InfoDica";
import Note from "@/components/ui/Note";
import Panel from "@/components/ui/Panel";
import {
  AVISO_LDR_SHOPEE,
  DIAS_LDR_SHOPEE,
  NOME_LDR_SHOPEE,
  VALOR_AUSENTE,
  estadoDaLdrShopee,
  montarExclusoes,
  motivoDaTaxaAusente,
  queryDaLdrShopee,
  textoDaCobertura,
  textoDoEstado,
  type RespostaLdrShopee,
} from "@/lib/expedicao-shopee-ldr";
import { formatarInteiro } from "@/lib/expedicao-formato";
import { buscarExpedicao } from "@/lib/expedicao-demo";

const numero = formatarInteiro;
const diaCurto = (iso: string) => {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
};

export interface EstadoLdr {
  resposta: RespostaLdrShopee | null;
  carregando: boolean;
  erro: boolean;
}

/**
 * UMA requisicao a' rota da LDR, compartilhada pelo "Risco atual" (fila do
 * vendedor x FBS) e por este painel. `ligada = false` nao faz requisicao
 * nenhuma — e' o caminho da flag desligada.
 */
export function useLdrShopee(
  api: string,
  marca: string | null,
  ligada: boolean,
  // UI-EXPEDICAO-1: o periodo do topo da pagina; sem ele, a janela padrao.
  dias: number = DIAS_LDR_SHOPEE,
): EstadoLdr {
  const chave = queryDaLdrShopee(dias, marca);
  const vigente = useRef(chave);
  vigente.current = chave;
  const [resposta, setResposta] = useState<RespostaLdrShopee | null>(null);
  const [carregando, setCarregando] = useState(ligada);
  const [erro, setErro] = useState(false);
  // Marca da resposta na tela. So' a troca de MARCA tira a resposta antiga:
  // trocar o periodo nao muda a fila aberta (vendedor x FBS), que e' "agora".
  const marcaDaResposta = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (!ligada) {
      setResposta(null);
      marcaDaResposta.current = undefined;
      setCarregando(false);
      setErro(false);
      return;
    }
    const minha = chave;
    let vivo = true;
    // A resposta anterior de OUTRA marca sai da tela enquanto a nova nao
    // chega. De outro periodo, fica: a divisao da fila aberta nao depende da
    // janela, e o "Risco atual" nao pisca para a fila com FBS a cada troca.
    if (marcaDaResposta.current !== marca) setResposta(null);
    setCarregando(true);
    setErro(false);
    buscarExpedicao(`${api}${minha}`, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok) throw new Error("http");
        return (await r.json()) as RespostaLdrShopee;
      })
      .then((json) => {
        if (!vivo || vigente.current !== minha) return;
        marcaDaResposta.current = marca;
        setResposta(json);
        setCarregando(false);
      })
      .catch(() => {
        if (!vivo || vigente.current !== minha) return;
        // Erro NAO mantem o numero antigo na tela: ele seria de outra marca
        // ou de outra fotografia.
        marcaDaResposta.current = undefined;
        setResposta(null);
        setErro(true);
        setCarregando(false);
      });
    return () => {
      vivo = false;
    };
  }, [api, chave, ligada]);

  return { resposta, carregando, erro };
}

export default function LdrShopeePanel({ ldr, marca }: { ldr: EstadoLdr; marca: string | null }) {
  const { resposta, carregando, erro } = ldr;
  const estado = estadoDaLdrShopee({ carregando, erro, resposta });
  const titulo = resposta?.metric_name ?? NOME_LDR_SHOPEE;

  return (
    <Panel
      id="exp-ldr-shopee"
      titulo={titulo}
      // Indicador interno, nao o oficial da Shopee: a ressalva mora no "ⓘ",
      // fora do h3 (o nome do titulo continua so' o titulo).
      acao={
        <span data-testid="exp-ldr-shopee-aviso" className="inline-flex">
          <InfoDica texto={resposta?.disclaimer ?? AVISO_LDR_SHOPEE} />
        </span>
      }
    >
      {estado !== "pronta" || !resposta?.ldr ? (
        <p
          className="rounded-lg border border-dashed border-line px-3 py-2 text-sm text-ink-muted"
          role={estado === "erro" ? "alert" : "status"}
          data-testid="exp-ldr-shopee-estado"
          data-estado={estado}
        >
          {textoDoEstado(estado, marca)}
        </p>
      ) : (
        <Conteudo r={resposta} />
      )}
    </Panel>
  );
}

function Conteudo({ r }: { r: RespostaLdrShopee }) {
  const l = r.ldr!;
  const motivo = motivoDaTaxaAusente(r);
  const exclusoes = montarExclusoes(r);
  const alertas = r.alerts.filter((a) => a.severity !== "info");
  // A API ja' manda o alerta de cobertura com o texto pronto; repeti-lo aqui
  // dizia a mesma coisa duas vezes. A frase local so' cobre resposta sem ele.
  const cobertura = alertas.some((a) => a.code.startsWith("cobertura_"))
    ? null
    : textoDaCobertura(r);
  return (
    <>
      <p className="mb-2 text-xs text-ink-muted" data-testid="exp-ldr-shopee-periodo">
        Vencimentos de {diaCurto(l.from)} a {diaCurto(l.to)} ({l.days} dias + hoje) · {l.mature_due_days}{" "}
        fechado(s) na taxa, {l.partial_due_days} em aberto.
      </p>
      {alertas.length > 0 && (
        <ul className="mb-3 space-y-1.5" data-testid="exp-ldr-shopee-alertas">
          {alertas.map((a) => (
            <li key={a.code}>
              <Note tom={a.severity === "critical" ? "critico" : "atencao"} papel="status">
                {a.message}
              </Note>
            </li>
          ))}
        </ul>
      )}
      <FaixaKpis rotulo="LDR operacional da Shopee" colunas={4}>
        <KpiComAjuda
          ajuda="shopee.ldr"
          rotulo="LDR operacional"
          valor={<TaxaComBase numerador={l.late} base={l.base} taxa={l.rate} />}
          comparacao={motivo}
          contexto={AVISO_LDR_SHOPEE}
          tom={l.rate === null ? "ausente" : "neutro"}
          destaque
        />
        <KpiComAjuda
          ajuda="shopee.ldr_base"
          rotulo="Base (prazos vencidos)"
          valor={l.mature_due_days ? numero(l.base) : VALOR_AUSENTE}
          tom={l.mature_due_days ? "neutro" : "ausente"}
          destaque
        />
        <KpiComAjuda
          ajuda="shopee.ldr_atrasados"
          rotulo="Atrasados"
          valor={l.mature_due_days ? numero(l.late) : VALOR_AUSENTE}
          comparacao={`${numero(l.shipped_late)} coletados depois do prazo · ${numero(l.pending_overdue)} vencidos sem coleta`}
          tom={l.mature_due_days ? "neutro" : "ausente"}
          destaque
        />
        <KpiComAjuda
          ajuda="shopee.ldr_em_risco"
          rotulo="Vencem hoje ou amanhã"
          valor={numero(l.pending_at_risk)}
          destaque
        />
      </FaixaKpis>
      {cobertura && (
        <Note
          tom={r.coverage?.state === "insuficiente" ? "critico" : "atencao"}
          papel="status"
          className="mt-3"
        >
          {cobertura}
        </Note>
      )}
      <h4 className="mt-4 text-xs font-semibold uppercase tracking-wide text-ink-faint">
        Fora da taxa, contados à parte
      </h4>
      <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid="exp-ldr-shopee-exclusoes">
        {exclusoes.map((e) => (
          <div key={e.chave} className="rounded-lg bg-raised px-3 py-2">
            <dt className="text-xs text-ink-muted">{e.rotulo}</dt>
            <dd className="text-sm text-ink">
              <span className="text-lg font-semibold tabular-nums">{numero(e.valor)}</span>
              {e.nota ? <span className="ml-2 text-xs text-ink-muted">{e.nota}</span> : null}
            </dd>
          </div>
        ))}
      </dl>
    </>
  );
}
