"""Regras da aba "B2B / Revendedores" (Gate TORRE-B2B-OCTAPRICE).

NADA DE REGRA DE PRECO AQUI. PMA, violacao, desvio e severidade vem prontos
das RPCs da Torre B2B (`b2b_revenda_fonte`). Este modulo so':

  1. valida o recorte pedido (allowlists, sem ecoar valor recebido);
  2. impoe o ESCOPO do MVP: os KPIs so' veem os canais da allowlist — a
     Amazon fica fora enquanto a deduplicacao da fonte estiver incorreta;
  3. diz de QUANDO e' o dado (frescor) e se ele esta' atrasado;
  4. transforma "nao sei" em `unavailable`, nunca em zero.

O relogio e' sempre injetado (`agora`), como no resto da API.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

from app.deps.period import APP_TIMEZONE
from app.schemas.b2b_revenda import (
    BuyBox,
    CanaisMarcasResponse,
    CanalExcluido,
    CoberturaIdentificacao,
    Escopo,
    FiltrosAplicados,
    Frescor,
    FrescorResponse,
    Kpis,
    LinhaMarcaCanal,
    LinhaSku,
    ResumoResponse,
    Severidade,
    SkusResponse,
)
from app.services.b2b_revenda_fonte import (
    LIMITE_SELLERS_COBERTURA,
    CargaFonte,
    FonteComCache,
    FonteIndisponivel,
    FiltrosFonte,
)

# ---------------------------------------------------------------------------
# Constantes de produto
# ---------------------------------------------------------------------------
#: O n8n da Torre B2B importa o e-mail do OctaPrice as 08h40, 10h40 e 12h40
#: BRT. Depois das 13h o relatorio do dia ja' deveria estar la'.
HORA_CORTE_BRT = time(13, 0)

MARCAS = {"AP": "Ápice", "BB": "Barbours", "KS": "Kokeshi"}
ROTULO_SEM_MARCA = "Marca não mapeada"
SEVERIDADES = ("grave", "media", "leve")
UFS = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)
MAX_SKUS_FILTRO = 50
MAX_TAM_SKU = 80
LIMITE_SKUS_PADRAO = 10
LIMITE_SKUS_MAX = 50
_RE_MARCA = re.compile(r"^[A-Z0-9]{2}$")
_RE_SKU = re.compile(r"^[^\x00-\x1f\x7f]+$")

#: A tela ja' rotula "Piso: PMA do OctaPrice."; o aviso diz o que ele NAO e'.
AVISO_PISO = (
    "Não é o preço sugerido de revenda (PDV) usado na visão Lojas próprias, "
    "e os dois não se comparam."
)
MOTIVO_AMAZON = (
    "Fora dos KPIs: a deduplicação da fonte guarda um vendedor por anúncio e "
    "descarta os demais vendedores do mesmo anúncio de catálogo."
)
MOTIVO_FORA_ALLOWLIST = "Canal novo na fonte, ainda não revisado para os KPIs."

LIMITACOES = [
    "Violação, desvio e PMA vêm prontos do relatório do OctaPrice; a Torre não aplica tolerância.",
    "Amazon fora dos KPIs enquanto a deduplicação da fonte estiver incorreta.",
    "Seller é identificado pelo nome normalizado: nomes iguais em canais diferentes contam como um.",
    "Cliente B2B depende da identificação feita na Torre B2B; seller sem vínculo não tem cliente.",
    "Histórico limitado à retenção da fonte (cerca de 90 dias).",
    "Horários de coleta e importação não são expostos pelas RPCs; o frescor é medido pelo dia do relatório.",
]

MENSAGENS = {
    "feature_flag_desligada": "Aba B2B / Revendedores ainda não habilitada.",
    "credencial_ausente": "A credencial de leitura da Torre B2B ainda não foi provisionada.",
    "credencial_invalida": "A credencial configurada para a Torre B2B é inválida.",
    "configuracao_invalida": "A configuração de canais desta aba é inválida.",
    "fonte_inacessivel": "A Torre B2B não respondeu.",
    "permissao_negada": "A credencial de leitura não tem permissão sobre algum objeto do contrato.",
    "contrato_da_fonte_quebrado": "A Torre B2B mudou uma função ou coluna do contrato de leitura.",
    "fonte_lenta": "A Torre B2B demorou além do limite e a consulta foi cancelada.",
    "falha_desconhecida": "Falha inesperada ao ler a Torre B2B.",
    "fonte_sem_dados": "A Torre B2B não tem nenhum relatório do OctaPrice carregado.",
    "sem_relatorio_na_data": "Não há relatório do OctaPrice para o dia pedido.",
}

# Mensagens de 422: SO' constantes do servidor. O valor recebido nunca volta.
MSG_CANAL_FORA = "Canal fora do escopo dos KPIs desta aba."
MSG_MARCA_INVALIDA = "Marca inválida: use o código de duas letras da fonte."
MSG_SEVERIDADE_INVALIDA = "Severidade inválida: use grave, media ou leve."
MSG_UF_INVALIDA = "UF inválida."
MSG_SKU_INVALIDO = "SKU inválido ou lista de SKUs longa demais."


class FiltroInvalido(Exception):
    def __init__(self, mensagem_segura: str):
        super().__init__(mensagem_segura)
        self.mensagem_segura = mensagem_segura


class B2BIndisponivel(Exception):
    """Sem dado para responder. Carrega o frescor quando ele foi lido."""

    def __init__(self, motivo: str, frescor: Optional[Frescor] = None):
        super().__init__(motivo)
        self.motivo = motivo
        self.frescor = frescor

    @property
    def mensagem(self) -> str:
        return MENSAGENS.get(self.motivo, MENSAGENS["falha_desconhecida"])


@dataclass(frozen=True)
class Recorte:
    """Filtros ja' validados e normalizados (ordenados, sem repeticao)."""

    data: Optional[date]
    marcas: tuple[str, ...] = ()
    canais: tuple[str, ...] = ()
    severidades: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()
    ufs: tuple[str, ...] = ()


