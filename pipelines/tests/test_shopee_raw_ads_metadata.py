"""
Testes do parser de preâmbulo de ads (Fase Staging Shopee 2A — Gate 2B,
revisão de 2026-07-06, 2ª rodada). Só dados SINTÉTICOS — nenhum arquivo
real, nenhuma marca/loja real.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from pipelines.ingestion.shopee_raw import ads_metadata
from pipelines.ingestion.shopee_raw.ads_metadata import (
    AdsPreambleError,
    parse_ads_preamble,
)

_VALID_PREAMBLE = [
    "Relatório de Todos os Anúncios CPC - Shopee Brasil",
    "Nome de Usuário,lojaexemplo",
    "Nome da loja,Loja Exemplo",
    "ID da Loja,999999999",
    "Data de Criação do Relatório,15/07/2026 10:30",
    "Período,01/01/2026 - 31/03/2026",
    "",
    "#,Nome do Anúncio,Status",
    "1,Anúncio Exemplo,Em Andamento",
]


def test_parse_preambulo_valido():
    meta = parse_ads_preamble(_VALID_PREAMBLE)
    assert meta.report_period_start == date(2026, 1, 1)
    assert meta.report_period_end == date(2026, 3, 31)
    assert meta.report_created_at == datetime(2026, 7, 15, 10, 30)
    assert meta.shop_id == "999999999"


def test_periodo_sem_ano_no_nome_de_arquivo_nao_importa_pois_le_do_preambulo():
    """O caso motivador: kokeshi tem nome de arquivo sem ano
    ('Dados+Gerais-01-01-19-03.csv'), mas o PREÂMBULO sempre tem a data
    completa — este parser nunca olha para o nome do arquivo."""
    preamble = list(_VALID_PREAMBLE)
    preamble[5] = "Período,20/03/2026 - 20/06/2026"
    meta = parse_ads_preamble(preamble)
    assert meta.report_period_start == date(2026, 3, 20)
    assert meta.report_period_end == date(2026, 6, 20)


def test_to_jsonb_dict_e_minimizado_sem_username_ou_nome_de_loja():
    """Minimização (revisão de 2026-07-06): só period_start/period_end/
    report_created_at/shop_id — shop_username/shop_display_name foram
    removidos, sem necessidade concreta identificada."""
    meta = parse_ads_preamble(_VALID_PREAMBLE)
    d = meta.to_jsonb_dict()
    assert d == {
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "report_created_at": "2026-07-15T10:30:00",
        "shop_id": "999999999",
    }
    assert "ads_shop_username" not in d
    assert "ads_shop_display_name" not in d
    assert "shop_username" not in d
    assert "shop_display_name" not in d
    for v in d.values():
        assert v is None or isinstance(v, str)


def test_periodo_ausente_falha_explicitamente():
    preamble = [l for l in _VALID_PREAMBLE if not l.startswith("Período,")]
    with pytest.raises(AdsPreambleError, match="Período"):
        parse_ads_preamble(preamble)


def test_data_criacao_ausente_falha_explicitamente():
    preamble = [l for l in _VALID_PREAMBLE if not l.startswith("Data de Criação")]
    with pytest.raises(AdsPreambleError, match="Criação"):
        parse_ads_preamble(preamble)


def test_periodo_formato_invalido_falha():
    preamble = list(_VALID_PREAMBLE)
    preamble[5] = "Período,não é uma data"
    with pytest.raises(AdsPreambleError):
        parse_ads_preamble(preamble)


def test_periodo_com_inicio_depois_do_fim_falha():
    preamble = list(_VALID_PREAMBLE)
    preamble[5] = "Período,31/03/2026 - 01/01/2026"
    with pytest.raises(AdsPreambleError, match="início posterior ao fim"):
        parse_ads_preamble(preamble)


def test_data_de_calendario_impossivel_falha():
    preamble = list(_VALID_PREAMBLE)
    preamble[5] = "Período,31/02/2026 - 31/03/2026"
    with pytest.raises(AdsPreambleError):
        parse_ads_preamble(preamble)


def test_hora_de_calendario_impossivel_falha():
    preamble = list(_VALID_PREAMBLE)
    preamble[4] = "Data de Criação do Relatório,15/07/2026 25:99"
    with pytest.raises(AdsPreambleError, match="Criação"):
        parse_ads_preamble(preamble)


def test_id_da_loja_e_obrigatorio_desde_a_revisao_de_3a_rodada():
    """Revisão de 2026-07-06 (3ª rodada): 'ID da Loja' deixou de ser
    melhor-esforço — está presente nos 10 arquivos reais auditados, então
    sua ausência agora falha explicitamente."""
    preamble = [l for l in _VALID_PREAMBLE if not l.startswith("ID da Loja")]
    with pytest.raises(AdsPreambleError, match="ID da Loja"):
        parse_ads_preamble(preamble)


def test_shop_id_deve_ser_somente_digitos():
    preamble = list(_VALID_PREAMBLE)
    preamble[3] = "ID da Loja,ABC123"
    with pytest.raises(AdsPreambleError, match="ID da Loja"):
        parse_ads_preamble(preamble)


def test_shop_id_com_digitos_e_aceito():
    meta = parse_ads_preamble(_VALID_PREAMBLE)
    assert meta.shop_id == "999999999"
    assert meta.shop_id.isdigit()


def test_linha_com_rotulo_conhecido_e_virgula_solta_fora_de_aspas_falha():
    """Revisão de 2026-07-06 (3ª rodada): uma vírgula solta (sem aspas) num
    rótulo CONHECIDO produz >2 campos via csv.reader e deve FALHAR — nunca
    mais remontada silenciosamente unindo os campos extras (comportamento
    antigo removido)."""
    preamble = list(_VALID_PREAMBLE)
    preamble[3] = "ID da Loja,999,999"  # vírgula solta, sem aspas -> 3 campos CSV
    with pytest.raises(AdsPreambleError, match="ID da Loja"):
        parse_ads_preamble(preamble)


def test_linha_com_rotulo_desconhecido_e_virgula_solta_nao_falha():
    """A regra de 'exatamente 2 campos' só vale para rótulos CONHECIDOS —
    uma linha com rótulo fora do catálogo pode ter qualquer formatação,
    já que nunca é usada."""
    preamble = list(_VALID_PREAMBLE)
    preamble.insert(1, "Campo Desconhecido,a,b,c")  # 4 campos, rótulo desconhecido
    meta = parse_ads_preamble(preamble)  # não deve levantar
    assert meta.report_period_start == date(2026, 1, 1)


def test_linhas_de_dados_apos_header_sao_ignoradas():
    """Uma linha de dado que contivesse, por coincidência, um rótulo
    conhecido (ex.: um Nome do Anúncio chamado 'Período') não deve ser
    interpretada como metadado — só linhas ANTES do header '#,' contam."""
    preamble = list(_VALID_PREAMBLE)
    preamble.append("2,Período,Pausado")  # linha de dado após o header
    meta = parse_ads_preamble(preamble)
    assert meta.report_period_start == date(2026, 1, 1)  # não sobrescrito


def test_rotulo_desconhecido_no_preambulo_e_ignorado_sem_erro():
    preamble = list(_VALID_PREAMBLE)
    preamble.insert(1, "Campo Futuro Desconhecido,algum valor")
    meta = parse_ads_preamble(preamble)
    assert meta.report_period_start == date(2026, 1, 1)


def test_nome_de_usuario_e_nome_de_loja_sao_ignorados_como_rotulo_desconhecido():
    """Minimização: esses dois rótulos não são mais 'conhecidos' — nem
    entram no resultado, nem disparam duplicidade se repetidos."""
    preamble = list(_VALID_PREAMBLE) + ["Nome de Usuário,outraloja"]
    meta = parse_ads_preamble(preamble)  # não deve levantar (rótulo ignorado)
    assert meta.report_period_start == date(2026, 1, 1)


def test_header_tabular_ausente_falha_explicitamente():
    preamble = [l for l in _VALID_PREAMBLE if not l.startswith("#,")]
    with pytest.raises(AdsPreambleError, match="cabeçalho tabular"):
        parse_ads_preamble(preamble)


def test_label_conhecida_duplicada_falha_mesmo_com_mesmo_valor():
    preamble = list(_VALID_PREAMBLE)
    preamble.insert(5, "Período,01/01/2026 - 31/03/2026")  # mesma label, mesmo valor
    with pytest.raises(AdsPreambleError, match="Período"):
        parse_ads_preamble(preamble)


def test_label_conhecida_duplicada_com_valor_conflitante_falha():
    preamble = list(_VALID_PREAMBLE)
    preamble.insert(5, "Período,01/04/2026 - 30/06/2026")  # mesma label, valor diferente
    with pytest.raises(AdsPreambleError, match="Período"):
        parse_ads_preamble(preamble)


def test_preambulo_com_valor_entre_aspas_contendo_virgula_e_interpretado_corretamente():
    """csv.reader (não partition por vírgula simples) — um valor entre
    aspas contendo vírgula não deve ser cortado no meio nem quebrar o
    parsing de outras linhas."""
    preamble = list(_VALID_PREAMBLE)
    preamble.insert(1, '"ID da Loja","999,999"')  # aspas + vírgula dentro do valor
    # 'ID da Loja' já existe em _VALID_PREAMBLE — isso é uma duplicidade
    # proposital para provar que o valor entre aspas foi lido como UM
    # campo (e não interpretado como duas colunas) antes de decidir se é
    # duplicidade.
    with pytest.raises(AdsPreambleError, match="ID da Loja"):
        parse_ads_preamble(preamble)


def test_parse_kv_lines_valor_entre_aspas_contendo_virgula_e_um_so_campo():
    """csv.reader (não partition por vírgula simples) — um valor entre
    aspas contendo vírgula é UM campo só, nunca dois. Testado direto em
    `_parse_kv_lines` (não via `parse_ads_preamble`) para não colidir com a
    validação de 'só dígitos' de shop_id, que é uma checagem posterior e
    independente da tokenização CSV em si."""
    kv = ads_metadata._parse_kv_lines(['"ID da Loja","999,999"'])
    assert kv["ID da Loja"] == "999,999"


def test_parse_kv_lines_usa_csv_reader_de_verdade_aspas_duplas_escapadas():
    """Regressão específica para csv.reader (não um partition/split com
    rejoin ingênuo): um valor com aspas duplas escapadas ("") só é
    interpretado corretamente por um parser CSV de verdade — um split
    ingênuo por vírgula deixaria as aspas literais no valor."""
    kv = ads_metadata._parse_kv_lines(['"ID da Loja","Loja ""Premium"" 123"'])
    assert kv["ID da Loja"] == 'Loja "Premium" 123'


def test_shop_id_com_virgula_ou_aspas_falha_na_validacao_de_digitos():
    """O valor entre aspas é corretamente tokenizado como 1 campo (teste
    acima) — mas ainda assim reprova a validação de 'só dígitos' de
    `parse_ads_preamble`, que é uma checagem semântica separada da
    tokenização CSV."""
    preamble = [l for l in _VALID_PREAMBLE if not l.startswith("ID da Loja")]
    preamble.insert(3, '"ID da Loja","999,999"')
    with pytest.raises(AdsPreambleError, match="ID da Loja"):
        parse_ads_preamble(preamble)


def test_erro_nunca_contem_valor_bruto_de_celula():
    preamble = list(_VALID_PREAMBLE)
    preamble[5] = "Período,ISTO_NAO_E_UMA_DATA_VALIDA"
    with pytest.raises(AdsPreambleError) as exc_info:
        parse_ads_preamble(preamble)
    assert "ISTO_NAO_E_UMA_DATA_VALIDA" not in str(exc_info.value)


def test_erro_de_calendario_impossivel_nunca_contem_valor_bruto_nem_no_context():
    """__context__ (encadeamento implícito de exceção) também não pode
    carregar o texto original: o parser valida por regex ANTES de
    construir date()/datetime(), então o único ValueError encadeado é o
    nativo de calendário (mensagem genérica, sem eco do valor)."""
    preamble = list(_VALID_PREAMBLE)
    preamble[5] = "Período,31/02/2026 - 31/03/2026"
    with pytest.raises(AdsPreambleError) as exc_info:
        parse_ads_preamble(preamble)
    exc = exc_info.value
    assert "31/02/2026" not in str(exc)
    if exc.__context__ is not None:
        assert "31/02/2026" not in str(exc.__context__)
    if exc.__cause__ is not None:
        assert "31/02/2026" not in str(exc.__cause__)


def test_shop_username_e_shop_display_name_nao_existem_mais_no_dataclass():
    meta = parse_ads_preamble(_VALID_PREAMBLE)
    assert not hasattr(meta, "shop_username")
    assert not hasattr(meta, "shop_display_name")
