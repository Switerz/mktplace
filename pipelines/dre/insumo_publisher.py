"""Publisher dos insumos mensais da DRE: Data Mart -> Neon (Gate DRE-DATA-BRIDGE-1).

O QUE ELE FAZ
-------------
Le, no Data Mart e em transacao READ ONLY, somente AGREGADOS MENSAIS por
`competencia x canal x marca`:

  * CMV fiscal e a receita fiscal que o acompanha, somados do grao de ITEM
    (`gold.nf_vendas_unificada_v2`, a base da query de custos do FP&A), com
    contraprova contra o agregado `gold.nf_vendas_plataforma_sku_mensal`;
  * taxa, frete, afiliado, receita e residuo da identidade do repasse TikTok
    por coorte de pedido (`silver.stg_tiktok_payments_by_order`);
  * taxa da Shopee do escrow (comissao + servico + transacao) por coorte de
    criacao do pedido, com a cobertura em pedidos (T-0059). So' entra quando o
    CHECK da tabela no Neon ja aceita o componente — ver `TAXA_SHOPEE`;
  * imposto sobre venda realizado (ICMS, DIFAL, PIS+COFINS) do webgex, casado
    com a NF de marketplace pela chave, com a cobertura em receita fiscal
    (T-0069). So' entra depois da migration 040 — ver `IMPOSTOS`.

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
    python -m pipelines.dre.insumo_publisher --meses 4 --apply --sem-vendas 2026-08:shopee

ESCOPO NUNCA SAI VAZIO EM SILENCIO
----------------------------------
O universo esperado vem da receita da Torre. Se ele estiver vazio, ou se um
par `competencia x canal` pedido nao tiver celula nenhuma, a publicacao e'
RECUSADA antes do DELETE: apagar o escopo e fechar `success` trocaria a
fotografia anterior por nada. Mes/canal legitimamente sem vendas precisa ser
declarado com `--sem-vendas YYYY-MM:canal` -- e a declaracao e' conferida: se
houver receita, a publicacao tambem e' recusada.

MARCA JA PUBLICADA NAO SOME EM SILENCIO
---------------------------------------
Numa republicacao, toda chave `competencia x canal x marca` ja publicada e'
comparada com o novo universo esperado. Se uma marca some de um mes/canal que
continua existindo, a publicacao e' RECUSADA antes do DELETE e a chave e'
nomeada. Uma marca pode deixar um canal daqui para a frente, mas isso nao
apaga uma competencia historica fechada: a remocao exige
`--remover-marca YYYY-MM:canal:marca`, conferida (a chave tem de estar
publicada e sem receita) e registrada na auditoria do lote.

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

#: T-0055 (C1): a fonte do CMV passou do agregado `nf_vendas_plataforma_sku_mensal`,
#: que fica dias atras da base de item (out/26 parou em 27/10 com a regua "_vivo"),
#: para a propria base de item. Em mes estavel as duas sao identicas (set/26 medido
#: ao real); a troca so' muda o mes que o agregado ainda nao alcancou.
PROC_CMV = "gold.nf_vendas_unificada_v2 (canal=marketplace, sem intercompany) @dre-custos-vivos-1"
PROC_REPASSE = "silver.stg_tiktok_payments_by_order (transaction_type=ORDER, coorte) @dre-bridge-1"

#: T-0059 — taxa da Shopee do escrow (`payment/get_escrow_detail`), carregada no
#: Data Mart em `silver.fct_gobeaute_despesas_shopee` (lojas com aplicacao:
#: apice, barbours, lescent, rituaria; kokeshi nao tem). Desde o `daily_shopee`
#: (22/10/2026) a fato diaria grava `total_fees` NULL nessas lojas numa janela
#: de 60 dias, e a DRE ficou sem taxa de set/out.
#:
#: INERTE ATE' A MIGRATION: o CHECK `ck_fdim_componente` (025) e' lista fechada.
#: O publisher so' le' e grava este componente quando o CHECK do Neon ja o
#: aceita (`SQL_CHECK_COMPONENTE`); antes disso o lote sai identico ao de hoje,
#: com uma observacao. Nada quebra na publicacao diaria.
TAXA_SHOPEE = "shopee_taxa_escrow"
PROC_TAXA_SHOPEE = ("silver.fct_gobeaute_despesas_shopee (comissao+servico+transacao) x silver.stg_shopee_orders "
                    "(coorte de criacao BRT, nao cancelados) @T-0059")

#: T-0064 — completude pela NUMERACAO de NF. Rotulo estavel do trecho do
#: `motivo` que o serving (`app.services.dre_service.BURACO_NF_MARCA`)
#: reconhece para pôr o CMV em `parcial`. O CHECK `ck_fdim_status` e' lista
#: fechada e nao tem `parcial`, entao o estado viaja no texto, sem migration.
#: Um teste compara as duas constantes.
BURACO_NF_MARCA = "buraco de numeracao de NF em "
#: Menor buraco CONTIGUO (numeros faltando entre duas notas vizinhas da mesma
#: serie) que torna o mes `parcial`. Buraco menor e' tratado como inutilizacao
#: ou nota fora da base de vendas e so' aparece no resumo. Medido na gold
#: (jun, ago, set, out/26 x 5 marcas; jul estourou o tempo de leitura): a serie
#: 001 dos emissores principais intercala notas que nao sao de venda e tem buraco
#: de ate' 474 numeros sem incidente (03/08); com 50, todas as marcas ficavam
#: `parcial` em set e out. Os incidentes conhecidos (Apice 03/10, Barbours 04-05/10)
#: tem 810 a 1.640 numeros contiguos. Rever com o resumo `numeracao_nf`.
BURACO_NF_MINIMO = 500
#: Quantos dias o `motivo` nomeia; o resto vira "e mais N dia(s)".
BURACO_NF_DIAS_NO_MOTIVO = 10

#: T-0069 — imposto sobre venda REALIZADO, do webgex (`raw.webgex_impostos_gobeaute`
#: + `raw.webgex_impostos_ap`, circuito `VX`, debito `DB`), casado com a NF de
#: marketplace pela chave. Valor ABSOLUTO (custo positivo, `sinal_origem = 1`),
#: nunca % aplicado sobre o GMV. Validacao: `webgex_impostos_validacao.md`
#: (05/11/2026).
#:
#: INERTE ATE' A MIGRATION 040: o CHECK `ck_fdim_componente` e' lista fechada. O
#: publisher so' le' e grava os tres componentes quando o CHECK do Neon ja
#: aceita OS TRES (`check_aceita_impostos`); antes disso o lote sai identico ao
#: de hoje e o resumo diz `inerte`. Mesmo mecanismo da `TAXA_SHOPEE`.
IMPOSTO_ICMS = "imposto_icms"
IMPOSTO_DIFAL = "imposto_difal"
IMPOSTO_PIS_COFINS = "imposto_pis_cofins"
IMPOSTOS = (IMPOSTO_ICMS, IMPOSTO_DIFAL, IMPOSTO_PIS_COFINS)
PROC_IMPOSTOS = ("raw.webgex_impostos_gobeaute + raw.webgex_impostos_ap (circuito VX, debito DB) x "
                 "gold.nf_vendas_unificada_v2 (chave_nota = chave_nfe; canal=marketplace, sem intercompany) @T-0069")
#: Cobertura minima: receita fiscal das NFs casadas no webgex / receita fiscal
#: da celula. Abaixo dela o imposto e' `parcial` (Lescent set/26: 13 dias sem
#: carga, ~62-67%). Mesmo limiar do CMV no serving (`COBERTURA_CMV_MINIMA`).
IMPOSTO_COBERTURA_MINIMA = Decimal("0.98")
#: Rotulo estavel dos trechos do `motivo` que tornam o imposto `parcial`. O
#: CHECK `ck_fdim_status` nao tem `parcial`; como no `BURACO_NF_MARCA` (T-0064),
#: o estado viaja no texto, sem migration. Cada trecho comeca com o rotulo e
#: nao tem `; ` dentro, para o serving separa'-lo do resto do motivo.
IMPOSTO_PARCIAL = "imposto parcial: "
#: Shopee: o imposto so' cobre a receita FATURADA (21-36% do GMV em set/26).
#: Ate' a pergunta F1 ser respondida, todo imposto Shopee e' `parcial`.
IMPOSTO_PARCIAL_SHOPEE = IMPOSTO_PARCIAL + "imposto so' sobre a receita fiscal faturada (F1 em aberto)"
#: Marca -> primeira competencia em que a BASE do ICMS mudou e ainda nao foi
#: confirmada pelo Fiscal. Rituaria out/26: a base passou a ser o valor ANTES
#: do desconto (05/10: base 49,4 mil x receita 27,7 mil; em 05/08 base =
#: receita), e a aliquota efetiva saltou para 32,6% (Shopee) e 38,3% (TikTok).
#: Vale para a competencia e as POSTERIORES, em todos os canais da marca (a
#: base e' do emissor), ate' decisao do Fiscal -- ai' remover/ajustar aqui.
IMPOSTO_BASE_PENDENTE_FISCAL = {"rituaria": date(2026, 10, 1)}
IMPOSTO_PARCIAL_BASE = IMPOSTO_PARCIAL + "base do ICMS mudou em out/26 (desconto na base), pendente do Fiscal"
#: Folga da janela de `data` do webgex em torno das competencias lidas.
WEBGEX_FOLGA = timedelta(days=7)

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

#: CMV do grao de ITEM, somado direto para `competencia x marca x plataforma`.
#: Mesmas colunas de saida da leitura antiga (o agregado SKU), entao
#: `montar_lote` nao muda de forma. Regras, todas da query de custos do FP&A:
#:   * `canal = 'marketplace'` no canal ORIGINAL do item: o TikTok e' o item
#:     de `plataforma = 'tiktokshop'` dentro dele (paridade medida em set/26);
#:   * plataforma vem do item; so' as tres da Torre. amazon, outro_marketplace,
#:     nao_mapeada, sem_loja etc. ficam FORA -- nunca redistribuidas;
#:   * `intercompany IS NOT TRUE` = o default `tipo_operacao = 'normal'` da
#:     query (NULL conta como normal); `NOT intercompany` descartaria o NULL;
#:   * `SUM(custo)` ignora o nulo: custo ausente nao vira zero, e a receita que
#:     ele deixa descoberta sai de `receita_com_custo` (a cobertura);
#:   * `custo_provisorio` e' booleano no item: a soma filtrada reproduz o valor
#:     do agregado (provado em set/26).
#: Sem JOIN: uma tabela, GROUP BY na chave inteira da saida. Nada multiplica.
SQL_CMV = text("""
    SELECT data_mes AS competencia, marca, plataforma,
           SUM(receita_produto)                                  AS receita_fiscal,
           SUM(custo)                                            AS custo,
           SUM(custo) FILTER (WHERE custo_provisorio)            AS custo_provisorio,
           SUM(receita_produto) FILTER (WHERE custo IS NOT NULL) AS receita_com_custo,
           MAX(data_emissao)::date                               AS ultima_emissao,
           COUNT(*)                                              AS linhas
    FROM gold.nf_vendas_unificada_v2
    WHERE data_mes = ANY(:competencias)
      AND canal = 'marketplace'
      AND plataforma = ANY(:plataformas)
      AND marca = ANY(:marcas)
      AND intercompany IS NOT TRUE
    GROUP BY data_mes, marca, plataforma