def _normaliza(valores: Optional[Iterable[str]]) -> tuple[str, ...]:
    if not valores:
        return ()
    return tuple(sorted({v.strip() for v in valores if v and v.strip()}))


def validar_recorte(
    *,
    data: Optional[date],
    marcas: Optional[Iterable[str]],
    canais: Optional[Iterable[str]],
    severidades: Optional[Iterable[str]],
    skus: Optional[Iterable[str]],
    ufs: Optional[Iterable[str]],
    canais_kpi: list[str],
) -> Recorte:
    m = tuple(sorted({x.upper() for x in _normaliza(marcas)}))
    if any(not _RE_MARCA.match(x) for x in m):
        raise FiltroInvalido(MSG_MARCA_INVALIDA)
    c = _normaliza(canais)
    if any(x not in canais_kpi for x in c):
        raise FiltroInvalido(MSG_CANAL_FORA)
    s = tuple(sorted({x.lower() for x in _normaliza(severidades)}))
    if any(x not in SEVERIDADES for x in s):
        raise FiltroInvalido(MSG_SEVERIDADE_INVALIDA)
    u = tuple(sorted({x.upper() for x in _normaliza(ufs)}))
    if any(x not in UFS for x in u):
        raise FiltroInvalido(MSG_UF_INVALIDA)
    k = _normaliza(skus)
    if len(k) > MAX_SKUS_FILTRO or any(
        len(x) > MAX_TAM_SKU or not _RE_SKU.match(x) for x in k
    ):
        raise FiltroInvalido(MSG_SKU_INVALIDO)
    return Recorte(data=data, marcas=m, canais=c, severidades=s, skus=k, ufs=u)


# ---------------------------------------------------------------------------
# Frescor e escopo
# ---------------------------------------------------------------------------
def data_ref_esperada(agora: datetime) -> date:
    """Ultimo dia de relatorio que ja' deveria existir. Antes do corte, o de
    ontem ainda vale (o do dia pode estar em importacao)."""
    local = agora.astimezone(APP_TIMEZONE)
    hoje = local.date()
    return hoje if local.time() >= HORA_CORTE_BRT else hoje - timedelta(days=1)


