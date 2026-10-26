"""Тесты чистых функций аналитики — без HTTP и базы данных."""
import datetime as dt

import pytest

from app.models import Kind
from app.schemas import MonthStats
from app.services.analytics import Tx, budget_split, forecast, linear_fit, monthly


def test_budget_split():
    # Доход 100 000 ₽ (в копейках). Еда — needs, кафе — wants, вклад — savings, «хобби» нет в словаре → wants
    split = budget_split(100_000_00, {"еда": 40_000_00, "кафе": 15_000_00, "хобби": 5_000_00,
                                      "вклад": 10_000_00})
    # savings = 10 000 на вкладе + 30 000 непотраченных (100 000 − 70 000 расходов)
    assert split == {"needs": 0.4, "wants": 0.2, "savings": 0.4}


def test_budget_split_overspending_and_no_income():
    # Потратили больше, чем заработали: сбережения отрицательные, доли всё равно в сумме дают 1
    assert budget_split(1000, {"еда": 1500}) == {"needs": 1.5, "wants": 0.0, "savings": -0.5}
    assert budget_split(0, {"еда": 1500}) is None


def test_linear_fit_on_exact_line():
    # y = 10 + 5x → наклон 5, сдвиг 10
    a, b = linear_fit([10, 15, 20, 25])
    assert (round(a, 6), round(b, 6)) == (10, 5)


def test_linear_fit_constant_series():
    assert linear_fit([7, 7, 7]) == (7, 0)


def _months(incomes, expenses, start=(2026, 1)):
    y, m = start
    out = []
    for inc, exp in zip(incomes, expenses):
        out.append(MonthStats(month=f"{y:04d}-{m:02d}", income=inc, expense=exp, net=inc - exp))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def test_forecast_follows_trend_with_zero_noise():
    hist = _months([10000, 12000, 14000, 16000], [5000, 5000, 5000, 5000])
    f = forecast(hist, horizon=2, today=dt.date(2026, 6, 15))
    assert [p.month for p in f.points] == ["2026-05", "2026-06"]
    assert [p.income for p in f.points] == [18000, 20000]
    assert f.points[0].net == 13000
    assert f.points[0].net_low == f.points[0].net_high == 13000  # нет шума → коридор нулевой


def test_forecast_ignores_current_incomplete_month():
    hist = _months([10000, 10000, 10000, 500], [0, 0, 0, 0])  # апрель только начался
    f = forecast(hist, horizon=1, today=dt.date(2026, 4, 3))
    assert f.based_on_months == 3 and f.points[0].income == 10000


def test_forecast_interval_widens_and_clamps_negative():
    hist = _months([9000, 11000, 8000, 12000, 3000, 1000], [4000, 4000, 4000, 4000, 4000, 4000])
    f = forecast(hist, horizon=3, today=dt.date(2026, 12, 1))
    widths = [p.net_high - p.net_low for p in f.points]
    assert widths[0] < widths[1] < widths[2]          # чем дальше, тем неопределённее
    assert all(p.income >= 0 for p in f.points)       # доход в минус не уходит


def test_forecast_too_short():
    with pytest.raises(ValueError, match="минимум 3"):
        forecast(_months([1, 2], [0, 0]), today=dt.date(2026, 12, 1))


def test_monthly_handles_year_boundary():
    txs = [Tx(dt.date(2025, 12, 5), Kind.income, 100_00, "x"), Tx(dt.date(2026, 2, 5), Kind.expense, 50_00, "y")]
    assert [m.month for m in monthly(txs)] == ["2025-12", "2026-01", "2026-02"]
