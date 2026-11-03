"use client";

/**
 * Gate EXPEDICAO-UX-UNIFICADA-1 — o cabecalho que os tres canais compartilham.
 *
 * Duas faixas, com papeis diferentes:
 *
 *   1. a faixa STICKY (PageBar): canal no titulo, seletor de canal e a
 *      fotografia — publicacao, frescor, fonte e modo de publicacao. E' o que
 *      precisa continuar visivel durante a rolagem de uma fila de 100 linhas;
 *   2. a faixa de CONTROLES, que rola com a pagina: profundidade (visao do
 *      gestor / detalhe operacional) e os controles proprios do canal. No
 *      mobile, cinco seletores dentro da faixa sticky comiam metade da tela.
 *
 * O componente nao conhece canal nenhum: recebe o rotulo, a `Fotografia` que o
 * contrato do canal montou e os controles que o painel quiser pendurar.
 */
import { useId, type ReactNode } from "react";

import Chip from "@/components/ui/Chip";
import PageBar from "@/components/ui/PageBar";
import InfoDica from "@/components/ui/InfoDica";
import Segmented from "@/components/ui/Segmented";
import { ROTULO_VISAO, VISOES, type Fotografia, type Visao } from "@/lib/expedicao-ux";
import { ALL_BRAND_OPTIONS, brandLabel } from "@/lib/filters/brands";

/** Ancora da faixa de controles — o convite ao detalhe leva o foco ate' ela. */
export const ID_FAIXA_DE_CONTROLES = "exp-controles";

/** "30/10, 16:10", no fuso do navegador. */
function dataHoraCurta(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/**
 * EXPEDICAO-ULTIMA-ATUALIZACAO (pedido de 30/10/2026): no topo, UM selo —
 * "Última atualização 30/10, 16:10". "Fotografia", "Atualizada" e "Fonte até"
 * sairam da tela; a idade e a leitura da fonte ficam no `title` do selo.
 *
 * So' o aviso "Desatualizada" continua aparecendo, e ESCRITO (nao so'
 * pintado de ambar): cor nao pode ser o unico sinal de que os numeros
 * abaixo descrevem o passado. O veredito e' do contrato do canal.
 */
export function ChipsFotografia({ fotografia: f }: { fotografia: Fotografia }) {
  const detalhe = [
    f.idade !== null ? `Publicada há ${f.idade}.` : null,
    f.fonteLidaAte ? `Fonte lida até ${dataHoraCurta(f.fonteLidaAte)}.` : null,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <>
      <Chip title={detalhe || undefined}>
        Última atualização{" "}
        {f.publicadaEm ? <time dateTime={f.publicadaEm}>{dataHoraCurta(f.publicadaEm)}</time> : "—"}
      </Chip>
      {f.publicadaEm && f.desatualizada ? (
        <Chip ponto tom="atencao" title="Os números descrevem o instante da publicação, não agora.">
          Desatualizada
        </Chip>
      ) : null}
    </>
  );
}

/**
 * Filtro de marca comum aos tres canais. Opcoes: o mapa canonico de marcas
 * mais qualquer marca que o payload trouxer (nunca some uma marca nova).
 * `<select>` nativo: lista curta, teclado e leitor de tela de graca.
 */
export function SeletorMarca({
  marca,
  marcasPresentes,
  aoTrocar,
}: {
  marca: string | null;
  marcasPresentes: readonly string[];
  aoTrocar: (m: string | null) => void;
}) {
  const id = useId();
  const opcoes = [...new Set([...ALL_BRAND_OPTIONS.map((b) => b.value), ...marcasPresentes, ...(marca ? [marca] : [])])];
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="text-xs text-ink-faint">
        Marca
      </label>
      <select
        id={id}
        value={marca ?? ""}
        onChange={(e) => aoTrocar(e.target.value || null)}
        className="min-h-11 rounded-md border border-line bg-surface px-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <option value="">Todas as marcas</option>
        {opcoes.map((m) => (
          <option key={m} value={m}>
            {brandLabel(m)}
          </option>
        ))}
      </select>
    </div>
  );
}

export default function CabecalhoExpedicao({
  canal,
  seletorCanal,
  fotografia,
  visao,
  aoTrocarVisao,
  controles,
  definicao,
  filtroMarca,
}: {
  /** Rotulo do canal, que vai NO TITULO: numero de uma loja sob o titulo de
   *  outra foi o defeito do EXP-3C2-R/V. */
  canal: string;
  seletorCanal?: ReactNode;
  fotografia: Fotografia | null;
  visao: Visao;
  aoTrocarVisao?: (v: Visao) => void;
  controles?: ReactNode;
  /** Uma linha: o que esta tela mede neste canal (EXPEDICAO-UX-3). */
  definicao?: string;
  /** `SeletorMarca` montado pelo painel, com as marcas do payload. */
  filtroMarca?: ReactNode;
}) {
  const idProfundidade = useId();
  return (
    <>
      <PageBar
        titulo={
          <>
            {/* h2, nao h1: o h1 da pagina e' do shell. */}
            <h2 className="text-base font-semibold tracking-tight text-ink">
              Expedição — {canal}
            </h2>
            {/* UI-EXPEDICAO-1: a definicao vira um "ⓘ" ao lado do titulo. */}
            {definicao ? (
              <span data-testid="exp-definicao" className="inline-flex">
                <InfoDica texto={definicao} />
              </span>
            ) : null}
            {seletorCanal}
          </>
        }
        meta={fotografia ? <ChipsFotografia fotografia={fotografia} /> : undefined}
      />
      {aoTrocarVisao || controles || filtroMarca ? (
        <div
          id={ID_FAIXA_DE_CONTROLES}
          className="flex scroll-mt-24 flex-wrap items-center gap-x-4 gap-y-2"
        >
          {aoTrocarVisao ? (
            <div className="flex flex-wrap items-center gap-2">
              <span id={idProfundidade} className="text-xs text-ink-faint">
                Profundidade
              </span>
              <Segmented
                rotulo="Profundidade"
                rotuladoPor={idProfundidade}
                opcoes={VISOES.map((v) => ({ valor: v, texto: ROTULO_VISAO[v] }))}
                valor={visao}
                aoEscolher={aoTrocarVisao}
              />
            </div>
          ) : null}
          {filtroMarca}
          {controles}
        </div>
      ) : null}
    </>
  );
}
