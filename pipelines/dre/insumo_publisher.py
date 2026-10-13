"""Publisher dos insumos mensais da DRE: Data Mart -> Neon (Gate DRE-DATA-BRIDGE-1).

O QUE ELE FAZ
-------------
Le, no Data Mart e em transacao READ ONLY, somente AGREGADOS MENSAIS por
`competencia x canal x marca`:

  * CMV fiscal e a receita fiscal que o acompanha
    (`gold.nf_vendas_plataforma_sku_mensal`);
  * taxa, frete, afiliado, receita e residuo da identidade do repasse TikTok
    por coorte de pedido (`silver.stg_tiktok_payments_by_order`).

e grava em `marts.fact_dre_insumo_mensal` (Neon). Nenhum pedido, nota,
settlement ou dado pessoal sai do Data Mart.

CONTRATO
--------
  * dry-run por padrao; escrever exige `--apply`;
  * origem read-only (`SET TRANSACTION READ ONLY`);
  * uma transacao no Neon, com `pg_try_advisory_xact_lock` tomado ANTES de ler
    a fonte — sem espera, sem retry;
  * reconciliacao ANTES de qualquer escrita; se ela recusar, nada e' escrito;
  * a publicacao SUBSTITUI o escopo `competencias x canais` inteiro: chave que
    sumiu da fonte some da tabela, e chave esperada sem dado na fonte e'
    gravada com `valor NULL` e status `ausente_na_origem` — nunca zero, nunca
    a fotografia antiga;
  * idempotente: a mesma fonte produz o mesmo conteudo;
  * auditoria em `audit.source_sync_run`; o `sync_run_id` e' o `lote_id`;
  * nenhum DSN, host, senha ou IP em log ou mensagem.

O QUE ELE NAO FAZ
-----------------
Nao roda Alembic, nao cria tabela, nao importa `app.*` nem `alembic`. Se a
migration 025 nao estiver aplicada, o `--apply` aborta antes de escrever.

USO
---
    python -m pipelines.dre.insumo_publisher --de 2026-07 --ate 2026-07
    python -m pipelines.dre.insumo_publisher --meses 4 --apply

Codigos de saida: 0 ok; 65 reconciliacao recusada; 69 fonte indisponivel;
75 lock ocupado; 78 migration ausente; 1 erro inesperado.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("pipelines.dre.insumo_publisher")

SOURCE_NAME = "dre_insumo_mensal"
TABELA = "marts.fact_dre_insumo_mensal"
#: Familia propria (9181400xx), distinta de 917120017 (ofertas) e 918130000+mid
#: (fato diaria). 025 = numero da migration que cria a tabela.
LOCK_KEY = 918140025

CANAIS = ("tiktok", "ml", "shopee")
MKT_ID = {"tiktok": 1, "ml": 2, "shopee": 3}
PLATAFORMA_FISCAL = {"tiktok": "tiktokshop", "ml": "mercadolivre", "shopee": "shopee"}

#: Mesma faixa do serving (`app.services.dre_service.CMV_RAZAO_*`). Um teste
#: compara as duas constantes: divergir criaria duas definicoes de reconciliado.
CMV_RAZAO_MIN = Decimal("0.95")
CMV_RAZAO_MAX = Decimal("1.05")
#: Residuo tolerado em `settlement - revenue - fee_and_tax - shipping` por
#: celula. Medido: 0,00 em 758.324 transacoes (contrato §6.1, P1).
RESIDUO_MAX = Decimal("1.00")
#: Divergencia tolerada entre o CMV por SKU e o agregado mensal independente.
CONTRAPROVA_MAX = Decimal("1.00")

PROC_CMV = "gold.nf_vendas_plataforma_sku_mensal (canal=marketplace) @dre-bridge-1"
PROC_REPASSE = "silver.stg_tiktok_payments_by_order (transaction_type=ORDER, coorte) @dre-bridge-1"

EXIT_OK = 0
EXIT_RECONCILIACAO = 65
EXIT_FONTE = 69
EXIT_LOCK = 75
EXIT_MIGRATION = 78
#: Offset fixo, e nao `ZoneInfo`: o pacote `pipelines` nao declara `tzdata`, e
#: no Windows `ZoneInfo("America/Sao_Paulo")` falha na importacao. O Brasil
#: nao tem horario de verao desde 2019, entao -03:00 e' exato.
TZ_BRT = timezone(timedelta(hours=-3), "BRT")


class PublicacaoRecusada(RuntimeError):
    """Reconciliacao reprovou: nada foi escrito."""

    def __init__(self, motivos: list[str]):
        super().__init__("; ".join(motivos[:20]))
        self.motivos = motivos


class LockOcupado(RuntimeError):
    pass


class MigrationAusente(RuntimeError):
    pass


class FonteIndisponivel(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Sanitizacao
# ---------------------------------------------------------------------------

_SENSIVEIS = (
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s'\"]*", re.IGNORECASE),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    re.compile(r"password=\S+", re.IGNORECASE),
    re.compile(r"\bhost=\S+", re.IGNORECASE),
    re.compile(r"\buser=\S+", re.IGNORECASE),
)


def sanitizar(exc: BaseException | str) -> str:
    texto = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    for p in _SENSIVEIS:
        texto = p.sub("[REDACTED]", texto)
    return " ".join(texto.split())[:500]


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------

def mes(d: date) -> date:
    return date(d.year, d.month, 1)


def somar_meses(m: date, n: int) -> date:
    t = m.year * 12 + (m.month - 1) + n
    return date(t // 12, t % 12 + 1, 1)


def ultimo_dia(m: date) -> date:
    return somar_meses(m, 1) - timedelta(days=1)


def competencias(de: date, ate: date) -> list[date]:
    out, m = [], mes(de)
    while m <= mes(ate):
        out.append(m)
        m = somar_meses(m, 1)
    return out


def janela_padrao(hoje: date, meses: int) -> list[date]:
    """Os `meses` meses FECHADOS mais recentes. O mes corrente nunca entra."""
    ate = somar_meses(mes(hoje), -1)
    return competencias(somar_meses(ate, -(meses - 1)), ate)


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

#: Celulas que a DRE vai pedir: as que tem receita na Torre. E' o conjunto
#: ESPERADO — chave da fonte fora dele nao e' publicada; chave dele sem dado
#: na fonte vira linha `ausente_na_origem`.
SQL_ESPERADO = text("""
    SELECT date_trunc('month', f.date)::date AS competencia,
           f.marketplace_id                  AS marketplace_id,
           l.brand_key                       AS marca,
           SUM(f.gmv)                        AS gmv
    FROM marts.fact_marketplace_daily_performance f
    JOIN marts.dim_loja l ON l.loja_id = f.loja_id
    WHERE f.date >= :inicio AND f.date < :fim_exclusivo
      AND f.marketplace_id = ANY(:mkt_ids)
      AND l.ativo
    GROUP BY 1, 2, 3