def montar_frescor(
    carga: CargaFonte, datas: list[date], *, agora: datetime, consultado_em: datetime
) -> Frescor:
    assert carga.data_ref is not None  # garantido por `_contexto`
    esperada = data_ref_esperada(agora)
    atraso = max(0, (esperada - carga.data_ref).days)
    return Frescor(
        data_ref_mais_recente=carga.data_ref,
        coleta_inicio=carga.coleta_inicio,
        coleta_fim=carga.coleta_fim,
        importado_inicio=carga.importado_inicio,
        importado_fim=carga.importado_fim,
        linhas_na_carga=carga.linhas,
        situacao="em_dia" if atraso == 0 else "atrasado",
        data_ref_esperada=esperada,
        dias_de_atraso=atraso,
        consultado_em=consultado_em,
        datas_disponiveis=sorted(datas, reverse=True),
    )


def montar_escopo(canais_da_carga: Iterable[str], canais_kpi: list[str]) -> Escopo:
    excluidos = [
        CanalExcluido(
            canal=c,
            motivo=MOTIVO_AMAZON if c.strip().lower() == "amazon" else MOTIVO_FORA_ALLOWLIST,
        )
        for c in sorted(set(canais_da_carga))
        if c not in canais_kpi
    ]
    return Escopo(aviso_piso=AVISO_PISO, canais_nos_kpis=list(canais_kpi), canais_excluidos=excluidos)


@dataclass
class _Contexto:
    data_ref: date
    frescor: Frescor
    escopo: Escopo
    filtros_fonte: FiltrosFonte
    filtros: FiltrosAplicados
    consultado_em: datetime


def _ler(chamada):
    """Executa uma leitura da fonte traduzindo a falha para `B2BIndisponivel`."""
    try:
        return chamada()
    except FonteIndisponivel as exc:
        raise B2BIndisponivel(exc.motivo) from None


def _contexto(fonte: FonteComCache, recorte: Recorte, *, agora: datetime, canais_kpi: list[str]) -> _Contexto:
    if not canais_kpi:
        # Allowlist vazia mandaria `p_canais = NULL` — e a Amazon voltaria aos
        # KPIs em silencio. Melhor nao responder.
        raise B2BIndisponivel("configuracao_invalida")
    carga, lido_carga = _ler(fonte.carga_mais_recente)
    if carga.data_ref is None:
        raise B2BIndisponivel("fonte_sem_dados")
    datas, lido_datas = _ler(fonte.datas)
    consultado = min(lido_carga, lido_datas)
    frescor = montar_frescor(carga, datas, agora=agora, consultado_em=consultado)
    data_ref = recorte.data or carga.data_ref
    if data_ref not in set(datas) and data_ref != carga.data_ref:
        # Dia sem relatorio: as RPCs devolveriam ZERO para ele. E' exatamente
        # o "zero que significa nao sei" que este contrato proibe.
        raise B2BIndisponivel("sem_relatorio_na_data", frescor=frescor)
    escopo = montar_escopo(carga.canais, canais_kpi)
    # Sem canal pedido = TODOS os da allowlist, nunca NULL.
    canais = recorte.canais or tuple(sorted(canais_kpi))
    filtros_fonte = FiltrosFonte(
        data=data_ref, marcas=recorte.marcas, canais=canais,
        severidades=recorte.severidades, skus=recorte.skus, ufs=recorte.ufs,
    )
    filtros = FiltrosAplicados(
        data_ref=data_ref, marcas=list(recorte.marcas), canais=list(canais),
        severidades=list(recorte.severidades), skus=list(recorte.skus), ufs=list(recorte.ufs),
    )
    return _Contexto(data_ref, frescor, escopo, filtros_fonte, filtros, consultado)