""")

#: Contraprova: o agregado que o Data Mart materializa a partir da base de
#: item (era a fonte antiga). Ele anda ATRAS da base de item, entao so'
#: bloqueia a publicacao quando ja alcancou o mes -- ver `contraprova_cmv`.
SQL_CMV_CONTRAPROVA = text("""
    SELECT data_mes AS competencia, marca, plataforma,
           SUM(receita_produto)      AS receita_fiscal,
           SUM(custo)                AS custo,
           MAX(ultima_emissao)::date AS ultima_emissao
    FROM gold.nf_vendas_plataforma_sku_mensal
    WHERE data_mes = ANY(:competencias)
      AND canal = 'marketplace'
      AND plataforma = ANY(:plataformas)
      AND marca = ANY(:marcas)
    GROUP BY data_mes, marca, plataforma
""")

#: Gate de completude pela numeracao de NF (T-0064). A chave de acesso (44
#: digitos) traz o emissor (CNPJ, pos. 7-20), o modelo (21-22), a serie
#: (23-25) e o numero (26-34). Por emissor x modelo x serie, ordena os numeros
#: presentes e mede o buraco entre vizinhos (`faltam`); o buraco e' atribuido
#: ao DIA da nota seguinte (a primeira que voltou a aparecer).
#:   * a numeracao e' do EMISSOR, nao do canal: entram todas as notas da gold
#:     dos emissores das marcas da Torre (qualquer canal, plataforma ou
#:     intercompany). Filtrar marketplace abriria buraco onde ha' nota de
#:     e-commerce no meio;
#:   * le' tambem o mes ANTERIOR a' janela, para medir o buraco no inicio do
#:     primeiro mes; so' volta buraco datado dentro da janela;
#:   * emissor -> marca vem da propria gold (CNPJ da chave x `marca`), medido
#:     1:1 nas cinco marcas; nada fixo no codigo;
#:   * a gold exclui NF rejeitada/denegada pela SEFAZ, entao o buraco junta "nao
#:     ingerida" e "rejeitada" -- a gold nao distingue as duas.
#: Agrega por dia; o limiar (`:minimo`) e' aplicado por buraco contiguo.
SQL_NUMERACAO_NF = text("""
    WITH emissores AS (
        SELECT DISTINCT substr(chave_nfe, 7, 14) AS emissor, marca
        FROM gold.nf_vendas_unificada_v2
        WHERE data_mes = ANY(:competencias)
          AND marca = ANY(:marcas)
          AND chave_nfe ~ '^[0-9]{44}$'
    ), notas AS (
        SELECT substr(chave_nfe, 7, 14) AS emissor, substr(chave_nfe, 21, 2) AS modelo,
               substr(chave_nfe, 23, 3) AS serie, substr(chave_nfe, 26, 9)::bigint AS numero,
               MIN(data_emissao)::date AS dia
        FROM gold.nf_vendas_unificada_v2
        WHERE data_mes = ANY(:meses_numeracao)
          AND chave_nfe ~ '^[0-9]{44}$'
          AND substr(chave_nfe, 7, 14) IN (SELECT emissor FROM emissores)
        GROUP BY 1, 2, 3, 4
    ), buracos AS (
        SELECT emissor, modelo, serie, dia,
               numero - LAG(numero) OVER (PARTITION BY emissor, modelo, serie ORDER BY numero) - 1 AS faltam
        FROM notas
    )
    SELECT date_trunc('month', b.dia)::date                            AS competencia,
           e.marca                                                     AS marca,
           b.modelo                                                    AS modelo,
           b.serie                                                     AS serie,
           b.dia                                                       AS dia,
           COUNT(*)                                                    AS buracos,
           SUM(b.faltam)                                               AS faltam,
           MAX(b.faltam)                                               AS maior_buraco,
           COUNT(*) FILTER (WHERE b.faltam >= :minimo)                 AS buracos_relevantes,
           COALESCE(SUM(b.faltam) FILTER (WHERE b.faltam >= :minimo), 0) AS faltam_relevantes
    FROM buracos b
    JOIN emissores e ON e.emissor = b.emissor
    WHERE b.faltam > 0 AND b.dia >= :inicio AND b.dia < :fim_exclusivo
    GROUP BY 1, 2, 3, 4, 5
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