""")

SQL_CMV = text("""
    SELECT data_mes AS competencia, marca, plataforma,
           SUM(receita_produto)    AS receita_fiscal,
           SUM(custo)              AS custo,
           SUM(custo_provisorio)   AS custo_provisorio,
           SUM(receita_com_custo)  AS receita_com_custo,
           MAX(ultima_emissao)     AS ultima_emissao,
           COUNT(*)                AS linhas
    FROM gold.nf_vendas_plataforma_sku_mensal
    WHERE data_mes = ANY(:competencias)
      AND canal = 'marketplace'
      AND plataforma = ANY(:plataformas)
      AND marca = ANY(:marcas)
    GROUP BY 1, 2, 3
""")

#: Contraprova: o agregado mensal que o proprio Data Mart materializa SEM SKU.
#: Se a soma por SKU nao bater com ele, uma das duas tabelas esta no meio de
#: uma carga — e publicar seria fotografar o meio da carga.
SQL_CMV_CONTRAPROVA = text("""
    SELECT data_mes AS competencia, marca, plataforma,
           SUM(receita_produto) AS receita_fiscal,
           SUM(custo)           AS custo
    FROM gold.nf_vendas_plataforma_mensal
    WHERE data_mes = ANY(:competencias)
      AND canal = 'marketplace'
      AND plataforma = ANY(:plataformas)
      AND marca = ANY(:marcas)
    GROUP BY 1, 2, 3
