"""Gate S3 — testes das migrations 009, 010 e 011.

Nenhum DDL e' executado e nenhuma conexao e' aberta. Os modulos de migration sao
importados (o que prova que o import nao tem efeito colateral) e o texto de
`upgrade()`/`downgrade()` e' inspecionado para provar que cada revisao toca
SOMENTE os objetos que declarou criar.

O que estes testes protegem, concretamente:

- cadeia linear com head unico — duas migrations com o mesmo `down_revision`
  produziriam branch e `alembic upgrade head` falharia;
- ausencia de `IF NOT EXISTS` no CREATE — colisao tem de falhar alto;
- `downgrade()` restrito: uma revisao que derrubasse objeto de outra tornaria a
  reversao destrutiva;
- nenhum objeto Shopee, regional ou de outro gate e' tocado;
- 011 nasce anulavel e sem default, que e' o que preserva a diferenca entre
  "nao retroalimentado" e "zero medido".
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"

NOVAS = {
    "009": "009_create_fact_ml_cross_company_summary.py",
    "010": "010_create_fact_tiktok_channel_efficiency_daily.py",
    "011": "011_add_tiktok_product_content_metrics.py",
}

#: Objeto que CADA revisao esta autorizada a criar/alterar. Qualquer outro nome
#: qualificado que apareca no upgrade/downgrade reprova.
AUTORIZADO = {
    "009": {"marts.fact_ml_cross_company_summary"},
    "010": {"marts.fact_tiktok_channel_efficiency_daily", "marts.idx_ftced_brand_date"},
    "011": {"marts.fact_tiktok_product_daily"},
}

PROIBIDOS = (
    "shopee", "region", "regional", "dim_loja", "audit.", "raw.", "gold.",
    "fact_marketplace_daily_performance", "fact_ml_gestao_diaria",
    "fact_tiktok_brand_content_daily", "fact_tiktok_creator_daily",
    "alembic_version",
)


def _texto(rev: str) -> str:
    return (VERSOES / NOVAS[rev]).read_text(encoding="utf-8")


def _corpo(rev: str, funcao: str) -> str:
    """Corpo de `upgrade()` ou `downgrade()`, sem o docstring do modulo."""
    t = _texto(rev)
    i = t.index(f"def {funcao}(")
    resto = t[i:]
    outra = "downgrade" if funcao == "upgrade" else None
    if outra and f"def {outra}(" in resto:
        resto = resto[: resto.index(f"def {outra}(")]
    return resto


def _ast(rev: str) -> ast.Module:
    return ast.parse(_texto(rev))


def _meta(rev: str) -> dict:
    """Le `revision`/`down_revision`/`branch_labels`/`depends_on` pela AST.

    NAO importa o modulo de proposito. Nos testes deste repositorio `apps/api`
    esta no `sys.path`, e o diretorio `apps/api/alembic/` SOMBREIA o pacote
    `alembic` instalado — um import direto quebraria em `from alembic import op`
    por um motivo que nada tem a ver com a migration. Na operacao real quem
    importa estes modulos e' o proprio alembic, com o pacote verdadeiro ja
    carregado, e o sombreamento nao acontece.

    Ler pela AST tambem prova de forma MAIS forte o que o teste quer: nada e'
    executado, entao nenhum efeito colateral e' possivel.
    """
    fora = {}
    for node in _ast(rev).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            alvo = node.targets[0]
            if isinstance(alvo, ast.Name) and isinstance(node.value, ast.Constant):
                fora[alvo.id] = node.value.value
    return fora


def _corpo_sem_comentarios_sql(rev: str, funcao: str) -> str:
    """Corpo da funcao sem os blocos `COMMENT ON`.

    Os COMMENT documentam a linhagem ("copia de gold.v_channel_efficiency"), e
    citar a fonte num comentario nao e' tocar a fonte. Sem esta remocao o check
    de objetos proibidos casaria com a propria documentacao da regra.
    """
    corpo = _corpo(rev, funcao)
    fora, pular = [], False
    for linha in corpo.splitlines():
        if "COMMENT ON" in linha:
            pular = True
        if pular:
            if linha.strip().endswith('""")'):
                pular = False
            continue
        fora.append(linha)
    return "\n".join(fora)