#: Taxa da Shopee por coorte de CRIACAO do pedido (BRT) — o mesmo eixo do GMV
#: da fato diaria (`daily_shopee`). Base = pedidos nao cancelados da silver de
#: pedidos (um por `order_sn`, a versao mais recente); o escrow entra por LEFT
#: JOIN, entao a cobertura e' medida contra o que DEVERIA ter taxa:
#:   * `taxa` soma SO' pedidos com as tres parcelas preenchidas; parcela NULL
#:     nao vira zero — o pedido conta como sem escrow;
#:   * pedido sem escrow (em transito, devolucao) fica fora da soma e reduz a
#:     cobertura; nada e' extrapolado;
#:   * PK do escrow e' (canal_origem, pedido): filtrar `canal_origem='shopee'`
#:     garante uma linha por pedido, logo o JOIN nao multiplica.
#: Medido (05/11/2026): escrow so' existe para pedido nao cancelado; dia do
#: escrow = `created_date_brt` em 100% dos casamentos.
SQL_TAXA_SHOPEE = text("""
    WITH pedidos AS (
        SELECT DISTINCT ON (order_sn) order_sn, brand, created_date_brt, lower(order_status) AS status
        FROM silver.stg_shopee_orders
        WHERE created_date_brt >= :inicio AND created_date_brt < :fim_exclusivo
          AND brand = ANY(:marcas)
        ORDER BY order_sn, update_time DESC NULLS LAST
    ), escrow AS (
        SELECT pedido,
               comissao_plataforma_fee + taxa_servico_fee + taxa_transacao_fee AS taxa,
               (comissao_plataforma_fee < 0 OR taxa_servico_fee < 0 OR taxa_transacao_fee < 0) AS negativa,
               data_carga
        FROM silver.fct_gobeaute_despesas_shopee
        WHERE canal_origem = 'shopee'
    )
    SELECT date_trunc('month', p.created_date_brt)::date AS competencia,
           p.brand                                        AS marca,
           COUNT(*)                                       AS pedidos,
           COUNT(e.taxa)                                  AS pedidos_com_escrow,
           SUM(e.taxa)                                    AS taxa,
           COUNT(*) FILTER (WHERE e.negativa)             AS parcelas_negativas,
           MAX(e.data_carga)                              AS atualizado_em
    FROM pedidos p
    LEFT JOIN escrow e ON e.pedido = p.order_sn
    WHERE p.status <> 'cancelled'
    GROUP BY 1, 2
""")

#: Imposto realizado por `competencia x marca x plataforma` (T-0069).
#:   * `notas`: as MESMAS linhas do `SQL_CMV` (mesmos filtros de celula),
#:     colapsadas para UMA linha por chave x celula ANTES do JOIN. A gold tem
#:     uma linha por ITEM; juntar item com imposto multiplicaria o imposto pelo
#:     numero de itens. `receita_fiscal` da celula = a do CMV (conferida no lote);
#:   * `celulas_da_chave`: uma chave que cai em mais de uma celula duplicaria o
#:     imposto; o lote recusa (`chaves_ambiguas`). Chave NULL nao conta como
#:     ambigua: ela so' nao casa e reduz a cobertura;
#:   * `impostos`: o webgex e' uma linha por imposto x item de NF; agrega por
#:     chave (as duas tabelas, UNION ALL: emissoras distintas). Venda =
#:     `circuito = 'VX'` e `debito_credito = 'DB'` (fora: OX, CX, TF, DX/DV).
#:     DIFAL e' a coluna `valor_difal` das linhas de ICMS, FORA de
#:     `valor_imposto`. IPI fica fora (valor zero medido). Sem COALESCE: linha
#:     ausente nao vira zero;
#:   * cobertura = receita das NFs casadas / receita fiscal da celula. NF sem
#:     linha no webgex fica fora da soma; nada e' extrapolado;
#:   * janela em `data` do webgex (`janela_webgex`): so' para reduzir a
#:     varredura (~28 mi linhas). Folga de `WEBGEX_FOLGA` antes e depois da
#:     janela, para o lancamento fiscal que cai fora do mes da emissao. A
#:     competencia continua vindo da gold (`data_mes`) pela chave: a folga nao
#:     muda a atribuicao, so' deixa de ler linha que nenhuma NF da janela casaria.
SQL_IMPOSTOS = text("""
    WITH notas AS (
        SELECT chave_nfe, data_mes, marca, plataforma, SUM(receita_produto) AS receita_fiscal,
               COUNT(*) OVER (PARTITION BY chave_nfe) AS celulas_da_chave
        FROM gold.nf_vendas_unificada_v2
        WHERE data_mes = ANY(:competencias)
          AND canal = 'marketplace'
          AND plataforma = ANY(:plataformas)
          AND marca = ANY(:marcas)
          AND intercompany IS NOT TRUE
        GROUP BY chave_nfe, data_mes, marca, plataforma
    ), webgex AS (
        SELECT chave_nota, codigo_imposto, valor_imposto, valor_difal, ingested_at
        FROM raw.webgex_impostos_gobeaute
        WHERE circuito = 'VX' AND debito_credito = 'DB'
          AND data >= :inicio_webgex AND data < :fim_webgex
        UNION ALL
        SELECT chave_nota, codigo_imposto, valor_imposto, valor_difal, ingested_at
        FROM raw.webgex_impostos_ap
        WHERE circuito = 'VX' AND debito_credito = 'DB'
          AND data >= :inicio_webgex AND data < :fim_webgex
    ), impostos AS (
        SELECT chave_nota,
               SUM(valor_imposto) FILTER (WHERE codigo_imposto = 'ICM')               AS icms,
               SUM(valor_difal)   FILTER (WHERE codigo_imposto = 'ICM')               AS difal,
               SUM(valor_imposto) FILTER (WHERE codigo_imposto IN ('PIS', 'COF'))     AS pis_cofins,
               MAX(ingested_at)                                                       AS ingerido_em
        FROM webgex
        WHERE chave_nota IN (SELECT chave_nfe FROM notas)
        GROUP BY chave_nota
    )
    SELECT n.data_mes                                                        AS competencia,
           n.marca                                                           AS marca,
           n.plataforma                                                      AS plataforma,
           SUM(n.receita_fiscal)                                             AS receita_fiscal,
           SUM(n.receita_fiscal) FILTER (WHERE i.chave_nota IS NOT NULL)     AS receita_com_imposto,
           COUNT(*)                                                          AS notas,
           COUNT(i.chave_nota)                                               AS notas_com_imposto,
           SUM(i.icms)                                                       AS icms,
           SUM(i.difal)                                                      AS difal,
           SUM(i.pis_cofins)                                                 AS pis_cofins,
           COUNT(*) FILTER (WHERE n.chave_nfe IS NOT NULL AND n.celulas_da_chave > 1) AS chaves_ambiguas,
           MAX(i.ingerido_em)                                                AS atualizado_em
    FROM notas n
    LEFT JOIN impostos i ON i.chave_nota = n.chave_nfe
    GROUP BY n.data_mes, n.marca, n.plataforma
""")