""")

SQL_REPASSE = text("""
    SELECT date_trunc('month', order_create_time)::date AS competencia,
           brand AS marca,
           SUM(fee_and_tax_amount)   AS taxa,
           SUM(shipping_cost_amount) AS frete,
           SUM(revenue_amount)       AS receita,
           SUM(COALESCE(affiliate_commission_amount, 0)
               + COALESCE(affiliate_partner_commission_amount, 0)
               + COALESCE(affiliate_ads_commission_amount, 0)) AS afiliado,
           SUM(settlement_amount - revenue_amount - fee_and_tax_amount - shipping_cost_amount) AS residuo,
           -- So' transacoes COM afiliado: sem ele a comparacao nao prova nada, e
           -- transacao com taxa POSITIVA (estorno de tarifa, 1.536 em jul/2026 no
           -- grupo sem afiliado) cairia aqui como falso positivo.
           COUNT(*) FILTER (
               WHERE COALESCE(affiliate_commission_amount, 0)
                   + COALESCE(affiliate_partner_commission_amount, 0)
                   + COALESCE(affiliate_ads_commission_amount, 0) < 0
                 AND COALESCE(affiliate_commission_amount, 0)
                   + COALESCE(affiliate_partner_commission_amount, 0)
                   + COALESCE(affiliate_ads_commission_amount, 0) < fee_and_tax_amount - 0.01
           ) AS tx_afiliado_maior_taxa,
           COUNT(*)        AS transacoes,
           MAX(updated_at) AS atualizado_em
    FROM silver.stg_tiktok_payments_by_order
    WHERE transaction_type = 'ORDER'
      AND order_create_time >= :inicio
      AND order_create_time < :fim_exclusivo
      AND brand = ANY(:marcas)
    GROUP BY 1, 2
""")

SQL_TABELA_EXISTE = text("SELECT to_regclass('marts.fact_dre_insumo_mensal') IS NOT NULL")
SQL_LOCK = text(f"SELECT pg_try_advisory_xact_lock({LOCK_KEY})")
SQL_AUDIT_ABRE = text("""
    INSERT INTO audit.source_sync_run (source_name, status, started_at, source_min_date, source_max_date)
    VALUES (:fonte, 'running', :agora, :min_date, :max_date)
    RETURNING sync_run_id
""")
SQL_AUDIT_FECHA = text("""
    UPDATE audit.source_sync_run
       SET status = 'success', finished_at = :agora, rows_extracted = :extraidas, rows_loaded = :gravadas
     WHERE sync_run_id = :lote
""")
SQL_AUDIT_FALHA = text("""
    INSERT INTO audit.source_sync_run
        (source_name, status, started_at, finished_at, error_message, source_min_date, source_max_date)
    VALUES (:fonte, 'failed', :agora, :agora, :erro, :min_date, :max_date)
""")
SQL_APAGA_ESCOPO = text(f"""
    DELETE FROM {TABELA} WHERE competencia = ANY(:competencias) AND canal = ANY(:canais)
""")
SQL_INSERE = text(f"""
    INSERT INTO {TABELA} (competencia, canal, marca, componente, valor, sinal_origem, cobertura,
        provisorio_share, status_reconciliacao, motivo, data_max_origem, publicado_em, lote_id, procedencia)
    VALUES (:competencia, :canal, :marca, :componente, :valor, :sinal_origem, :cobertura,
        :provisorio_share, :status_reconciliacao, :motivo, :data_max_origem, :publicado_em, :lote_id, :procedencia)
""")
SQL_ATUAL = text(f"""
    SELECT competencia, canal, marca, componente, valor, status_reconciliacao
    FROM {TABELA} WHERE competencia = ANY(:competencias) AND canal = ANY(:canais)
