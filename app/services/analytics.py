"""Аналитика: сводка, помесячная динамика, прогноз.

Все функции здесь «чистые»: принимают список операций и возвращают результат.
Ни базы, ни HTTP. Поэтому:
- их легко тестировать (подсунул список — проверил ответ);
- логику можно переиспользовать: в Telegram-боте, в скрипте, в Jupyter.
Получение данных из базы — забота роутеров (routers/stats.py).
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass

from ..config import BUDGET_GROUPS
from ..models import Kind
from ..schemas import Forecast, ForecastPoint, MonthStats, SourceStats, Summary, kop_to_rub


@dataclass
class Tx:
    """Минимальный набор полей операции, нужный аналитике.

    Свой класс вместо модели SQLAlchemy: аналитика не зависит от базы данных,
    и в тестах операции создаются одной строкой без всякой базы.
    """
    date: dt.date
    kind: Kind
    amount_kop: int
    category: str
    source: str | None = None
    hours: float | None = None


# ---------------------------------------------------------------- сводка
def summarize(txs: list[Tx], date_from: dt.date | None = None, date_to: dt.date | None = None) -> Summary:
    """Сводка, посчитанная в Python по готовому списку операций.

    Эндпоинт /stats/summary теперь считает её в базе (services/summary_sql.py),
    а эта версия осталась эталоном: тест сверяет с ней SQL-вариант.
    """
    income = sum(t.amount_kop for t in txs if t.kind == Kind.income)
    expense = sum(t.amount_kop for t in txs if t.kind == Kind.expense)

    by_category: dict[str, int] = defaultdict(int)  # defaultdict(int): новый ключ сразу = 0
    for t in txs:
        if t.kind == Kind.expense:
            by_category[t.category] += t.amount_kop

    # Доход и часы по источникам. Часы суммируем только по операциям, где они указаны,
    # и ставку считаем по тем же операциям — иначе доход без часов завысил бы «рублей в час».
    src_income: dict[str, int] = defaultdict(int)
    src_hours: dict[str, float] = defaultdict(float)
    src_paid_with_hours: dict[str, int] = defaultdict(int)
    for t in txs:
        if t.kind != Kind.income:
            continue
        name = t.source or "без источника"
        src_income[name] += t.amount_kop
        if t.hours:
            src_hours[name] += t.hours
            src_paid_with_hours[name] += t.amount_kop

    sources = [
        SourceStats(
            source=name,
            income=kop_to_rub(total),
            hours=round(src_hours[name], 2) if name in src_hours else None,
            per_hour=kop_to_rub(round(src_paid_with_hours[name] / src_hours[name])) if name in src_hours else None,
        )
        for name, total in src_income.items()
    ]
    sources.sort(key=lambda s: s.income, reverse=True)

    return Summary(
        date_from=date_from, date_to=date_to,
        income=kop_to_rub(income), expense=kop_to_rub(expense), net=kop_to_rub(income - expense),
        # Норма сбережений: какую долю дохода удалось не потратить. Без дохода не определена.
        savings_rate=round((income - expense) / income, 3) if income else None,
        # Категории расходов от больших к меньшим — так сразу видно, куда уходят деньги
        expense_by_category={k: kop_to_rub(v) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
        income_by_source=sources,
        budget_split=budget_split(income, by_category),
    )


def budget_split(income_kop: int, expense_by_category_kop: dict[str, int]) -> dict[str, float] | None:
    """Правило 50/30/20: какая доля дохода ушла на обязательное, на желания и в сбережения.

    Ориентир правила: needs ≈ 0.5, wants ≈ 0.3, savings ≈ 0.2.
    Принимает копейки, а не рубли: так нет ошибок округления до самого деления.
    Общая для обеих сводок (summarize и summarize_sql) — чтобы логика не разошлась.
    """
    if income_kop <= 0:
        return None  # доля от нуля не определена — как и savings_rate

    groups = {"needs": 0, "wants": 0, "savings": 0}
    for category, amount in expense_by_category_kop.items():
        # Категория, которой нет в словаре, — «желание»: если трата не обязательная, значит, по выбору
        groups[BUDGET_GROUPS.get(category, "wants")] += amount

    # Непотраченные деньги — тоже сбережения: доход минус все расходы.
    # Если потратили больше, чем заработали, это слагаемое отрицательное и честно уменьшает savings.
    # Благодаря этому три доли в сумме всегда дают 1 (с точностью до округления).
    groups["savings"] += income_kop - sum(expense_by_category_kop.values())

    return {name: round(amount / income_kop, 3) for name, amount in groups.items()}


# ---------------------------------------------------------------- по месяцам
def month_key(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def monthly(txs: list[Tx]) -> list[MonthStats]:
    """Доход, расход и остаток по каждому месяцу — от первой операции до последней.

    Месяцы БЕЗ операций тоже попадают в результат с нулями. Это важно для прогноза:
    если пропустить пустой месяц, тренд посчитается по «склеенному» ряду и будет неверным.
    """
    if not txs:
        return []
    inc: dict[str, int] = defaultdict(int)
    exp: dict[str, int] = defaultdict(int)
    for t in txs:
        (inc if t.kind == Kind.income else exp)[month_key(t.date)] += t.amount_kop

    first, last = min(t.date for t in txs), max(t.date for t in txs)
    result, (y, m) = [], (first.year, first.month)
    while (y, m) <= (last.year, last.month):  # кортежи сравниваются поэлементно: сначала год, потом месяц
        key = f"{y:04d}-{m:02d}"
        result.append(MonthStats(month=key, income=kop_to_rub(inc[key]), expense=kop_to_rub(exp[key]),
                                 net=kop_to_rub(inc[key] - exp[key])))
        y, m = next_month(y, m)
    return result


# ---------------------------------------------------------------- прогноз
def linear_fit(values: list[float]) -> tuple[float, float]:
    """Метод наименьших квадратов для прямой y = a + b·x, где x = 0, 1, 2, …

    Подбирает прямую, сумма квадратов отклонений от которой минимальна. Формулы:
        b (наклон)  = Σ(x - x̄)(y - ȳ) / Σ(x - x̄)²
        a (сдвиг)   = ȳ - b·x̄
    Наклон b — на сколько рублей в среднем меняется величина каждый месяц.
    """
    n = len(values)
    xs = range(n)
    x_mean, y_mean = (n - 1) / 2, sum(values) / n
    denom = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values)) / denom if denom else 0.0
    return y_mean - slope * x_mean, slope


def forecast(months: list[MonthStats], horizon: int = 3, today: dt.date | None = None,
             window: int = 12) -> Forecast:
    """Прогноз дохода, расхода и остатка на horizon месяцев вперёд.

    Как считаем:
    1. Текущий месяц не используем: он ещё не закончился, и его неполный доход
       выглядел бы как резкое падение и «утянул» бы тренд вниз.
    2. Берём последние window месяцев (по умолчанию год): давняя история хуже описывает настоящее.
    3. Доход и расход прогнозируем отдельно линейным трендом, остаток = доход − расход.
    4. Коридор неопределённости: насколько реальные остатки в прошлом отклонялись от тренда
       (стандартное отклонение остатков σ). Коридор ±1.28σ при нормальных ошибках
       накрывает около 80% случаев. Это грубая оценка, а не гарантия.
    """
    today = today or dt.date.today()
    history = [m for m in months if m.month < month_key(today)][-window:]
    if len(history) < 3:
        # По двум точкам любую прямую провести можно, но смысла в ней нет
        raise ValueError(f"Для прогноза нужно минимум 3 полных месяца истории, есть {len(history)}")

    inc_a, inc_b = linear_fit([m.income for m in history])
    exp_a, exp_b = linear_fit([m.expense for m in history])

    # Ошибки модели на истории: факт минус то, что дала бы прямая в том же месяце
    residuals = [m.net - ((inc_a + inc_b * i) - (exp_a + exp_b * i)) for i, m in enumerate(history)]
    # n - 2: две степени свободы «съели» два параметра прямой (a и b) — так оценка σ не занижается
    sigma = math.sqrt(sum(r * r for r in residuals) / max(len(residuals) - 2, 1))

    points = []
    y, m = map(int, history[-1].month.split("-"))
    for step in range(1, horizon + 1):
        y, m = next_month(y, m)
        x = len(history) - 1 + step
        # max(0, …): тренд может уйти в минус, но отрицательного дохода не бывает
        income = max(0.0, inc_a + inc_b * x)
        expense = max(0.0, exp_a + exp_b * x)
        net = income - expense
        # Чем дальше в будущее, тем шире коридор: ошибка накапливается (множитель √step)
        spread = 1.28 * sigma * math.sqrt(step)
        points.append(ForecastPoint(month=f"{y:04d}-{m:02d}", income=round(income, 2), expense=round(expense, 2),
                                    net=round(net, 2), net_low=round(net - spread, 2),
                                    net_high=round(net + spread, 2)))
    return Forecast(method="linear trend, 80% interval", based_on_months=len(history), points=points)