SQL_TABELA_EXISTE = text("SELECT to_regclass('marts.fact_dre_insumo_mensal') IS NOT NULL")
#: Definicao do CHECK de componentes no Neon. `TAXA_SHOPEE` so' e' publicada
#: quando aparece nela (migration que estende a 025 aplicada).
SQL_CHECK_COMPONENTE = text("""
    SELECT pg_get_constraintdef(c.oid)
    FROM pg_constraint c
    WHERE c.conname = 'ck_fdim_componente'
      AND c.conrelid = to_regclass('marts.fact_dre_insumo_mensal')
""")
SQL_LOCK = text(f"SELECT pg_try_advisory_xact_lock({LOCK_KEY})")
SQL_AUDIT_ABRE = text("""
    INSERT INTO audit.source_sync_run (source_name, status, started_at, source_min_date, source_max_date)
    VALUES (:fonte, 'running', :agora, :min_date, :max_date)
    RETURNING sync_run_id
""")
#: Em `success`, `error_message` carrega as EXCECOES AUTORIZADAS do lote
#: (`--sem-vendas`, `--remover-marca`) -- e' o rastro auditavel de quem
#: autorizou apagar o que. NULL quando nao houve excecao.
SQL_AUDIT_FECHA = text("""
    UPDATE audit.source_sync_run
       SET status = 'success', finished_at = :agora, rows_extracted = :extraidas, rows_loaded = :gravadas,
           error_message = :excecoes
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
SQL_PUBLICADAS = text(f"""
    SELECT DISTINCT competencia, canal, marca
    FROM {TABELA} WHERE competencia = ANY(:competencias) AND canal = ANY(:canais)
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


def _data(v) -> Optional[date]:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


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
    #: Observacoes que NAO recusam (contraprova contra agregado defasado).
    observacoes: list[str] = field(default_factory=list)
    extraidas: int = 0
    #: Resultado do gate de numeracao de NF (T-0064), para o resumo JSON.
    numeracao_nf: dict = field(default_factory=dict)
    #: Celulas com imposto `parcial` (T-0069), para o resumo JSON.
    impostos_parciais: list = field(default_factory=list)
    #: Estado dos impostos decidido na montagem (`publicado` ou `recusado: ...`);
    #: None = nao houve leitura (o estado vem de `ler_impostos`).
    impostos_estado: Optional[str] = None
    #: Motivos da validacao dos impostos que reprovou. NAO entram em `recusas`.
    impostos_recusas: list = field(default_factory=list)


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


def recusas_de_escopo(chaves_esperadas: set, comps: list[date], canais: list[str],
                      sem_vendas: frozenset = frozenset()) -> list[str]:
    """Recusas que impedem trocar a fotografia por um escopo vazio.

    `chaves_esperadas` sao as celulas `(competencia, canal, marca)` com receita
    na Torre. A publicacao substitui `comps x canais` inteiro; sem esta guarda,
    um universo vazio (carga quebrada, filtro errado) apagaria tudo e fecharia
    `success`. `sem_vendas` e' a unica saida legitima, e e' explicita.
    """
    recusas: list[str] = []
    pares_com_receita = {(k[0], k[1]) for k in chaves_esperadas}
    if not chaves_esperadas and not all((m, c) in sem_vendas for m in comps for c in canais):
        recusas.append(
            "universo esperado vazio: nenhuma celula com receita na Torre para o escopo pedido; "
            "nada foi apagado"
        )
    for m in comps:
        for c in canais:
            par = (m, c)
            rotulo = f"{m.isoformat()[:7]} {c}"
            if par in sem_vendas and par in pares_com_receita:
                recusas.append(f"{rotulo}: declarado --sem-vendas, mas ha receita na Torre")
            elif par not in sem_vendas and par not in pares_com_receita:
                recusas.append(
                    f"{rotulo}: nenhuma celula com receita na Torre; se for legitimo, "
                    f"declare --sem-vendas {rotulo.replace(' ', ':')}"
                )
    return recusas


def rotulo_chave(chave: tuple) -> str:
    """`(competencia, canal, marca)` -> `YYYY-MM:canal:marca`, o grao do CLI."""
    return f"{chave[0].isoformat()[:7]}:{chave[1]}:{chave[2]}"


def recusas_de_marca(chaves_esperadas: set, publicadas: set, sem_vendas: frozenset = frozenset(),
                     remover: frozenset = frozenset()) -> list[str]:
    """Recusas que impedem uma marca ja publicada de sumir em silencio.

    `publicadas` sao as chaves `(competencia, canal, marca)` que a fotografia
    atual tem para o escopo. Uma delas fora do novo universo esperado so' pode
    ser apagada com `--remover-marca` explicito. Par inteiro declarado
    `--sem-vendas` ja e' a autorizacao no grao do par.
    """
    recusas: list[str] = []
    for chave in sorted(publicadas):
        if (chave[0], chave[1]) in sem_vendas or chave in chaves_esperadas or chave in remover:
            continue
        rot = rotulo_chave(chave)
        recusas.append(
            f"chave publicada desapareceu: {rot} (mes/canal continua no escopo). Competencia fechada nao "
            f"perde marca em silencio; para remover, declare --remover-marca {rot}"
        )
    for chave in sorted(remover):
        rot = rotulo_chave(chave)
        if chave in chaves_esperadas:
            recusas.append(f"--remover-marca {rot}: a chave ainda tem receita na Torre")
        elif chave not in publicadas:
            recusas.append(f"--remover-marca {rot}: a chave nao esta publicada; nada a remover")
    return recusas


def _fmt_dia(d: Optional[date]) -> str:
    return "-" if d is None else d.strftime("%d/%m")


def contraprova_cmv(cmv_idx: dict, cp_idx: dict) -> tuple[list[str], dict]:
    """Confere o CMV da base de item contra o agregado gold, mes a mes.

    O agregado e' derivado da base de item e anda dias atras dela. Por
    competencia, ele esta COMPLETO quando sua ultima emissao alcancou a da base
    de item (`agregado >= item`; mes sem nota na base de item conta como
    alcancado). Entao:

      * completo: mesma chave e mesmo total (receita e custo, tolerancia de
        `CONTRAPROVA_MAX`) nos dois sentidos; divergiu, recusa -- uma das duas
        tabelas esta no meio de uma carga;
      * defasado: a defasagem explica o agregado ter MENOS (chave a menos,
        receita a menos, custo de outra regua), nunca MAIS. Recusa so' chave
        presente apenas no agregado ou receita da base de item abaixo da dele;
        o resto vira observacao, gravada no `motivo` do CMV daquele mes.

    Devolve `(recusas, {competencia: observacao})`.
    """
    recusas: list[str] = []
    item_max: dict[date, Optional[date]] = {}
    agg_max: dict[date, Optional[date]] = {}
    for idx, alvo in ((cmv_idx, item_max), (cp_idx, agg_max)):
        for k, r in idx.items():
            d, atual = _data(r["ultima_emissao"]), alvo.get(k[0])
            alvo[k[0]] = d if atual is None or (d is not None and d > atual) else atual

    completo: dict[date, bool] = {}
    obs: dict[date, str] = {}
    for m in set(item_max) | set(agg_max):
        i, a = item_max.get(m), agg_max.get(m)
        completo[m] = i is None or (a is not None and a >= i)
        if not completo[m]:
            obs[m] = ("contraprova nao bloqueante: agregado gold sem a competencia" if a is None else
                      f"contraprova nao bloqueante: agregado gold ate {_fmt_dia(a)}, base de item ate {_fmt_dia(i)}")

    for k in set(cmv_idx) | set(cp_idx):
        a, b = cmv_idx.get(k), cp_idx.get(k)
        if completo[k[0]]:
            if a is None or b is None:
                recusas.append(f"cmv: chave {k} presente so' em uma das duas tabelas fiscais")
                continue
            for col in ("receita_fiscal", "custo"):
                da, db_ = _dec(a[col]) or Decimal(0), _dec(b[col]) or Decimal(0)
                if abs(da - db_) > CONTRAPROVA_MAX:
                    recusas.append(f"cmv: {col} {k} diverge do agregado mensal em {da - db_}")
        elif a is None:
            recusas.append(f"cmv: chave {k} so' no agregado gold defasado; a base de item perdeu a chave")
        elif b is not None:
            da, db_ = _dec(a["receita_fiscal"]) or Decimal(0), _dec(b["receita_fiscal"]) or Decimal(0)
            if da < db_ - CONTRAPROVA_MAX:
                recusas.append(f"cmv: receita_fiscal {k} da base de item abaixo do agregado gold defasado "
                               f"em {db_ - da}")
    return recusas, obs


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def buracos_numeracao_nf(numeracao: list[dict]) -> tuple[dict, dict]:
    """Gate de completude pela numeracao de NF (T-0064). PURA.

    Entrada: linhas de `SQL_NUMERACAO_NF` (marca x modelo x serie x dia).
    Um dia conta quando tem buraco contiguo >= `BURACO_NF_MINIMO`
    (`faltam_relevantes > 0`); os numeros das series se somam no dia.

    Devolve `({(competencia, marca): trecho_do_motivo}, resumo)`. O trecho e'
    `buraco de numeracao de NF em 03/10 (1.914 numeros), 04/10 (12 numeros)`,
    sem `; ` dentro, para o serving separa'-lo do resto do motivo. O resumo
    lista tambem os buracos ABAIXO do limiar (contagem e maior), que sao a
    medicao para rever o limiar.
    """
    por_dia: dict[tuple, dict[date, int]] = {}
    abaixo: dict[tuple, dict] = {}
    for r in numeracao:
        k = (_mes_de(r["competencia"]), r["marca"])
        relevantes = int(r["faltam_relevantes"] or 0)
        if relevantes > 0:
            dia = _data(r["dia"])
            por_dia.setdefault(k, {})
            por_dia[k][dia] = por_dia[k].get(dia, 0) + relevantes
        n_abaixo = int(r["buracos"] or 0) - int(r["buracos_relevantes"] or 0)
        if n_abaixo > 0:
            a = abaixo.setdefault(k, {"buracos": 0, "numeros": 0, "maior": 0})
            a["buracos"] += n_abaixo
            a["numeros"] += int(r["faltam"] or 0) - relevantes
            # `maior_buraco` e' do dia inteiro; so' vale como "abaixo" se o dia nao teve relevante
            if not int(r["buracos_relevantes"] or 0):
                a["maior"] = max(a["maior"], int(r["maior_buraco"] or 0))

    motivos: dict[tuple, str] = {}
    for k, dias in por_dia.items():
        ordem = sorted(dias)
        nomeados = [f"{_fmt_dia(d)} ({_milhar(dias[d])} numeros)" for d in ordem[:BURACO_NF_DIAS_NO_MOTIVO]]
        resto = ordem[BURACO_NF_DIAS_NO_MOTIVO:]
        if resto:
            nomeados.append(f"e mais {len(resto)} dia(s) ({_milhar(sum(dias[d] for d in resto))} numeros)")
        motivos[k] = BURACO_NF_MARCA + ", ".join(nomeados)

    resumo = {
        "limiar_buraco": BURACO_NF_MINIMO,
        "parciais": [{"competencia": m.isoformat()[:7], "marca": marca, "motivo": motivos[(m, marca)],
                      "numeros": sum(por_dia[(m, marca)].values())}
                     for m, marca in sorted(motivos)],
        "abaixo_do_limiar": [{"competencia": m.isoformat()[:7], "marca": marca, **abaixo[(m, marca)]}
                             for m, marca in sorted(abaixo)],
    }
    return motivos, resumo


def montar_lote(*, esperado: list[dict], cmv: list[dict], contraprova: list[dict],
                repasse: list[dict], canais: list[str], comps: list[date],
                sem_vendas: frozenset = frozenset(), publicadas: frozenset = frozenset(),
                remover: frozenset = frozenset(), taxa_shopee: Optional[list[dict]] = None,
                numeracao_nf: Optional[list[dict]] = None, impostos: Optional[list[dict]] = None) -> Lote:
    """Transforma a leitura em linhas da fato e lista as recusas.

    Recusa (a publicacao inteira aborta) quando:
      * a fonte tem chave repetida;
      * o CMV da base de item diverge do agregado gold num mes que o agregado
        ja alcancou; com o agregado defasado, so' se a base de item tiver
        MENOS que ele (`contraprova_cmv`);
      * CMV negativo, cobertura ou provisorio fora de [0, 1];
      * a identidade do repasse TikTok nao fecha numa celula;
      * alguma transacao tem afiliado maior que a propria taxa.

      * o universo esperado, ou um par competencia x canal, esta vazio sem
        declaracao explicita (`recusas_de_escopo`);
      * uma chave ja publicada some sem `--remover-marca` (`recusas_de_marca`).

    NAO recusa — e grava com status — o CMV cuja receita fiscal nao bate com o
    GMV da Torre: isso e' estado do dado (Shopee hoje), nao defeito de carga.

    `taxa_shopee=None` = o Neon ainda nao aceita `TAXA_SHOPEE`: nenhuma linha do
    componente e' montada (o lote fica como antes do T-0059). Lista (mesmo
    vazia) = publicar o componente para toda celula Shopee esperada.

    `numeracao_nf` (T-0064): linhas de `SQL_NUMERACAO_NF`. Marca x mes com
    buraco de numeracao ganha o trecho `buraco de numeracao de NF em ...` no
    `motivo` do `cmv` e do `cmv_receita_fiscal` de TODOS os canais dela (a
    numeracao e' do emissor). Valor, status e cobertura NAO mudam -- nada e'
    extrapolado -- e nunca recusa. `None` = gate nao avaliado (lote como antes).

    `impostos` (T-0069): linhas de `SQL_IMPOSTOS`. `None` = o Neon ainda nao
    aceita os componentes (antes da 040): nenhuma linha de imposto e' montada e
    o lote fica como antes. Lista (mesmo vazia) = publicar os tres componentes
    para toda celula esperada (`_linhas_impostos`). A validacao dos impostos
    (chave repetida, chave de NF em mais de uma celula, receita fiscal diferente
    da do CMV, imposto negativo, cobertura fora de [0, 1]) NUNCA recusa o lote:
    se reprovar, NENHUMA linha de imposto e' montada (fail-closed dos impostos,
    como no modo inerte), o resto do lote sai igual e o motivo vai para
    `impostos_estado = "recusado: ..."`. Motivo: CMV e impostos sao lidos em
    statements separados (READ COMMITTED numa replica); uma recarga da gold
    entre os dois nao pode derrubar a DRE do dia.
    """
    lote = Lote(extraidas=len(cmv) + len(repasse) + len(taxa_shopee or []) + len(impostos or []))
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
    txs_idx = indexar(taxa_shopee or [], lambda r: (_mes_de(r["competencia"]), r["marca"]), "taxa_shopee")
    # Impostos indexados a' parte: chave repetida aqui reprova SO' os impostos.
    imp_idx: dict = {}
    imp_recusas: list[str] = []
    for r in impostos or []:
        k = (_mes_de(r["competencia"]), plat_para_canal.get(r["plataforma"]), r["marca"])
        if k in imp_idx:
            imp_recusas.append(f"impostos: chave repetida {k}")
        imp_idx[k] = r
    esp = {}
    for r in esperado:
        canal = {v: k for k, v in MKT_ID.items()}.get(r["marketplace_id"])
        if canal in canais:
            esp[(_mes_de(r["competencia"]), canal, r["marca"])] = _dec(r["gmv"])
    lote.recusas.extend(recusas_de_escopo(set(esp), comps, canais, sem_vendas))
    lote.recusas.extend(recusas_de_marca(set(esp), set(publicadas), sem_vendas, remover))

    recusas_cp, obs_por_mes = contraprova_cmv(cmv_idx, cp_idx)
    lote.recusas.extend(recusas_cp)
    lote.observacoes.extend(f"{m.isoformat()[:7]}: {o}" for m, o in sorted(obs_por_mes.items()))
    buraco_nf: dict = {}
    if numeracao_nf is not None:
        buraco_nf, lote.numeracao_nf = buracos_numeracao_nf(numeracao_nf)

    for k in set(cmv_idx) - set(esp):
        lote.fora_do_escopo.append(("cmv",) + k)
    for k in set(imp_idx) - set(esp):
        lote.fora_do_escopo.append(("impostos",) + k)
    usar_impostos = impostos is not None
    if usar_impostos:
        imp_recusas.extend(recusas_impostos(imp_idx, cmv_idx))
        if imp_recusas:
            usar_impostos = False
            lote.impostos_recusas = imp_recusas
            lote.impostos_estado = "recusado: " + "; ".join(imp_recusas[:5]) + (
                f" (e mais {len(imp_recusas) - 5})" if len(imp_recusas) > 5 else "")
        else:
            lote.impostos_estado = "publicado"
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
            if m in obs_por_mes:
                motivo = f"{motivo}; {obs_por_mes[m]}"
            motivo_rf = "base da razao com o GMV"
            if (m, marca) in buraco_nf:
                motivo = f"{motivo}; {buraco_nf[(m, marca)]}"
                motivo_rf = f"{motivo_rf}; {buraco_nf[(m, marca)]}"
            data_max = _ts(c["ultima_emissao"])
            lote.linhas.append(_linha(m, canal, marca, "cmv", custo, status=status, procedencia=PROC_CMV, sinal=1,
                                      cobertura=cobertura, provisorio=share, motivo=motivo, data_max=data_max))
            lote.linhas.append(_linha(m, canal, marca, "cmv_receita_fiscal", receita_fiscal, status="nao_avaliado",
                                      procedencia=PROC_CMV, motivo=motivo_rf, data_max=data_max))

        if canal == "shopee" and taxa_shopee is not None:
            lote.linhas.append(_linha_taxa_shopee(m, marca, txs_idx.get((m, marca)), lote))
        if usar_impostos:
            lote.linhas.extend(_linhas_impostos(m, canal, marca, imp_idx.get((m, canal, marca)), lote))
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


def _linha_taxa_shopee(m: date, marca: str, r: Optional[dict], lote: Lote) -> dict:
    """Linha de `TAXA_SHOPEE` de uma celula Shopee esperada.

    Sem pedido com escrow na origem -> `valor NULL`, `ausente_na_origem`
    (nunca zero). Com escrow, `valor` e' a soma medida (custo POSITIVO, como o
    `total_fees` da Shopee) e `cobertura` = pedidos com escrow / pedidos nao
    cancelados; a DRE decide `disponivel`/`parcial` por ela. Parcela negativa
    no escrow recusa o lote: sinal inesperado nao e' corrigido aqui.
    """
    if r is None or not int(r["pedidos_com_escrow"] or 0):
        return _linha(m, "shopee", marca, TAXA_SHOPEE, None, status="ausente_na_origem",
                      procedencia=PROC_TAXA_SHOPEE, motivo="sem pedido com escrow para a chave na origem")
    pedidos, com = int(r["pedidos"] or 0), int(r["pedidos_com_escrow"])
    if int(r["parcelas_negativas"] or 0):
        lote.recusas.append(f"taxa_shopee: {r['parcelas_negativas']} pedido(s) com parcela negativa no escrow "
                            f"em {(m, marca)}")
    cobertura = _razao(Decimal(com), Decimal(pedidos))
    if cobertura is None or not (Decimal(0) <= cobertura <= Decimal(1)):
        lote.recusas.append(f"taxa_shopee: cobertura fora de [0,1] em {(m, marca)}: {com}/{pedidos}")
        cobertura = None
    return _linha(m, "shopee", marca, TAXA_SHOPEE, _dec(r["taxa"]), status="nao_avaliado",
                  procedencia=PROC_TAXA_SHOPEE, sinal=1,
                  cobertura=None if cobertura is None else cobertura.quantize(Decimal("0.000001")),
                  motivo=f"escrow em {com} de {pedidos} pedidos nao cancelados",
                  data_max=_ts(r["atualizado_em"]))


#: Componente -> (coluna de `SQL_IMPOSTOS`, codigo do webgex, para o motivo).
_COLUNA_IMPOSTO = {IMPOSTO_ICMS: ("icms", "ICM"), IMPOSTO_DIFAL: ("difal", "ICM/valor_difal"),
                   IMPOSTO_PIS_COFINS: ("pis_cofins", "PIS/COF")}


def janela_webgex(comps: list[date]) -> tuple[date, date]:
    """`[inicio, fim)` em `data` do webgex: primeira competencia - 7 dias ate'
    o 1o dia do mes seguinte a' ultima + 7 dias. So' filtro de varredura."""
    return comps[0] - WEBGEX_FOLGA, somar_meses(comps[-1], 1) + WEBGEX_FOLGA


def _cobertura_imposto(r: dict) -> Optional[Decimal]:
    return _razao(_dec(r["receita_com_imposto"]), _dec(r["receita_fiscal"]))


def recusas_impostos(imp_idx: dict, cmv_idx: dict) -> list[str]:
    """Recusas da leitura de impostos (T-0069), em TODA celula lida.

      * chave de NF em mais de uma celula: o imposto dela seria somado duas
        vezes;
      * receita fiscal diferente da do CMV (mesmos filtros): as duas leituras
        viram instantes diferentes da gold, e a cobertura nao seria a do CMV;
      * imposto negativo numa celula: debito de venda e' custo; sinal
        inesperado nao e' corrigido aqui;
      * cobertura fora de [0, 1].
    """
    recusas: list[str] = []
    for k in sorted(imp_idx, key=lambda x: (x[0], str(x[1]), x[2])):
        r = imp_idx[k]
        if int(r["chaves_ambiguas"] or 0):
            recusas.append(f"impostos: {r['chaves_ambiguas']} item(ns) de NF cuja chave cai em mais de uma celula "
                           f"em {k}; o imposto seria duplicado")
        c = cmv_idx.get(k)
        rf_imp = _dec(r["receita_fiscal"]) or Decimal(0)
        rf_cmv = (_dec(c["receita_fiscal"]) or Decimal(0)) if c is not None else None
        if rf_cmv is None or abs(rf_imp - rf_cmv) > CONTRAPROVA_MAX:
            recusas.append(f"impostos: receita fiscal {k} diverge da do CMV "
                           f"({rf_imp} x {'sem CMV' if rf_cmv is None else rf_cmv}); leituras em instantes diferentes")
        for comp, (col, _cod) in _COLUNA_IMPOSTO.items():
            v = _dec(r[col])
            if v is not None and v < 0:
                recusas.append(f"impostos: {comp} negativo em {k}: {v}")
        cob = _cobertura_imposto(r)
        if cob is not None and not (Decimal(0) <= cob <= Decimal(1)):
            recusas.append(f"impostos: cobertura fora de [0,1] em {k}: {cob}")
    return recusas


def motivos_parcial_imposto(m: date, canal: str, marca: str, cobertura: Optional[Decimal]) -> list[str]:
    """Trechos `imposto parcial: ...` de uma celula COM imposto casado. PURA.

    Nunca muda o valor: o imposto publicado e' sempre o medido nas NFs casadas.
    """
    trechos = []
    if cobertura is None or cobertura < IMPOSTO_COBERTURA_MINIMA:
        pct = "desconhecida" if cobertura is None else f"{cobertura:.4f}"
        trechos.append(f"{IMPOSTO_PARCIAL}cobertura {pct} da receita fiscal, minimo {IMPOSTO_COBERTURA_MINIMA}")
    if canal == "shopee":
        trechos.append(IMPOSTO_PARCIAL_SHOPEE)
    desde = IMPOSTO_BASE_PENDENTE_FISCAL.get(marca)
    if desde is not None and m >= desde:
        trechos.append(IMPOSTO_PARCIAL_BASE)
    return trechos


def _linhas_impostos(m: date, canal: str, marca: str, r: Optional[dict], lote: Lote) -> list[dict]:
    """As tres linhas de imposto de uma celula esperada (T-0069).

    * sem NF de venda, ou nenhuma NF casada no webgex -> `valor NULL`,
      `ausente_na_origem` nos tres (nulo != zero; nada inventado);
    * com NF casada -> o valor medido (custo POSITIVO, `sinal_origem = 1`).
      Zero medido (PIS/COFINS CST 04) e' zero. Componente sem nenhuma linha do
      seu codigo nas NFs casadas -> NULL, nao zero;
    * `cobertura` = receita das NFs casadas / receita fiscal da celula;
      `status = nao_avaliado` (como a `TAXA_SHOPEE`), com os trechos
      `imposto parcial: ...` no motivo quando cabe.
    """
    if r is None or not int(r["notas_com_imposto"] or 0):
        motivo = ("sem NF de venda para a chave na origem" if r is None
                  else f"nenhuma das {int(r['notas'] or 0)} NFs da chave casada no webgex (VX/DB)")
        return [_linha(m, canal, marca, comp, None, status="ausente_na_origem", procedencia=PROC_IMPOSTOS,
                       motivo=motivo) for comp in IMPOSTOS]
    notas, com = int(r["notas"] or 0), int(r["notas_com_imposto"])
    cobertura = _cobertura_imposto(r)
    if cobertura is not None and not (Decimal(0) <= cobertura <= Decimal(1)):
        cobertura = None  # inalcancavel: `recusas_impostos` ja' teria descartado os impostos
    base = (f"imposto de {com} de {notas} NFs casadas no webgex (VX/DB); "
            f"receita fiscal coberta = {'desconhecida' if cobertura is None else f'{cobertura:.4f}'}")
    parciais = motivos_parcial_imposto(m, canal, marca, cobertura)
    if parciais:
        lote.impostos_parciais.append({"competencia": m.isoformat()[:7], "canal": canal, "marca": marca,
                                       "motivos": [p[len(IMPOSTO_PARCIAL):] for p in parciais]})
    motivo = "; ".join([base] + parciais)
    data_max = _ts(r["atualizado_em"])
    cob = None if cobertura is None else cobertura.quantize(Decimal("0.000001"))
    linhas = []
    for comp in IMPOSTOS:
        col, cod = _COLUNA_IMPOSTO[comp]
        v = _dec(r[col])
        if v is None:
            linhas.append(_linha(m, canal, marca, comp, None, status="ausente_na_origem", procedencia=PROC_IMPOSTOS,
                                 cobertura=cob, motivo=f"NFs casadas sem linha {cod} no webgex", data_max=data_max))
            continue
        linhas.append(_linha(m, canal, marca, comp, v, status="nao_avaliado", procedencia=PROC_IMPOSTOS, sinal=1,
                             cobertura=cob, motivo=motivo, data_max=data_max))
    return linhas


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def _rows(conn, sql, params) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).mappings().all()]