""")


# ---------------------------------------------------------------------------
# Montagem do lote — PURA
# ---------------------------------------------------------------------------

def _dec(v) -> Optional[Decimal]:
    return None if v is None else Decimal(str(v))


def _mes_de(v) -> date:
    if isinstance(v, datetime):
        v = v.date()
    return date(v.year, v.month, 1)


def _ts(v) -> Optional[datetime]:
    """Data maxima da origem como instante.

    `date` (emissao fiscal) vira meia-noite BRT. `datetime` sem fuso
    (`updated_at` da silver) e' tratado como UTC — INFERENCIA: a carga da
    silver grava o relogio do worker; nao ha coluna de fuso para provar.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime(v.year, v.month, v.day, tzinfo=TZ_BRT)


@dataclass
class Lote:
    linhas: list[dict] = field(default_factory=list)
    recusas: list[str] = field(default_factory=list)
    fora_do_escopo: list[tuple] = field(default_factory=list)
    extraidas: int = 0


def _linha(competencia, canal, marca, componente, valor, *, status, procedencia, sinal=None,
           cobertura=None, provisorio=None, motivo=None, data_max=None) -> dict:
    if valor is None:
        status = "ausente_na_origem"
    return {
        "competencia": competencia, "canal": canal, "marca": marca, "componente": componente,
        "valor": valor, "sinal_origem": sinal, "cobertura": cobertura, "provisorio_share": provisorio,
        "status_reconciliacao": status, "motivo": motivo, "data_max_origem": data_max,
        "procedencia": procedencia,
    }


def _razao(num: Optional[Decimal], den: Optional[Decimal]) -> Optional[Decimal]:
    if num is None or den is None or den == 0:
        return None
    return num / den