# ===========================================================================
# Cadeia
# ===========================================================================

@pytest.mark.parametrize("rev,anterior", [("009", "008"), ("010", "009"), ("011", "010")])
def test_m01_cadeia_linear_008_009_010_011(rev, anterior):
    meta = _meta(rev)
    assert meta["revision"] == rev
    assert meta["down_revision"] == anterior
    assert meta["branch_labels"] is None
    assert meta["depends_on"] is None


def test_m02_head_unico_em_todas_as_revisoes():
    """Nenhum `down_revision` repetido: revisao apontada duas vezes vira branch,
    e `alembic upgrade head` passa a exigir escolha manual."""
    revisoes, filhos = {}, {}
    for arq in sorted(VERSOES.glob("*.py")):
        t = arq.read_text(encoding="utf-8")
        r = re.search(r'^revision = "([^"]+)"', t, re.M)
        d = re.search(r'^down_revision = (?:"([^"]+)"|None)', t, re.M)
        if not r or not d:
            continue
        revisoes[r.group(1)] = arq.name
        pai = d.group(1)
        filhos.setdefault(pai, []).append(r.group(1))
    duplicados = {p: f for p, f in filhos.items() if p is not None and len(f) > 1}
    assert not duplicados, f"branch na cadeia: {duplicados}"
    heads = [r for r in revisoes if r not in filhos]
    # Head avancado de 011 para 012 pelo Gate UE2-B, que acrescentou
    # marts.fact_tiktok_affiliate_cost_order_monthly; e de 012 para 013 pelo
    # Gate UE8-I1, que acrescentou marts.fact_tiktok_order_discounts_daily. O
    # pino literal e' proposital: forca uma revisao consciente a cada migration
    # nova, em vez de aceitar qualquer head em silencio.
    assert heads == ["013"], f"head deveria ser unico e igual a 013, veio {heads}"


def test_m03_as_tres_revisoes_novas_existem_uma_vez_cada():
    for rev, nome in NOVAS.items():
        assert (VERSOES / nome).exists(), nome
        iguais = [a.name for a in VERSOES.glob("*.py")
                  if re.search(rf'^revision = "{rev}"', a.read_text(encoding="utf-8"), re.M)]
        assert iguais == [nome], f"revision {rev} declarada em {iguais}"


def test_m04_modulo_nao_executa_nada_no_import():
    """Nivel de modulo contem SOMENTE docstring, imports, atribuicoes de metadado
    e as duas funcoes. Nenhuma chamada, logo nenhum efeito colateral possivel."""
    for rev in NOVAS:
        arvore = _ast(rev)
        funcoes = set()
        for node in arvore.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue                      # docstring do modulo
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign)):
                continue                      # imports e metadados
            if isinstance(node, ast.FunctionDef):
                funcoes.add(node.name)
                continue
            pytest.fail(f"{rev}: statement inesperado no nivel de modulo: "
                        f"{ast.dump(node)[:80]}")
        assert funcoes == {"upgrade", "downgrade"}, f"{rev}: {funcoes}"
        # nenhuma chamada de funcao no nivel de modulo
        for node in arvore.body:
            for filho in ast.walk(node) if not isinstance(node, ast.FunctionDef) else []:
                assert not isinstance(filho, ast.Call), f"{rev}: chamada no topo"


def test_m04b_nenhuma_conexao_ou_leitura_declarada():
    for rev in NOVAS:
        corpo = _corpo(rev, "upgrade") + _corpo(rev, "downgrade")
        assert "psycopg2" not in corpo
        assert "create_engine" not in corpo
        assert "SELECT" not in corpo.upper()


# ===========================================================================
# upgrade restrito aos objetos autorizados
# ===========================================================================

@pytest.mark.parametrize("rev", sorted(NOVAS))
def test_m05_upgrade_toca_somente_objetos_autorizados(rev):
    corpo = _corpo(rev, "upgrade")
    qualificados = set(re.findall(r"\bmarts\.[a-z_0-9]+", corpo))
    assert qualificados <= AUTORIZADO[rev], f"{rev} toca {qualificados - AUTORIZADO[rev]}"