def ler_fonte(fonte_engine, comps: list[date], canais: list[str], marcas: list[str],
              com_taxa_shopee: bool = False) -> tuple[list, list, list, Optional[list]]:
    """Leitura do Data Mart em transacao READ ONLY. Falha vira FonteIndisponivel.

    A taxa da Shopee so' e' lida com `com_taxa_shopee` (CHECK do Neon aceita o
    componente) e a Shopee no escopo; senao volta `None` e o lote nao a monta.
    Os impostos (T-0069) tem leitura propria, que nunca derruba o lote:
    `ler_impostos`.
    """
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
            taxa_shopee = (_rows(conn, SQL_TAXA_SHOPEE, params)
                           if com_taxa_shopee and "shopee" in canais else None)
            conn.rollback()
    except SQLAlchemyError as exc:
        raise FonteIndisponivel(sanitizar(exc)) from None
    return cmv, contraprova, repasse, taxa_shopee


def ler_impostos(fonte_engine, comps: list[date], canais: list[str], marcas: list[str],
                 aceita: bool) -> tuple[Optional[list], str]:
    """Impostos (T-0069): leitura PROPRIA, READ ONLY, com timeout.

    Como o gate de numeracao (T-0064), nunca derruba a publicacao:
      * `aceita` falso (CHECK do Neon sem os tres componentes) -> nao le';
        `(None, "inerte: ...")`;
      * erro de SQL (timeout, tabela ausente...) -> `(None, "indisponivel:
        <classe>")`, so' a classe do erro, sem o texto (nada de DSN);
      * ok -> `(linhas, "lido")`; a montagem decide `publicado`/`recusado`.
    So' `SQLAlchemyError` e' engolido; qualquer outra excecao sobe.
    """
    if not aceita:
        return None, estado_impostos(None)
    if fonte_engine is None or not marcas:
        return None, "nao avaliado: sem Data Mart ou sem marca no escopo"
    inicio_webgex, fim_webgex = janela_webgex(comps)
    params = {
        "competencias": comps, "plataformas": [PLATAFORMA_FISCAL[c] for c in canais], "marcas": marcas,
        "inicio_webgex": inicio_webgex, "fim_webgex": fim_webgex,
    }
    try:
        with fonte_engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '600s'"))
            linhas = _rows(conn, SQL_IMPOSTOS, params)
            conn.rollback()
    except SQLAlchemyError as exc:
        return None, f"indisponivel: {type(exc).__name__}"
    return linhas, "lido"