def montar_lote(*, esperado: list[dict], cmv: list[dict], contraprova: list[dict],
                repasse: list[dict], canais: list[str]) -> Lote:
    """Transforma a leitura em linhas da fato e lista as recusas.

    Recusa (a publicacao inteira aborta) quando:
      * a fonte tem chave repetida;
      * CMV por SKU diverge do agregado mensal independente;
      * CMV negativo, cobertura ou provisorio fora de [0, 1];
      * a identidade do repasse TikTok nao fecha numa celula;
      * alguma transacao tem afiliado maior que a propria taxa.

    NAO recusa — e grava com status — o CMV cuja receita fiscal nao bate com o
    GMV da Torre: isso e' estado do dado (Shopee hoje), nao defeito de carga.
    """
    lote = Lote(extraidas=len(cmv) + len(repasse))
    plat_para_canal = {v: k for k, v in PLATAFORMA_FISCAL.items()}

    def indexar(rows, chave_fn, nome):
        idx = {}
        for r in rows:
            k = chave_fn(r)
            if k in idx:
                lote.recusas.append(f"{nome}: chave repetida {k}")
            idx[k] = r
        return idx

    cmv_idx = indexar(cmv, lambda r: (_mes_de(r["competencia"]), plat_para_canal.get(r["plataforma"]), r["marca"]), "cmv")
    cp_idx = indexar(contraprova, lambda r: (_mes_de(r["competencia"]), plat_para_canal.get(r["plataforma"]), r["marca"]),
                     "contraprova")
    rep_idx = indexar(repasse, lambda r: (_mes_de(r["competencia"]), r["marca"]), "repasse")
    esp = {}
    for r in esperado:
        canal = {v: k for k, v in MKT_ID.items()}.get(r["marketplace_id"])
        if canal in canais:
            esp[(_mes_de(r["competencia"]), canal, r["marca"])] = _dec(r["gmv"])

    # Contraprova do CMV — mesma chave, mesmo total, nos dois sentidos.
    for k in set(cmv_idx) | set(cp_idx):
        a, b = cmv_idx.get(k), cp_idx.get(k)
        if a is None or b is None:
            lote.recusas.append(f"cmv: chave {k} presente so' em uma das duas tabelas fiscais")
            continue
        for col in ("receita_fiscal", "custo"):
            da, db_ = _dec(a[col]) or Decimal(0), _dec(b[col]) or Decimal(0)
            if abs(da - db_) > CONTRAPROVA_MAX:
                lote.recusas.append(f"cmv: {col} {k} diverge do agregado mensal em {da - db_}")

    for k in set(cmv_idx) - set(esp):
        lote.fora_do_escopo.append(("cmv",) + k)
    for (m, marca) in set(rep_idx):
        if "tiktok" in canais and (m, "tiktok", marca) not in esp:
            lote.fora_do_escopo.append(("repasse", m, "tiktok", marca))

    for (m, canal, marca), gmv in sorted(esp.items(), key=lambda kv: (kv[0][0], CANAIS.index(kv[0][1]), kv[0][2])):
        c = cmv_idx.get((m, canal, marca))
        if c is None:
            for comp in ("cmv", "cmv_receita_fiscal"):
                lote.linhas.append(_linha(m, canal, marca, comp, None, status="ausente_na_origem",
                                          procedencia=PROC_CMV, motivo="sem NF de venda para a chave na origem"))
        else:
            custo, receita_fiscal = _dec(c["custo"]), _dec(c["receita_fiscal"])
            prov = _dec(c["custo_provisorio"]) or Decimal(0)
            share = _razao(prov, custo) if custo else None
            cobertura = _razao(_dec(c["receita_com_custo"]), receita_fiscal)
            if custo is not None and custo < 0:
                lote.recusas.append(f"cmv negativo em {(m, canal, marca)}")
            for nome, v in (("provisorio", share), ("cobertura", cobertura)):
                if v is not None and not (Decimal(0) <= v <= Decimal(1)):
                    lote.recusas.append(f"cmv: {nome} fora de [0,1] em {(m, canal, marca)}: {v}")
            razao = _razao(receita_fiscal, gmv)
            if razao is not None and CMV_RAZAO_MIN <= razao <= CMV_RAZAO_MAX:
                status, motivo = "reconciliado", f"receita fiscal / GMV = {razao:.4f}"
            else:
                status = "nao_reconciliado"
                motivo = ("sem GMV para comparar" if razao is None
                          else f"receita fiscal / GMV = {razao:.4f}; aceito entre {CMV_RAZAO_MIN} e {CMV_RAZAO_MAX}")
            data_max = _ts(c["ultima_emissao"])
            lote.linhas.append(_linha(m, canal, marca, "cmv", custo, status=status, procedencia=PROC_CMV, sinal=1,
                                      cobertura=cobertura, provisorio=share, motivo=motivo, data_max=data_max))
            lote.linhas.append(_linha(m, canal, marca, "cmv_receita_fiscal", receita_fiscal, status="nao_avaliado",
                                      procedencia=PROC_CMV, motivo="base da razao com o GMV", data_max=data_max))

        if canal != "tiktok":
            continue
        r = rep_idx.get((m, marca))
        comps = ("tiktok_taxa_repasse", "tiktok_frete_repasse", "tiktok_afiliado_repasse", "tiktok_receita_repasse",
                 "tiktok_residuo_identidade", "tiktok_tx_afiliado_maior_taxa", "tiktok_transacoes")
        if r is None:
            for comp in comps:
                lote.linhas.append(_linha(m, canal, marca, comp, None, status="ausente_na_origem",
                                          procedencia=PROC_REPASSE, motivo="sem transacao de repasse na coorte"))
            continue
        residuo = _dec(r["residuo"]) or Decimal(0)
        violacoes = int(r["tx_afiliado_maior_taxa"] or 0)
        if abs(residuo) > RESIDUO_MAX:
            lote.recusas.append(f"repasse: identidade nao fecha em {(m, marca)} (residuo {residuo})")
        if violacoes:
            lote.recusas.append(f"repasse: {violacoes} transacao(oes) com afiliado maior que a taxa em {(m, marca)}")
        taxa, afiliado = _dec(r["taxa"]), _dec(r["afiliado"])
        if taxa is not None and taxa > 0:
            lote.recusas.append(f"repasse: taxa positiva em {(m, marca)}")
        if taxa is not None and afiliado is not None and afiliado < taxa - Decimal("0.01"):
            lote.recusas.append(f"repasse: afiliado maior que a taxa no agregado {(m, marca)}")
        data_max = _ts(r["atualizado_em"])
        motivo = "identidade do repasse fecha; afiliado contido na taxa"
        valores = {
            "tiktok_taxa_repasse": (taxa, -1), "tiktok_frete_repasse": (_dec(r["frete"]), -1),
            "tiktok_afiliado_repasse": (afiliado, -1), "tiktok_receita_repasse": (_dec(r["receita"]), None),
            "tiktok_residuo_identidade": (residuo, None),
            "tiktok_tx_afiliado_maior_taxa": (Decimal(violacoes), None),
            "tiktok_transacoes": (Decimal(int(r["transacoes"] or 0)), None),
        }
        for comp in comps:
            v, sinal = valores[comp]
            lote.linhas.append(_linha(m, canal, marca, comp, v, status="reconciliado", procedencia=PROC_REPASSE,
                                      sinal=sinal, motivo=motivo, data_max=data_max))
    return lote


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def _rows(conn, sql, params) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).mappings().all()]


