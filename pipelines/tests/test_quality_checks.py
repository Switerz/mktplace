from datetime import date

from pipelines.quality import checks


def _row(**overrides):
    row = {
        "date": date(2026, 6, 1),
        "loja_id": 1,
        "marketplace_id": 1,
        "empresa_id": 1,
        "gmv": 100.0,
        "orders": 5,
    }
    row.update(overrides)
    return row


def test_gmv_negative_falha_critica():
    results = checks.run_all([_row(gmv=-1.0)])
    gmv_check = next(r for r in results if r.name == "gmv_non_negative")
    assert gmv_check.status == "fail"
    assert gmv_check.severity == "critical"
    assert checks.has_critical_failure(results)


def test_gmv_positivo_passa():
    results = checks.run_all([_row(gmv=10.0)])
    gmv_check = next(r for r in results if r.name == "gmv_non_negative")
    assert gmv_check.status == "pass"


def test_data_fora_do_intervalo_falha():
    results = checks.run_all([_row(date=date(2999, 1, 1))])
    date_check = next(r for r in results if r.name == "date_valid_range")
    assert date_check.status == "fail"
    assert checks.has_critical_failure(results)


def test_chave_obrigatoria_ausente_falha():
    row = _row()
    del row["loja_id"]
    results = checks.run_all([row])
    key_check = next(r for r in results if r.name == "required_keys_present")
    assert key_check.status == "fail"
    assert checks.has_critical_failure(results)


def test_loja_id_fora_do_escopo_falha_mas_nao_e_critico():
    results = checks.run_all([_row(loja_id=999)])
    loja_check = next(r for r in results if r.name == "loja_id_valid")
    assert loja_check.status == "fail"
    assert loja_check.severity == "high"
    # loja_id invalido nao e' 'critical' isoladamente — nao deve abortar a carga
    assert not checks.has_critical_failure(results)


def test_orders_negativo_falha():
    results = checks.run_all([_row(orders=-3)])
    orders_check = next(r for r in results if r.name == "orders_non_negative")
    assert orders_check.status == "fail"


def test_chaves_duplicadas_no_batch_falha():
    rows = [_row(), _row()]  # mesma date/loja_id/marketplace_id
    results = checks.run_all(rows)
    dup_check = next(r for r in results if r.name == "no_duplicate_keys")
    assert dup_check.status == "fail"
    assert dup_check.failed_rows == 1
    assert checks.has_critical_failure(results)


def test_batch_vazio_passa_todos_os_checks():
    results = checks.run_all([])
    assert all(r.status == "pass" for r in results)
    assert not checks.has_critical_failure(results)


def test_batch_valido_passa_sem_falhas_criticas():
    rows = [_row(loja_id=1), _row(loja_id=2, date=date(2026, 6, 2))]
    results = checks.run_all(rows)
    assert not checks.has_critical_failure(results)