def ler_numeracao_nf(fonte_engine, comps: list[date], marcas: list[str]) -> tuple[Optional[list], str]:
    """Gate de numeracao de NF (T-0064): leitura PROPRIA, READ ONLY, com timeout.

    Conexao separada de `ler_fonte` de proposito: o gate so' acrescenta motivo
    e NUNCA bloqueia a publicacao. Erro de SQL vira `(None, "indisponivel:
    ...")`, o lote sai como antes e o resumo diz que o gate nao rodou. So'
    `SQLAlchemyError` e' engolido; qualquer outra excecao sobe.
    """
    if fonte_engine is None or not marcas:
        return None, "nao avaliado: sem Data Mart ou sem marca no escopo"
    params = {
        "competencias": comps, "marcas": marcas, "minimo": BURACO_NF_MINIMO,
        "meses_numeracao": [somar_meses(comps[0], -1)] + list(comps),
        "inicio": comps[0], "fim_exclusivo": somar_meses(comps[-1], 1),
    }
    try:
        with fonte_engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '600s'"))
            linhas = _rows(conn, SQL_NUMERACAO_NF, params)
            conn.rollback()
    except SQLAlchemyError as exc:
        return None, f"indisponivel: {sanitizar(exc)}"
    return linhas, "avaliado"