def ler_fonte(fonte_engine, comps: list[date], canais: list[str], marcas: list[str]) -> tuple[list, list, list]:
    """Leitura do Data Mart em transacao READ ONLY. Falha vira FonteIndisponivel."""
    if fonte_engine is None:
        raise FonteIndisponivel("Data Mart nao configurado")
    params = {
        "competencias": comps, "plataformas": [PLATAFORMA_FISCAL[c] for c in canais], "marcas": marcas,
        "inicio": comps[0], "fim_exclusivo": somar_meses(comps[-1], 1),
    }
    try:
        with fonte_engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '600s'"))
            cmv = _rows(conn, SQL_CMV, params)
            contraprova = _rows(conn, SQL_CMV_CONTRAPROVA, params)
            repasse = _rows(conn, SQL_REPASSE, params) if "tiktok" in canais else []
            conn.rollback()
    except SQLAlchemyError as exc:
        raise FonteIndisponivel(sanitizar(exc)) from None
    return cmv, contraprova, repasse


def _esperado(conn, comps: list[date], canais: list[str]) -> list[dict]:
    return _rows(conn, SQL_ESPERADO, {
        "inicio": comps[0], "fim_exclusivo": somar_meses(comps[-1], 1),
        "mkt_ids": [MKT_ID[c] for c in canais],
    })


def _marcas(esperado: list[dict]) -> list[str]:
    return sorted({r["marca"] for r in esperado})


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------

def _resumo(lote: Lote, comps: list[date], canais: list[str], **extra) -> dict:
    por_comp: dict[str, dict] = {}
    for ln in lote.linhas:
        d = por_comp.setdefault(ln["componente"], {"linhas": 0, "ausentes": 0, "soma": Decimal(0),
                                                    "nao_reconciliadas": 0})
        d["linhas"] += 1
        if ln["valor"] is None:
            d["ausentes"] += 1
        else:
            d["soma"] += ln["valor"]
        if ln["status_reconciliacao"] == "nao_reconciliado":
            d["nao_reconciliadas"] += 1
    return {
        "competencias": [c.isoformat()[:7] for c in comps], "canais": canais,
        "linhas": len(lote.linhas), "extraidas": lote.extraidas,
        "recusas": lote.recusas, "fora_do_escopo": len(lote.fora_do_escopo),
        "por_componente": {k: {**v, "soma": str(v["soma"])} for k, v in sorted(por_comp.items())},
        **extra,
    }


def _diff(atual: list[dict], novas: list[dict]) -> dict:
    chave = lambda r: (_mes_de(r["competencia"]), r["canal"], r["marca"], r["componente"])
    a = {chave(r): (_dec(r["valor"]), r["status_reconciliacao"]) for r in atual}
    n = {chave(r): (r["valor"], r["status_reconciliacao"]) for r in novas}
    return {
        "novas": len(set(n) - set(a)), "removidas": len(set(a) - set(n)),
        "alteradas": sum(1 for k in set(a) & set(n) if a[k] != n[k]),
        "iguais": sum(1 for k in set(a) & set(n) if a[k] == n[k]),
    }


