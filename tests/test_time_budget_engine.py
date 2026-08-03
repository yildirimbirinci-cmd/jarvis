from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "core" / "time_budget_engine.py"
spec = importlib.util.spec_from_file_location("time_budget_engine", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_time_estimate_question_is_recognized() -> None:
    assert module.asks_for_time_estimate("Bunu yapman ne kadar sürer?")
    assert module.asks_for_time_estimate("Tahmini süre nedir?")
    assert not module.asks_for_time_estimate("Bu işlem çok sürdü")


def test_user_budget_is_parsed() -> None:
    assert module.parse_time_budget("1 saatin var") == 60
    assert module.parse_time_budget("En fazla 45 dakika içinde bitir") == 45
    assert module.parse_time_budget("2 saat 30 dakikan var") == 150
    assert module.parse_time_budget("Bu iki saat sürdü") is None


def test_estimate_has_uncertainty_range(tmp_path: Path) -> None:
    store = module.TimeBudgetStore(tmp_path / "time_budget.json")
    plan = store.estimate("Kodu düzelt, regresyon testlerini çalıştır ve doğrula")
    assert plan.estimate_low_minutes < plan.estimate_likely_minutes < plan.estimate_high_minutes
    report = plan.estimate_report()
    assert "En iyi durumda" in report
    assert "beklenmeyen sorun" in report


def test_one_hour_budget_creates_minimum_safe_delivery(tmp_path: Path) -> None:
    store = module.TimeBudgetStore(tmp_path / "time_budget.json")
    store.estimate("Tüm mimariyi geliştir, kodu uygula ve tam regresyon testlerini çalıştır")
    plan = store.apply_budget(60)
    assert plan is not None
    assert plan.strategy == "minimum güvenli teslimat"
    assert "Tam regresyon paketi" in plan.defer
    assert "Derleme ve odaklı testleri çalıştırmak" in plan.required_now
    report = plan.budget_report()
    assert "testleri sessizce atlamayacağım" in report


def test_tiny_budget_limits_work_to_diagnosis(tmp_path: Path) -> None:
    store = module.TimeBudgetStore(tmp_path / "time_budget.json")
    store.estimate("Tüm sistemi yeniden düzenle ve test et")
    plan = store.apply_budget(15)
    assert plan is not None
    assert plan.strategy == "teşhis ve uygulanabilir plan"
    assert "Kaynak kod değişikliği" in plan.defer


def test_plan_is_persisted(tmp_path: Path) -> None:
    store = module.TimeBudgetStore(tmp_path / "time_budget.json")
    original = store.estimate("Küçük bir kod düzeltmesi yap")
    updated = store.apply_budget(30)
    loaded = store.load()
    assert updated is not None
    assert loaded == updated
    assert loaded.plan_id == original.plan_id