def _resumo_numeracao(lote: Lote, estado: str) -> dict:
    return {"estado": estado, **lote.numeracao_nf}


def estado_taxa_shopee(taxa_shopee: Optional[list], canais: list[str]) -> str:
    """Rotulo do resumo: o componente entrou no lote ou ficou inerte, e por que."""
    if "shopee" not in canais:
        return "fora do escopo"
    if taxa_shopee is None:
        return f"inerte: o CHECK ck_fdim_componente do Neon ainda nao aceita {TAXA_SHOPEE} (migration pendente)"
    return "publicado"


def estado_impostos(impostos: Optional[list]) -> str:
    """Rotulo do resumo para os impostos (T-0069): publicados ou inertes, e por que."""
    if impostos is None:
        return ("inerte: o CHECK ck_fdim_componente do Neon ainda nao aceita " + ", ".join(IMPOSTOS)
                + " (migration 040 pendente)")
    return "publicado"


def _resumo_impostos(lote: Lote, estado_leitura: str) -> dict:
    """Estado final: o da montagem (`publicado`/`recusado: ...`) ou, sem
    leitura, o de `ler_impostos` (`inerte: ...`/`indisponivel: ...`)."""
    return {"estado": lote.impostos_estado or estado_leitura, "parciais": lote.impostos_parciais,
            "recusas": lote.impostos_recusas}


def check_aceita_taxa_shopee(conn) -> bool:
    """True quando o CHECK de componentes do Neon ja lista `TAXA_SHOPEE`."""
    definicao = conn.execute(SQL_CHECK_COMPONENTE).scalar()
    return bool(definicao) and f"'{TAXA_SHOPEE}'" in definicao


def check_aceita_impostos(conn) -> bool:
    """True quando o CHECK de componentes do Neon ja lista OS TRES impostos (040)."""
    definicao = conn.execute(SQL_CHECK_COMPONENTE).scalar()
    return bool(definicao) and all(f"'{c}'" in definicao for c in IMPOSTOS)


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
        "observacoes": lote.observacoes,
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


def excecoes_autorizadas(sem_vendas: frozenset, remover: frozenset) -> Optional[str]:
    """Texto auditavel das excecoes do lote; None quando nao ha."""
    partes = []
    if sem_vendas:
        partes.append("sem_vendas=" + ",".join(sorted(f"{m.isoformat()[:7]}:{c}" for m, c in sem_vendas)))
    if remover:
        partes.append("remover_marca=" + ",".join(sorted(rotulo_chave(k) for k in remover)))
    return ("excecoes autorizadas: " + "; ".join(partes)) if partes else None