def diagnosticar(alvo_engine, fonte_engine, comps: list[date], canais: list[str]) -> dict:
    """Dry-run: le as duas pontas, monta, reconcilia e compara. ZERO escrita."""
    with alvo_engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        esperado = _esperado(conn, comps, canais)
        existe = conn.execute(SQL_TABELA_EXISTE).scalar_one()
        atual = _rows(conn, SQL_ATUAL, {"competencias": comps, "canais": canais}) if existe else []
        conn.rollback()
    cmv, contraprova, repasse = ler_fonte(fonte_engine, comps, canais, _marcas(esperado))
    lote = montar_lote(esperado=esperado, cmv=cmv, contraprova=contraprova, repasse=repasse, canais=canais)
    return _resumo(lote, comps, canais, modo="dry-run", aplicado=False, tabela_existe=bool(existe),
                   diff=_diff(atual, lote.linhas) if existe else None), lote


def publicar(alvo_engine, fonte_engine, comps: list[date], canais: list[str], *,
             agora: Optional[datetime] = None,
             antes_do_commit: Optional[Callable[[Any], None]] = None) -> dict:
    """`--apply`: transacao unica no Neon, lock antes da leitura, recusa antes da escrita.

    `antes_do_commit` existe so' para teste: injeta falha DEPOIS da escrita e
    ANTES do commit, para provar que o rollback desfaz tudo.
    """
    agora = agora or datetime.now(timezone.utc)
    try:
        with alvo_engine.begin() as conn:
            if not conn.execute(SQL_LOCK).scalar_one():
                raise LockOcupado(f"advisory lock {LOCK_KEY} ocupado por outra publicacao")
            if not conn.execute(SQL_TABELA_EXISTE).scalar_one():
                raise MigrationAusente(f"{TABELA} nao existe: aplique a migration 025 antes do --apply")
            lote_id = conn.execute(SQL_AUDIT_ABRE, {"fonte": SOURCE_NAME, "agora": agora,
                                                    "min_date": comps[0], "max_date": ultimo_dia(comps[-1])}).scalar_one()
            esperado = _esperado(conn, comps, canais)
            cmv, contraprova, repasse = ler_fonte(fonte_engine, comps, canais, _marcas(esperado))
            lote = montar_lote(esperado=esperado, cmv=cmv, contraprova=contraprova, repasse=repasse, canais=canais)
            if lote.recusas:
                raise PublicacaoRecusada(lote.recusas)
            apagadas = conn.execute(SQL_APAGA_ESCOPO, {"competencias": comps, "canais": canais}).rowcount
            for ln in lote.linhas:
                conn.execute(SQL_INSERE, {**ln, "publicado_em": agora, "lote_id": lote_id})
            fechou = conn.execute(SQL_AUDIT_FECHA, {"agora": agora, "extraidas": lote.extraidas,
                                                    "gravadas": len(lote.linhas), "lote": lote_id}).rowcount
            if fechou != 1:
                raise RuntimeError(f"auditoria do lote {lote_id} nao fechou ({fechou} linha[s])")
            if antes_do_commit is not None:
                antes_do_commit(conn)
    except BaseException as exc:
        _registrar_falha(alvo_engine, comps, exc, agora)
        raise
    return _resumo(lote, comps, canais, modo="apply", aplicado=True, lote_id=lote_id, apagadas=apagadas)


def _registrar_falha(alvo_engine, comps: list[date], exc: BaseException, agora: datetime) -> None:
    """Auditoria da falha, em transacao PROPRIA. Nunca mascara a excecao original."""
    if isinstance(exc, LockOcupado):
        return  # outra publicacao esta em curso; registrar aqui seria ruido
    try:
        with alvo_engine.begin() as conn:
            conn.execute(SQL_AUDIT_FALHA, {"fonte": SOURCE_NAME, "agora": agora, "erro": sanitizar(exc),
                                           "min_date": comps[0], "max_date": ultimo_dia(comps[-1])})
    except Exception as aud:  # noqa: BLE001 — auditoria da falha nunca mascara a causa
        logger.error("auditoria da falha nao registrada: %s", sanitizar(aud))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_mes(s: str) -> date:
    y, m = s.split("-")
    return date(int(y), int(m), 1)