@pytest.mark.parametrize("rev", sorted(NOVAS))
def test_m06_downgrade_toca_somente_objetos_autorizados(rev):
    corpo = _corpo(rev, "downgrade")
    qualificados = set(re.findall(r"\bmarts\.[a-z_0-9]+", corpo))
    assert qualificados <= AUTORIZADO[rev], f"{rev} toca {qualificados - AUTORIZADO[rev]}"


@pytest.mark.parametrize("rev", sorted(NOVAS))
def test_m07_nenhum_objeto_de_outro_gate_e_tocado(rev):
    """Shopee, regional, audit, e as fatos de gates anteriores ficam fora."""
    corpo = (_corpo_sem_comentarios_sql(rev, "upgrade")
             + _corpo_sem_comentarios_sql(rev, "downgrade")).lower()
    autorizados_baixo = {a.lower() for a in AUTORIZADO[rev]}
    for proibido in PROIBIDOS:
        if any(proibido in a for a in autorizados_baixo):
            continue  # 011 legitimamente altera fact_tiktok_product_daily
        assert proibido not in corpo, f"{rev} menciona objeto proibido {proibido!r}"


@pytest.mark.parametrize("rev", ["009", "010"])
def test_m08_create_table_sem_if_not_exists(rev):
    """Colisao tem de falhar alto, como em 006/007/008."""
    corpo = _corpo(rev, "upgrade").upper()
    assert "CREATE TABLE" in corpo
    assert "CREATE TABLE IF NOT EXISTS" not in corpo


def test_m09_downgrades_usam_if_exists_para_serem_idempotentes():
    """No caminho de reversao o IF EXISTS e' desejavel: derrubar duas vezes nao
    pode explodir. E' o inverso do CREATE."""
    for rev in NOVAS:
        corpo = _corpo(rev, "downgrade").upper()
        assert "IF EXISTS" in corpo, rev


# ===========================================================================
# 009 — snapshot ML
# ===========================================================================

def test_m10_009_grao_pk_e_nove_colunas_de_negocio():
    corpo = _corpo("009", "upgrade")
    assert "PRIMARY KEY (brand)" in corpo
    for col in ("total_buyers", "repeat_buyers", "repeat_rate_pct", "avg_customer_ltv",
                "vip_buyers", "one_and_done_buyers", "at_risk_or_churned", "overall_roas"):
        assert re.search(rf"^\s+{col}\s+\w+", corpo, re.M), col
    assert "synced_at            TIMESTAMPTZ NOT NULL" in corpo
    assert "source_run_id        TEXT        NOT NULL" in corpo


def test_m11_009_nao_tem_dimensao_temporal():
    """Snapshot sem data: fabricar `date` mentiria sobre o grao da fonte."""
    corpo = _corpo("009", "upgrade")
    assert not re.search(r"^\s+date\s+DATE", corpo, re.M)
    assert "date_column" not in corpo


def test_m12_009_numericos_sem_escala_para_nao_arredondar():
    corpo = _corpo("009", "upgrade")
    for col in ("repeat_rate_pct", "avg_customer_ltv", "overall_roas"):
        assert re.search(rf"{col}\s+NUMERIC,", corpo), f"{col} deveria ser NUMERIC sem escala"
    assert "NUMERIC(" not in corpo


def test_m13_009_check_de_nan_explicito_em_todas_as_numericas():
    """`'NaN'::numeric >= 0` e' TRUE em Postgres: o CHECK de nao-negatividade
    sozinho nao barra NaN."""
    corpo = _corpo("009", "upgrade")
    for col in ("repeat_rate_pct", "avg_customer_ltv", "overall_roas"):
        assert re.search(rf"{col}\s+<> 'NaN'", corpo), col


def test_m14_009_checks_de_nao_negatividade_nas_contagens():
    corpo = _corpo("009", "upgrade")
    for col in ("total_buyers", "repeat_buyers", "vip_buyers",
                "one_and_done_buyers", "at_risk_or_churned"):
        assert re.search(rf"CHECK \({col}\s+>= 0\)", corpo), col


def test_m15_009_sem_indice_extra_com_quatro_linhas():
    assert "CREATE INDEX" not in _corpo("009", "upgrade")


# ===========================================================================
# 010 — channel efficiency
# ===========================================================================

