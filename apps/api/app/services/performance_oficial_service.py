"""CX-MKT-2 — secao "Performance" da Expedicao: a nota OFICIAL de cada conta.

Contrato: `docs/cx_atendimento_contrato.md` §7.2 e
`docs/performance_contas_contrato.md` §5 (O1-O8 do ML; AccountHealth Shopee).

Le a fotografia diaria MAIS RECENTE de cada conta no Data Mart (o Airflow
grava; ver o schema). Somente leitura, parametros nomeados.

REGRAS
------
* Tabela ainda nao criada (DAG nao publicada) = `sem_fonte` com motivo, nunca
  erro e nunca zero. Checado com `to_regclass` antes de consultar.
* Conta sem fotografia = lista `sem_foto`, com motivo.
* Fotografia velha (> `LIMITE_DIAS_FOTO`) ganha aviso.
* Meta e' a oficial: ML = limites MercadoLider MLB documentados (o que o
  Seller Center mostra como "permitido"); Shopee = `target` da propria
  resposta, por loja. Metrica sem meta sai `sem_meta`, sem cor.
* Excecao numa consulta vira `erro` com motivo FIXO; o texto nunca sai.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import text

from app.schemas.account_performance import Cobertura, MarcaFora
from app.schemas.performance_oficial import (
    ContaOficial,
    ContaSemFoto,
    Destaque,
    MetricaOficial,
    PerformanceOficialResponse,
)
from app.services.gold_service import BRANDS_IN_SCOPE, ML_BRANDS, TZ_OPERACIONAL

logger = logging.getLogger(__name__)

Consultar = Callable[[str, dict], list[dict]]

MARCAS_PERMITIDAS: tuple[str, ...] = tuple(BRANDS_IN_SCOPE)
SHOPEE_MARCAS: tuple[str, ...] = ("apice", "barbours", "lescent", "rituaria")
CANAIS: tuple[str, ...] = ("mercadolivre", "shopee", "tiktokshop")

LIMITE_DIAS_FOTO = 2
MOTIVO_ERRO = "Fonte indisponível nesta carga. Tente novamente em alguns minutos."

TABELA_ML = "raw.ml_seller_reputation_snapshot"
TABELA_SHOPEE = "raw.shopee_account_health_snapshot"
TABELA_SHOPEE_METRICAS = "raw.shopee_account_health_metric"

FONTE_ML = "Mercado Livre · reputação do vendedor (API /users), fotografia diária"
FONTE_SHOPEE = "Shopee · Saúde da Conta (API account_health), fotografia diária"
FONTE_TIKTOK = "TikTok Shop · Shop Performance Score"

# ---------------------------------------------------------------------------
# Mercado Livre
# ---------------------------------------------------------------------------

#: Cor do `level_id` -> (rotulo, tom). Formato `<n>_<cor>` (doc [ML-REP]).
CORES_ML: dict[str, tuple[str, str]] = {
    "green": ("Verde", "bom"),
    "light_green": ("Verde-claro", "bom"),
    "yellow": ("Amarelo", "atencao"),
    "orange": ("Laranja", "critico"),
    "red": ("Vermelho", "critico"),
}
LIDER_ML = {"platinum": "Platinum", "gold": "Gold", "silver": "Silver"}

#: Limite MercadoLider MLB (doc [ML-REP] de 11/08/2025; o Seller Center mostra
#: o mesmo valor como "permitido"). Em %; a API devolve fracao.
LIMITES_LIDER_ML = {
    "claims": 1.0,
    "mediations": 0.5,  # visto no Seller Center (03/11/2026); fora da doc da API
    "cancellations": 0.5,
    "delayed_handling_time": 6.0,
}

METRICAS_ML: tuple[tuple[str, str, str], ...] = (
    ("claims", "Reclamações",
     "Vendas com reclamação do comprador ÷ vendas da janela, como o ML calcula para a reputação."),
    ("mediations", "Mediações",
     "Reclamações que foram para mediação do ML."),
    ("cancellations", "Canceladas por você",
     "Vendas canceladas pelo vendedor ÷ vendas da janela."),
    ("delayed_handling_time", "Envios com atraso",
     "Vendas despachadas com atraso ÷ vendas enviadas pelo Mercado Envios. "
     "No Seller Center pode aparecer como \"Envios incorretos\"."),
)

SQL_EXISTE = "SELECT to_regclass(:tabela) IS NOT NULL AS existe"

SQL_ML = f"""
SELECT DISTINCT ON (brand)
       brand, captured_date, captured_at, level_id, power_seller_status, real_level,
       protection_end_date, sales_period, sales_completed, seller_reputation
  FROM {TABELA_ML}
 WHERE brand = ANY(:marcas)
 ORDER BY brand, captured_date DESC
