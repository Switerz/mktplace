"""
Validações de qualidade aplicadas após o transform,
antes do upsert em fact_marketplace_daily_performance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class CheckResult:
    name: str
    status: str               # 'pass' | 'fail' | 'warn'
    severity: str             # 'critical' | 'high' | 'medium' | 'low'
    failed_rows: int = 0
    details: str = ""


def run_all(rows: list[dict]) -> list[CheckResult]:
    results = [
        _check_gmv_non_negative(rows),
        _check_date_valid(rows),
        _check_required_keys(rows),
        _check_loja_id_valid(rows),
        _check_marketplace_id_valid(rows),
        _check_orders_non_negative(rows),
        _check_no_duplicate_keys(rows),
    ]
    return results


def _check_gmv_non_negative(rows: list[dict]) -> CheckResult:
    bad = [r for r in rows if r.get("gmv") is not None and r["gmv"] < 0]
    return CheckResult(
        name="gmv_non_negative",
        status="fail" if bad else "pass",
        severity="critical",
        failed_rows=len(bad),
        details=f"Linhas com GMV < 0: {_sample_keys(bad)}" if bad else "",
    )


def _check_date_valid(rows: list[dict]) -> CheckResult:
    MIN_DATE = date(2024, 1, 1)
    MAX_DATE = date(2030, 12, 31)
    bad = [
        r for r in rows
        if not isinstance(r.get("date"), date)
        or not (MIN_DATE <= r["date"] <= MAX_DATE)
    ]
    return CheckResult(
        name="date_valid_range",
        status="fail" if bad else "pass",
        severity="critical",
        failed_rows=len(bad),
        details=f"Datas inválidas: {_sample_keys(bad)}" if bad else "",
    )


def _check_required_keys(rows: list[dict]) -> CheckResult:
    required = ("date", "loja_id", "marketplace_id", "empresa_id")
    bad = [
        r for r in rows
        if any(r.get(k) is None for k in required)
    ]
    return CheckResult(
        name="required_keys_present",
        status="fail" if bad else "pass",
        severity="critical",
        failed_rows=len(bad),
        details=f"Chaves obrigatórias ausentes: {_sample_keys(bad)}" if bad else "",
    )


def _check_loja_id_valid(rows: list[dict]) -> CheckResult:
    valid_ids = {1, 2, 3, 4, 5}
    bad = [r for r in rows if r.get("loja_id") not in valid_ids]
    return CheckResult(
        name="loja_id_valid",
        status="fail" if bad else "pass",
        severity="high",
        failed_rows=len(bad),
        details=f"loja_id fora do escopo: {_sample_keys(bad)}" if bad else "",
    )


def _check_marketplace_id_valid(rows: list[dict]) -> CheckResult:
    valid_ids = {1, 2, 3, 4, 5}
    bad = [r for r in rows if r.get("marketplace_id") not in valid_ids]
    return CheckResult(
        name="marketplace_id_valid",
        status="fail" if bad else "pass",
        severity="high",
        failed_rows=len(bad),
        details=f"marketplace_id inválido: {_sample_keys(bad)}" if bad else "",
    )


def _check_orders_non_negative(rows: list[dict]) -> CheckResult:
    bad = [r for r in rows if r.get("orders") is not None and r["orders"] < 0]
    return CheckResult(
        name="orders_non_negative",
        status="fail" if bad else "pass",
        severity="high",
        failed_rows=len(bad),
        details=f"orders < 0: {_sample_keys(bad)}" if bad else "",
    )


def _check_no_duplicate_keys(rows: list[dict]) -> CheckResult:
    seen: set[tuple] = set()
    dupes: list[dict] = []
    for r in rows:
        key = (r.get("date"), r.get("loja_id"), r.get("marketplace_id"))
        if key in seen:
            dupes.append(r)
        else:
            seen.add(key)
    return CheckResult(
        name="no_duplicate_keys",
        status="fail" if dupes else "pass",
        severity="critical",
        failed_rows=len(dupes),
        details=f"Chaves duplicadas no batch: {_sample_keys(dupes)}" if dupes else "",
    )


def has_critical_failure(results: list[CheckResult]) -> bool:
    return any(r.status == "fail" and r.severity == "critical" for r in results)


def _sample_keys(rows: list[dict], n: int = 3) -> str:
    sample = rows[:n]
    return str([
        {"date": r.get("date"), "loja_id": r.get("loja_id"), "mkt": r.get("marketplace_id")}
        for r in sample
    ])