def url_psycopg2(url: str) -> str:
    """Fixa o driver `psycopg2` quando a URL nao declara nenhum.

    O SQLAlchemy 2.1 trocou o DBAPI padrao de `postgresql://` para `psycopg`
    (v3), e este pacote so' declara `psycopg2-binary`. Sem isto, um ambiente
    que resolva SQLAlchemy 2.1 quebra no `create_engine` (mesma causa do
    INCIDENTE-API-DB-4C, `docs/incidente_api_db_sqlalchemy_21.md`). URL com
    driver explicito e' respeitada.
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def _engines():
    """Engines a partir do ambiente (DATABASE_URL, DATAMART_DATABASE_URL). Sem log de URL."""
    from pipelines.common.config import settings

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL ausente")
    # `future=True`: a imagem do Airflow traz SQLAlchemy 1.4.54 (medido, ver
    # pyproject.toml raiz). Sem ele, a Connection do 1.4 nao tem `rollback()`
    # nem transacao implicita, e o `SET TRANSACTION READ ONLY` valeria so' para
    # o proprio comando. No 2.x o parametro e' aceito e nao muda nada.
    alvo = create_engine(url_psycopg2(settings.database_url), pool_pre_ping=True, future=True)
    fonte = (create_engine(url_psycopg2(settings.datamart_url), pool_pre_ping=True, future=True)
             if settings.datamart_url else None)
    return alvo, fonte


def main(argv: Optional[list[str]] = None, *, engines: Optional[Callable] = None,
         hoje: Optional[date] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipelines.dre.insumo_publisher")
    ap.add_argument("--de", help="primeira competencia, YYYY-MM")
    ap.add_argument("--ate", help="ultima competencia, YYYY-MM")
    ap.add_argument("--meses", type=int, default=4, help="meses fechados mais recentes (padrao 4)")
    ap.add_argument("--canais", default="tiktok,ml,shopee")
    ap.add_argument("--apply", action="store_true", help="escreve no Neon; sem ele e' dry-run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    hoje = hoje or datetime.now(TZ_BRT).date()
    if args.de or args.ate:
        if not (args.de and args.ate):
            ap.error("--de e --ate vao juntos")
        comps = competencias(_parse_mes(args.de), _parse_mes(args.ate))
    else:
        comps = janela_padrao(hoje, args.meses)
    if comps[-1] >= mes(hoje):
        ap.error("a competencia corrente ainda nao fechou; publique so' meses anteriores")
    canais = [c for c in CANAIS if c in args.canais.split(",")]

    alvo, fonte = (engines or _engines)()
    try:
        if args.apply:
            resumo = publicar(alvo, fonte, comps, canais)
        else:
            resumo, _ = diagnosticar(alvo, fonte, comps, canais)
    except PublicacaoRecusada as exc:
        print(json.dumps({"status": "recusado", "motivos": exc.motivos}, ensure_ascii=False, indent=1))
        return EXIT_RECONCILIACAO
    except LockOcupado as exc:
        print(json.dumps({"status": "lock_ocupado", "detalhe": str(exc)}, ensure_ascii=False))
        return EXIT_LOCK
    except MigrationAusente as exc:
        print(json.dumps({"status": "migration_ausente", "detalhe": str(exc)}, ensure_ascii=False))
        return EXIT_MIGRATION
    except FonteIndisponivel as exc:
        print(json.dumps({"status": "fonte_indisponivel", "detalhe": sanitizar(str(exc))}, ensure_ascii=False))
        return EXIT_FONTE
    if resumo["recusas"]:
        print(json.dumps({"status": "recusado", **resumo}, ensure_ascii=False, indent=1, default=str))
        return EXIT_RECONCILIACAO
    print(json.dumps({"status": "ok", **resumo}, ensure_ascii=False, indent=1, default=str))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