"""

SQL_SHOPEE = f"""
SELECT DISTINCT ON (brand)
       brand, captured_date, captured_at, overall_rating, fulfillment_failed,
       listing_failed, custom_service_failed, penalty_points, ongoing_punishments
  FROM {TABELA_SHOPEE}
 WHERE brand = ANY(:marcas)
 ORDER BY brand, captured_date DESC
"""

SQL_SHOPEE_METRICAS = f"""
SELECT brand, captured_date, metric_id, metric_name, metric_type, parent_metric_id,
       unit, current_period, last_period, target_value, target_comparator
  FROM {TABELA_SHOPEE_METRICAS}
 WHERE brand = ANY(:marcas)
   AND (brand, captured_date) IN (SELECT brand, max(captured_date)
                                    FROM {TABELA_SHOPEE_METRICAS}
                                   WHERE brand = ANY(:marcas)
                                   GROUP BY brand)
"""

# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------

NOTAS_SHOPEE: dict[int, tuple[str, str]] = {
    1: ("Ruim", "critico"),
    2: ("Precisa melhorar", "atencao"),
    3: ("Bom", "bom"),
    4: ("Excelente", "bom"),
}
GRUPOS_SHOPEE = {1: "Envio", 2: "Anúncios", 3: "Atendimento"}
#: `unit` da doc: 1 Number, 2 Percentage, 3 Second, 4 Day, 5 Hour.
UNIDADES_SHOPEE = {1: "contagem", 2: "pct", 3: "segundos", 4: "dias", 5: "horas"}
#: Rotulos em portugues dos `metric_id` principais (doc AccountHealth).
#: Fora daqui sai o `metric_name` da propria Shopee.
NOMES_SHOPEE: dict[int, str] = {
    1: "Taxa de envio atrasado",
    3: "Taxa de não cumprimento",
    4: "Tempo de preparação",
    11: "Taxa de resposta do chat",
    12: "Anúncios em pré-venda",
    21: "Tempo de resposta",
    22: "Nota da loja",
    23: "Chats sem resposta",
    25: "Entrega rápida à transportadora",
    29: "Tempo médio de resposta",
    42: "Taxa de cancelamento",
    43: "Taxa de devolução/reembolso",
    52: "Violações graves de anúncio",
    53: "Outras violações de anúncio",
    95: "Satisfação do cliente",
}


def consultar_datamart(sql: str, params: dict) -> list[dict]:
    from app.database import datamart_engine  # import tardio: testes injetam `consultar`

    if datamart_engine is None:
        raise RuntimeError("datamart indisponivel")
    with datamart_engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def _int(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _utc(v: Any) -> Optional[datetime]:
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _cobertura(marcas: list[str], do_canal: tuple[str, ...], motivo: str) -> Cobertura:
    return Cobertura(
        marcas=[m for m in marcas if m in do_canal],
        fora=[MarcaFora(marca=m, motivo=motivo) for m in marcas if m not in do_canal],
    )


def _situacao(valor: Optional[float], meta: Optional[float], comparador: Optional[str]) -> str:
    """'ok' cumpre a meta, 'fora' nao cumpre, 'sem_meta' sem meta ou sem valor."""
    if valor is None or meta is None or comparador not in ("<", "<=", ">", ">=", "="):
        return "sem_meta"
    ok = {
        "<": valor < meta, "<=": valor <= meta, ">": valor > meta,
        ">=": valor >= meta, "=": valor == meta,
    }[comparador]
    return "ok" if ok else "fora"


def _aviso_foto(dia: Optional[date], hoje: date) -> Optional[str]:
    if dia is None:
        return None
    atraso = (hoje - dia).days
    if atraso > LIMITE_DIAS_FOTO:
        return f"Fotografia de {dia.strftime('%d/%m')}: a captura diária está atrasada há {atraso} dias."
    return None


def _tabela_existe(consultar: Consultar, tabela: str) -> bool:
    linhas = consultar(SQL_EXISTE, {"tabela": tabela})
    return bool(linhas and linhas[0].get("existe"))


def _rodar(consultar: Consultar, nome: str, sql: str, params: dict) -> Optional[list[dict]]:
    try:
        return consultar(sql, params)
    except Exception as exc:  # noqa: BLE001 — isolamento e motivo fixo sao o contrato
        logger.warning("performance_oficial: consulta %s falhou (%s)", nome, type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# Montagem por canal
# ---------------------------------------------------------------------------
def conta_ml(linha: dict, hoje: date) -> ContaOficial:
    rep = linha.get("seller_reputation") if isinstance(linha.get("seller_reputation"), dict) else {}
    metricas_brutas = rep.get("metrics") if isinstance(rep.get("metrics"), dict) else {}

    level = str(linha.get("level_id") or "")
    n, _, cor = level.partition("_")
    rotulo_cor, tom = CORES_ML.get(cor, (None, "neutro"))
    lider = LIDER_ML.get(str(linha.get("power_seller_status") or "").lower())
    nivel = f"MercadoLíder {lider}" if lider else (rotulo_cor or "Sem reputação calculada")

    metricas: list[MetricaOficial] = []
    for chave, rotulo, ajuda in METRICAS_ML:
        bruto = metricas_brutas.get(chave)
        if not isinstance(bruto, dict):
            continue  # a plataforma nao devolveu: nao aparece (nunca zero)
        taxa = _num(bruto.get("rate"))
        valor = round(taxa * 100, 2) if taxa is not None else None
        meta = LIMITES_LIDER_ML.get(chave)
        metricas.append(MetricaOficial(
            codigo=f"ml_{chave}", rotulo=rotulo, valor=valor, unidade="pct",
            quantidade=_int(bruto.get("value")), meta=meta, comparador="<=",
            rotulo_meta=(f"Até {str(meta).replace('.', ',')}% para MercadoLíder" if meta is not None else None),
            situacao=_situacao(valor, meta, "<="), ajuda=ajuda,
        ))

    vendas = _int(linha.get("sales_completed"))
    periodo = str(linha.get("sales_period") or "").replace("days", "dias").strip()
    avisos = []
    if linha.get("real_level"):
        fim = linha.get("protection_end_date")
        fim_txt = f" até {fim.strftime('%d/%m/%Y')}" if isinstance(fim, datetime) else ""
        avisos.append(f"Conta em proteção{fim_txt}: o ML exibe o nível protegido; o nível real "
                      f"é {CORES_ML.get(str(linha['real_level']), (str(linha['real_level']), ''))[0]}.")
    dia = linha.get("captured_date")
    aviso = _aviso_foto(dia if isinstance(dia, date) else None, hoje)
    if aviso:
        avisos.append(aviso)

    return ContaOficial(
        marca=str(linha["brand"]), dia=dia, capturado_em=_utc(linha.get("captured_at")),
        nivel=nivel, tom=tom, termometro=_int(n), termometro_max=5,
        resumo=(f"{_fmt_int(vendas)} vendas concluídas nos últimos {periodo}" if vendas is not None and periodo
                else None),
        destaques=[Destaque(rotulo="Termômetro", valor=rotulo_cor, tom=tom)] if rotulo_cor else [],
        metricas=metricas, avisos=avisos,
    )


def conta_shopee(linha: dict, metricas_linhas: list[dict], hoje: date) -> ContaOficial:
    nota = _int(linha.get("overall_rating"))
    nivel, tom = NOTAS_SHOPEE.get(nota, (None, "neutro"))
    metricas = []
    # So' metricas reais (id > 0) e de primeiro nivel; sub-metricas ficam de fora nesta fatia.
    for r in sorted(metricas_linhas, key=lambda x: (_int(x.get("metric_type")) or 9, _int(x.get("metric_id")) or 0)):
        mid = _int(r.get("metric_id"))
        if mid is None or mid <= 0 or _int(r.get("parent_metric_id")) not in (None, 0):
            continue
        unidade = UNIDADES_SHOPEE.get(_int(r.get("unit")), "contagem")
        valor = _num(r.get("current_period"))
        meta = _num(r.get("target_value"))
        comp = r.get("target_comparator")
        metricas.append(MetricaOficial(
            codigo=f"shopee_{mid}", rotulo=NOMES_SHOPEE.get(mid, str(r.get("metric_name") or mid)),
            grupo=GRUPOS_SHOPEE.get(_int(r.get("metric_type"))), valor=valor, unidade=unidade,
            anterior=_num(r.get("last_period")), meta=meta, comparador=comp,
            rotulo_meta=(f"Meta {comp} {meta:g}" if meta is not None and comp else None),
            situacao=_situacao(valor, meta, comp),
        ))
    fora = sum(_int(linha.get(c)) or 0 for c in ("fulfillment_failed", "listing_failed", "custom_service_failed"))
    destaques = []
    pontos = _int(linha.get("penalty_points"))
    if pontos is not None:
        destaques.append(Destaque(rotulo="Pontos de penalidade no trimestre", valor=str(pontos),
                                  tom="bom" if pontos == 0 else "atencao"))
    punicoes = _int(linha.get("ongoing_punishments"))
    if punicoes is not None:
        destaques.append(Destaque(rotulo="Punições em andamento", valor=str(punicoes),
                                  tom="bom" if punicoes == 0 else "critico"))
    dia = linha.get("captured_date")
    aviso = _aviso_foto(dia if isinstance(dia, date) else None, hoje)
    return ContaOficial(
        marca=str(linha["brand"]), dia=dia, capturado_em=_utc(linha.get("captured_at")),
        nivel=nivel, tom=tom, termometro=nota, termometro_max=4,
        resumo=("Todas as métricas dentro da meta" if fora == 0 else f"{fora} métrica(s) fora da meta"),
        destaques=destaques, metricas=metricas, avisos=[aviso] if aviso else [],
    )


def _resposta_base(canal: str, agora: datetime, fonte: str, cobertura: Cobertura, **kw) -> PerformanceOficialResponse:
    return PerformanceOficialResponse(gerado_em=agora, canal=canal, fonte=fonte, cobertura=cobertura, **kw)


def montar_performance_oficial(*, canal: str, brands: Optional[list[str]] = None,
                               consultar: Optional[Consultar] = None,
                               agora: Optional[datetime] = None) -> PerformanceOficialResponse:
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or consultar_datamart
    hoje = agora.astimezone(TZ_OPERACIONAL).date()
    marcas = [m for m in MARCAS_PERMITIDAS if brands is None or m in brands]

    if canal == "tiktokshop":
        return _resposta_base(
            canal, agora, FONTE_TIKTOK, _cobertura(marcas, MARCAS_PERMITIDAS, ""),
            estado="sem_fonte",
            motivo="A nota oficial da loja no TikTok (Shop Performance Score) só tem API para lojas dos "
                   "EUA pela documentação. O teste com as lojas BR está pendente; se não houver API, "
                   "a nota entra por captura manual semanal.",
        )

    if canal == "mercadolivre":
        cobertura = _cobertura(marcas, tuple(ML_BRANDS), "Sem conta Mercado Livre integrada.")
        tabela, fonte = TABELA_ML, FONTE_ML
    else:
        cobertura = _cobertura(marcas, SHOPEE_MARCAS, "Sem app Shopee (só export manual).")
        tabela, fonte = TABELA_SHOPEE, FONTE_SHOPEE

    if not cobertura.marcas:
        return _resposta_base(canal, agora, fonte, cobertura, estado="dado")

    try:
        existe = _tabela_existe(consultar, tabela)
    except Exception as exc:  # noqa: BLE001
        logger.warning("performance_oficial: checagem de %s falhou (%s)", canal, type(exc).__name__)
        return _resposta_base(canal, agora, fonte, cobertura, estado="erro", motivo=MOTIVO_ERRO)
    if not existe:
        return _resposta_base(
            canal, agora, fonte, cobertura, estado="sem_fonte",
            motivo="Aguardando a primeira captura diária no Airflow (DAG ainda não publicada).",
        )

    params = {"marcas": cobertura.marcas}
    if canal == "mercadolivre":
        linhas = _rodar(consultar, "ml", SQL_ML, params)
        if linhas is None:
            return _resposta_base(canal, agora, fonte, cobertura, estado="erro", motivo=MOTIVO_ERRO)
        contas = [conta_ml(r, hoje) for r in linhas if r.get("brand") in cobertura.marcas]
    else:
        linhas = _rodar(consultar, "shopee", SQL_SHOPEE, params)
        met = _rodar(consultar, "shopee_metricas", SQL_SHOPEE_METRICAS, params)
        if linhas is None:
            return _resposta_base(canal, agora, fonte, cobertura, estado="erro", motivo=MOTIVO_ERRO)
        por_marca: dict[str, list[dict]] = {}
        for r in met or []:
            por_marca.setdefault(str(r.get("brand")), []).append(r)
        contas = []
        for r in linhas:
            if r.get("brand") not in cobertura.marcas:
                continue
            conta = conta_shopee(r, por_marca.get(str(r["brand"]), []), hoje)
            if met is None:
                conta.avisos.append("Métricas detalhadas indisponíveis nesta carga.")
            contas.append(conta)

    ordem = {m: i for i, m in enumerate(cobertura.marcas)}
    contas.sort(key=lambda c: ordem.get(c.marca, 99))
    com_foto = {c.marca for c in contas}
    sem_foto = [ContaSemFoto(marca=m, motivo="Sem fotografia capturada ainda.")
                for m in cobertura.marcas if m not in com_foto]
    return _resposta_base(canal, agora, fonte, cobertura, estado="dado", contas=contas, sem_foto=sem_foto)