def diagnosticar(alvo_engine, fonte_engine, comps: list[date], canais: list[str],
                 sem_vendas: frozenset = frozenset(), remover: frozenset = frozenset()) -> dict:
    """Dry-run: le as duas pontas, monta, reconcilia e compara. ZERO escrita."""
    with alvo_engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        esperado = _esperado(conn, comps, canais)
        existe = conn.execute(SQL_TABELA_EXISTE).scalar_one()
        atual = _rows(conn, SQL_ATUAL, {"competencias": comps, "canais": canais}) if existe else []
        aceita_tx = bool(existe) and check_aceita_taxa_shopee(conn)
        aceita_imp = bool(existe) and check_aceita_impostos(conn)
        conn.rollback()
    cmv, contraprova, repasse, taxa_shopee = ler_fonte(fonte_engine, comps, canais, _marcas(esperado), aceita_tx)
    impostos, estado_imp = ler_impostos(fonte_engine, comps, canais, _marcas(esperado), aceita_imp)
    numeracao, estado_nf = ler_numeracao_nf(fonte_engine, comps, _marcas(esperado))
    publicadas = frozenset((_mes_de(r["competencia"]), r["canal"], r["marca"]) for r in atual)
    lote = montar_lote(esperado=esperado, cmv=cmv, contraprova=contraprova, repasse=repasse, canais=canais,
                       comps=comps, sem_vendas=sem_vendas, publicadas=publicadas, remover=remover,
                       taxa_shopee=taxa_shopee, numeracao_nf=numeracao, impostos=impostos)
    return _resumo(lote, comps, canais, modo="dry-run", aplicado=False, tabela_existe=bool(existe),
                   taxa_shopee=estado_taxa_shopee(taxa_shopee, canais),
                   impostos=_resumo_impostos(lote, estado_imp),
                   numeracao_nf=_resumo_numeracao(lote, estado_nf),
                   sem_vendas=sorted(f"{m.isoformat()[:7]}:{c}" for m, c in sem_vendas),
                   remover_marca=sorted(rotulo_chave(k) for k in remover),
                   diff=_diff(atual, lote.linhas) if existe else None), lote


def publicar(alvo_engine, fonte_engine, comps: list[date], canais: list[str], *,
             sem_vendas: frozenset = frozenset(),
             remover: frozenset = frozenset(),
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
            # Fotografia atual lida DENTRO da transacao e sob o lock: e' contra
            # ela que a guarda por marca compara o novo universo.
            publicadas = frozenset(
                (_mes_de(r["competencia"]), r["canal"], r["marca"])
                for r in _rows(conn, SQL_PUBLICADAS, {"competencias": comps, "canais": canais}))
            cmv, contraprova, repasse, taxa_shopee = ler_fonte(fonte_engine, comps, canais, _marcas(esperado),
                                                               check_aceita_taxa_shopee(conn))
            impostos, estado_imp = ler_impostos(fonte_engine, comps, canais, _marcas(esperado),
                                                check_aceita_impostos(conn))
            numeracao, estado_nf = ler_numeracao_nf(fonte_engine, comps, _marcas(esperado))
            lote = montar_lote(esperado=esperado, cmv=cmv, contraprova=contraprova, repasse=repasse, canais=canais,
                               comps=comps, sem_vendas=sem_vendas, publicadas=publicadas, remover=remover,
                               taxa_shopee=taxa_shopee, numeracao_nf=numeracao, impostos=impostos)
            if lote.recusas:  # ANTES do DELETE: recusa nunca apaga a fotografia
                raise PublicacaoRecusada(lote.recusas)
            apagadas = conn.execute(SQL_APAGA_ESCOPO, {"competencias": comps, "canais": canais}).rowcount
            for ln in lote.linhas:
                conn.execute(SQL_INSERE, {**ln, "publicado_em": agora, "lote_id": lote_id})
            fechou = conn.execute(SQL_AUDIT_FECHA, {"agora": agora, "extraidas": lote.extraidas,
                                                    "gravadas": len(lote.linhas), "lote": lote_id,
                                                    "excecoes": excecoes_autorizadas(sem_vendas, remover)}).rowcount
            if fechou != 1:
                raise RuntimeError(f"auditoria do lote {lote_id} nao fechou ({fechou} linha[s])")
            if antes_do_commit is not None:
                antes_do_commit(conn)
    except BaseException as exc:
        _registrar_falha(alvo_engine, comps, exc, agora)
        raise
    return _resumo(lote, comps, canais, modo="apply", aplicado=True, lote_id=lote_id, apagadas=apagadas,
                   taxa_shopee=estado_taxa_shopee(taxa_shopee, canais),
                   impostos=_resumo_impostos(lote, estado_imp),
                   numeracao_nf=_resumo_numeracao(lote, estado_nf),
                   sem_vendas=sorted(f"{m.isoformat()[:7]}:{c}" for m, c in sem_vendas),
                   remover_marca=sorted(rotulo_chave(k) for k in remover))


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
    """`YYYY-MM` -> dia 1. Qualquer outra forma levanta ValueError."""
    partes = s.strip().split("-")
    if len(partes) != 2 or len(partes[0]) != 4 or len(partes[1]) != 2:
        raise ValueError(f"competencia invalida {s!r}: use YYYY-MM")
    return date(int(partes[0]), int(partes[1]), 1)


_MARCA_VALIDA = re.compile(r"^[a-z0-9_]+$")


def validar_argumentos(args, hoje: date) -> tuple[list[date], list[str], frozenset, frozenset]:
    """Escopo do CLI. Levanta ValueError com mensagem clara; nunca devolve escopo vazio."""
    if (args.de is None) != (args.ate is None):
        raise ValueError("--de e --ate vao juntos")
    if args.de is not None:
        de, ate = _parse_mes(args.de), _parse_mes(args.ate)
        if de > ate:
            raise ValueError(f"--de {args.de} e' posterior a --ate {args.ate}")
        comps = competencias(de, ate)
    else:
        if args.meses is None or args.meses <= 0:
            raise ValueError(f"--meses precisa ser inteiro positivo (veio {args.meses})")
        comps = janela_padrao(hoje, args.meses)
    if comps[-1] >= mes(hoje):
        raise ValueError("a competencia corrente ainda nao fechou; publique so' meses anteriores")

    tokens = [t.strip() for t in (args.canais or "").split(",") if t.strip()]
    if not tokens:
        raise ValueError("--canais vazio: informe ao menos um de " + ", ".join(CANAIS))
    desconhecidos = sorted(set(tokens) - set(CANAIS))
    if desconhecidos:
        raise ValueError(f"canal desconhecido: {', '.join(desconhecidos)}; validos: {', '.join(CANAIS)}")
    canais = [c for c in CANAIS if c in tokens]

    sem_vendas = set()
    for item in args.sem_vendas or []:
        if ":" not in item:
            raise ValueError(f"--sem-vendas {item!r}: use YYYY-MM:canal")
        m_txt, canal = item.split(":", 1)
        m = _parse_mes(m_txt)
        if canal not in canais:
            raise ValueError(f"--sem-vendas {item!r}: canal fora de --canais")
        if m not in comps:
            raise ValueError(f"--sem-vendas {item!r}: competencia fora da janela publicada")
        sem_vendas.add((m, canal))

    remover = set()
    for item in getattr(args, "remover_marca", None) or []:
        partes = item.split(":")
        if len(partes) != 3:
            raise ValueError(f"--remover-marca {item!r}: use YYYY-MM:canal:marca")
        m_txt, canal, marca = partes
        m = _parse_mes(m_txt)
        if canal not in canais:
            raise ValueError(f"--remover-marca {item!r}: canal fora de --canais")
        if m not in comps:
            raise ValueError(f"--remover-marca {item!r}: competencia fora da janela publicada")
        if not _MARCA_VALIDA.match(marca):
            raise ValueError(f"--remover-marca {item!r}: marca invalida (use o brand_key, ex.: kokeshi)")
        if (m, canal) in sem_vendas:
            raise ValueError(f"--remover-marca {item!r}: o par ja esta declarado em --sem-vendas")
        remover.add((m, canal, marca))
    return comps, canais, frozenset(sem_vendas), frozenset(remover)


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
    ap.add_argument("--sem-vendas", action="append", metavar="YYYY-MM:canal",
                    help="declara mes/canal legitimamente sem receita na Torre (repetivel)")
    ap.add_argument("--remover-marca", action="append", metavar="YYYY-MM:canal:marca",
                    help="autoriza apagar uma marca ja publicada que saiu do mes/canal (repetivel, auditado)")
    ap.add_argument("--apply", action="store_true", help="escreve no Neon; sem ele e' dry-run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    hoje = hoje or datetime.now(TZ_BRT).date()
    try:
        comps, canais, sem_vendas, remover = validar_argumentos(args, hoje)
    except ValueError as exc:
        ap.error(str(exc))

    alvo, fonte = (engines or _engines)()
    try:
        if args.apply:
            resumo = publicar(alvo, fonte, comps, canais, sem_vendas=sem_vendas, remover=remover)
        else:
            resumo, _ = diagnosticar(alvo, fonte, comps, canais, sem_vendas=sem_vendas, remover=remover)
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
