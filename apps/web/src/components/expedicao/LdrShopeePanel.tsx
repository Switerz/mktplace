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
import KpiTile from "@/components/ui/KpiTile";
import Note from "@/components/ui/Note";
import Panel from "@/components/ui/Panel";
import {
  AVISO_LDR_SHOPEE,
  DIAS_LDR_SHOPEE,
  NOME_LDR_SHOPEE,
  VALOR_AUSENTE,
  estadoDaLdrShopee,
  formatarTaxaLdr,
  montarExclusoes,
  motivoDaTaxaAusente,
  queryDaLdrShopee,
  textoDaCobertura,
  textoDoEstado,
  type RespostaLdrShopee,
} from "@/lib/expedicao-shopee-ldr";
import { formatarInteiro } from "@/lib/expedicao-formato";

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
export function useLdrShopee(api: string, marca: string | null, ligada: boolean): EstadoLdr {
  const chave = queryDaLdrShopee(DIAS_LDR_SHOPEE, marca);
  const vigente = useRef(chave);
  vigente.current = chave;
  const [resposta, setResposta] = useState<RespostaLdrShopee | null>(null);
  const [carregando, setCarregando] = useState(ligada);
  const [erro, setErro] = useState(false);

  useEffect(() => {
    if (!ligada) {
      setResposta(null);
      setCarregando(false);
      setErro(false);
      return;
    }
    const minha = chave;
    let vivo = true;
    // A resposta anterior e' de outra marca (ou de outra fotografia): sai da
    // tela enquanto a nova nao chega.
    setResposta(null);
    setCarregando(true);
    setErro(false);
    fetch(`${api}${minha}`, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok) throw new Error("http");
        return (await r.json()) as RespostaLdrShopee;
      })
      .then((json) => {
        if (!vivo || vigente.current !== minha) return;
        setResposta(json);
        setCarregando(false);
      })
      .catch(() => {
        if (!vivo || vigente.current !== minha) return;
        // Erro NAO mantem o numero antigo na tela: ele seria de outra marca
        // ou de outra fotografia.
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
    <Panel titulo={titulo} id="exp-ldr-shopee">
      <p className="mb-2 text-xs text-ink-muted" data-testid="exp-ldr-shopee-aviso">
        {resposta?.disclaimer ?? AVISO_LDR_SHOPEE}
      </p>
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
        Vencimentos de {diaCurto(l.from)} a {diaCurto(l.to)} ({l.days} dias + hoje) · evento: coleta
        pela transportadora · só vencimentos fechados entram na taxa ({l.mature_due_days} fechado(s),{" "}
        {l.partial_due_days} em aberto).
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
        <KpiTile
          rotulo="LDR operacional"
          valor={formatarTaxaLdr(l.rate)}
          comparacao={motivo ?? `${numero(l.late)} de ${numero(l.base)} pedidos com prazo vencido`}
          contexto={AVISO_LDR_SHOPEE}
          tom={l.rate === null ? "ausente" : "neutro"}
          destaque
        />
        <KpiTile
          rotulo="Base (prazos vencidos)"
          valor={l.mature_due_days ? numero(l.base) : VALOR_AUSENTE}
          comparacao="Pagos, do vendedor, sem FBS e sem cancelados sem coleta."
          tom={l.mature_due_days ? "neutro" : "ausente"}
          destaque
        />
        <KpiTile
          rotulo="Atrasados"
          valor={l.mature_due_days ? numero(l.late) : VALOR_AUSENTE}
          comparacao={`${numero(l.shipped_late)} coletados depois do prazo · ${numero(l.pending_overdue)} vencidos sem coleta`}
          tom={l.mature_due_days ? "neutro" : "ausente"}
          destaque
        />
        <KpiTile
          rotulo="Vencem hoje ou amanhã"
          valor={numero(l.pending_at_risk)}
          comparacao="Ainda sem coleta e dentro do prazo."
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