def test_m16_010_grao_pk_e_indice():
    corpo = _corpo("010", "upgrade")
    assert "PRIMARY KEY (date, brand, channel)" in corpo
    assert "CREATE INDEX idx_ftced_brand_date" in corpo
    assert "(brand, date)" in corpo


def test_m17_010_colunas_exatas():
    corpo = _corpo("010", "upgrade")
    for col in ("date", "brand", "channel", "impressions", "page_views",
                "items_sold", "gmv", "synced_at", "source_run_id"):
        assert re.search(rf"^\s+{col}\s+\w+", corpo, re.M), col


def test_m18_010_nao_guarda_razao_derivada():
    """`ctr_pct` e `cvr_pct` sao calculados na consulta da rota. Guardar aqui
    duplicaria a regra e permitiria divergencia."""
    corpo = _corpo("010", "upgrade")
    assert "ctr_pct" not in corpo.replace("ctr_pct/cvr_pct", "")
    assert "cvr_pct" not in corpo.replace("ctr_pct/cvr_pct", "")


def test_m19_010_checks_validados_contra_a_fonte():
    corpo = _corpo("010", "upgrade")
    assert re.search(r"impressions\s+>= 0 AND impressions\s+<> 'NaN'", corpo)
    assert re.search(r"gmv\s+>= 0 AND gmv\s+<> 'NaN'", corpo)
    assert re.search(r"CHECK \(page_views\s+>= 0\)", corpo)
    assert re.search(r"CHECK \(items_sold\s+>= 0\)", corpo)


def test_m20_010_sem_coluna_de_pii():
    corpo = _corpo("010", "upgrade").lower()
    for termo in ("creator", "handle", "buyer", "email", "phone", "cpf", "customer_name"):
        assert termo not in corpo, termo


# ===========================================================================
# 011 — duas colunas do produto TikTok
# ===========================================================================

def test_m21_011_adiciona_exatamente_duas_colunas():
    corpo = _corpo("011", "upgrade")
    adds = re.findall(r"ADD COLUMN (\w+)", corpo)
    assert adds == ["active_videos", "video_views"], adds


def test_m22_011_colunas_anulaveis_e_sem_default():
    """NOT NULL falharia sobre as 213 mil linhas existentes, e DEFAULT 0 apagaria
    a diferenca entre nao-retroalimentado e zero medido."""
    corpo = _corpo("011", "upgrade")
    assert "NOT NULL" not in corpo
    assert "DEFAULT" not in corpo.upper()


def test_m23_011_downgrade_remove_somente_as_duas_colunas():
    corpo = _corpo("011", "downgrade")
    drops = re.findall(r"DROP COLUMN IF EXISTS (\w+)", corpo)
    assert sorted(drops) == ["active_videos", "video_views"], drops
    assert "DROP TABLE" not in corpo.upper()


def test_m24_011_nao_altera_pk_nem_outras_colunas():
    corpo = _corpo("011", "upgrade").upper()
    for proibido in ("DROP COLUMN", "ALTER COLUMN", "PRIMARY KEY", "DROP CONSTRAINT",
                     "ADD CONSTRAINT", "RENAME"):
        assert proibido not in corpo, proibido


def test_m25_011_nao_toca_gmv_frete_nem_filtro():
    corpo = (_corpo("011", "upgrade") + _corpo("011", "downgrade")).lower()
    for termo in ("gmv", "frete", "shipping", "sub_total", "where", "brand in"):
        assert termo not in corpo, termo


# ===========================================================================
# Nenhuma migration foi aplicada nesta task
# ===========================================================================

def test_m26_nenhuma_das_tres_declara_execucao_automatica():
    """Nada de `if __name__ == "__main__"` ou chamada direta de upgrade()."""
    for rev in NOVAS:
        t = _texto(rev)
        assert "__main__" not in t
        assert re.search(r"^upgrade\(\)", t, re.M) is None
        assert re.search(r"^downgrade\(\)", t, re.M) is None


def test_m27_as_tres_documentam_que_nao_foram_aplicadas():
    for rev in NOVAS:
        t = _texto(rev).upper()
        assert "NAO APLICADA NESTA TASK" in t, rev