def _pct(parte: int, todo: int) -> Optional[Decimal]:
    if todo <= 0:
        return None
    return (Decimal(parte) * 100 / Decimal(todo)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _marca(marca_id: Optional[str]) -> str:
    if marca_id is None or not marca_id.strip():
        return ROTULO_SEM_MARCA
    return MARCAS.get(marca_id.strip(), marca_id.strip())


def _base(ctx: _Contexto, consultado_em: datetime) -> dict:
    frescor = ctx.frescor.model_copy(update={"consultado_em": min(ctx.consultado_em, consultado_em)})
    return dict(
        data_ref=ctx.data_ref,
        consulta_historica=ctx.data_ref != ctx.frescor.data_ref_mais_recente,
        frescor=frescor,
        escopo=ctx.escopo,
        filtros=ctx.filtros,
        limitacoes=list(LIMITACOES),
    )


# ---------------------------------------------------------------------------
# Casos de uso
# ---------------------------------------------------------------------------
def get_frescor(fonte: FonteComCache, *, agora: datetime, canais_kpi: list[str]) -> FrescorResponse:
    ctx = _contexto(fonte, Recorte(data=None), agora=agora, canais_kpi=canais_kpi)
    return FrescorResponse(frescor=ctx.frescor, escopo=ctx.escopo, limitacoes=list(LIMITACOES))


def get_resumo(fonte: FonteComCache, recorte: Recorte, *, agora: datetime, canais_kpi: list[str]) -> ResumoResponse:
    ctx = _contexto(fonte, recorte, agora=agora, canais_kpi=canais_kpi)
    f = ctx.filtros_fonte
    resumo, t1 = _ler(lambda: fonte.resumo(f))
    sev, t2 = _ler(lambda: fonte.severidade(f))
    cob, t3 = _ler(lambda: fonte.cobertura(f))
    kpis = Kpis(
        anuncios=resumo.total,
        anuncios_violando=resumo.violando,
        pct_violacao=resumo.pct,
        sellers_violando=resumo.sellers_violando,
        skus_monitorados=resumo.produtos,
        desvio_medio_pma=resumo.desvio_medio,
        buy_box=BuyBox(anuncios_com_buy_box=sev.com_buybox, violando_com_buy_box=sev.viol_com_buybox),
        severidade=Severidade(leve=sev.leve, media=sev.media, grave=sev.grave),
    )
    cobertura = CoberturaIdentificacao(
        sellers_violando=cob.sellers_violando,
        sellers_violando_identificados=cob.sellers_violando_identificados,
        pct_sellers=_pct(cob.sellers_violando_identificados, cob.sellers_violando),
        anuncios_violando=cob.anuncios_violando,
        anuncios_violando_identificados=cob.anuncios_violando_identificados,
        pct_anuncios=_pct(cob.anuncios_violando_identificados, cob.anuncios_violando),
        # Mesma populacao do KPI por construcao; divergencia = fonte mudou ou
        # o teto de `p_limit` foi atingido.
        consistente=(
            cob.sellers_violando == resumo.sellers_violando
            and cob.anuncios_violando == resumo.violando
            and cob.sellers_violando < LIMITE_SELLERS_COBERTURA
        ),
    )
    return ResumoResponse(**_base(ctx, min(t1, t2, t3)), kpis=kpis, cobertura_identificacao=cobertura)


def get_canais_marcas(fonte: FonteComCache, recorte: Recorte, *, agora: datetime, canais_kpi: list[str]) -> CanaisMarcasResponse:
    ctx = _contexto(fonte, recorte, agora=agora, canais_kpi=canais_kpi)
    linhas, t = _ler(lambda: fonte.marca_canal(ctx.filtros_fonte))
    saida = [
        LinhaMarcaCanal(
            marca_id=r.marca_id, marca=_marca(r.marca_id), canal=r.canal,
            anuncios=r.anuncios, anuncios_violando=r.violando,
            pct_violacao=_pct(r.violando, r.anuncios),
            desvio_medio_pma=r.desvio_medio, preco_medio=r.preco_medio,
        )
        for r in linhas
    ]
    return CanaisMarcasResponse(**_base(ctx, t), linhas=saida)


def get_skus(fonte: FonteComCache, recorte: Recorte, *, limite: int, agora: datetime, canais_kpi: list[str]) -> SkusResponse:
    ctx = _contexto(fonte, recorte, agora=agora, canais_kpi=canais_kpi)
    linhas, t = _ler(lambda: fonte.top_skus(ctx.filtros_fonte, limite))
    saida = [
        LinhaSku(
            sku=r.sku, marca_id=r.marca_id, marca=_marca(r.marca_id), exemplo_anuncio=r.exemplo,
            anuncios=r.anuncios, anuncios_violando=r.violando,
            # Mesmo arredondamento do recorte marca x canal (1 casa). A RPC
            # arredonda a 0 casas; as duas tabelas da aba nao podem divergir.
            pct_violacao=_pct(r.violando, r.anuncios), desvio_medio_pma=r.desvio_medio,
        )
        for r in linhas
    ]
    return SkusResponse(**_base(ctx, t), linhas=saida, limite=limite)
