"""Схемы — что API принимает и что отдаёт (pydantic-модели).

Почему схемы отдельно от моделей базы (models.py):
- клиент не должен присылать id и created_at — их назначает сервер;
- наружу отдаём рубли (1499.90), а в базе храним копейки (149990);
- можно поменять таблицу, не ломая контракт API для тех, кто им пользуется.

pydantic проверяет входные данные сам: сумма -5 или дата «вчера» вернут клиенту
ошибку 422 с объяснением, и до нашего кода такой запрос даже не дойдёт.
"""
from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Kind


def rub_to_kop(value: Decimal) -> int:
    # Decimal, а не float: Decimal("1499.90") хранит ровно то, что написано
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def kop_to_rub(value: int) -> float:
    return round(value / 100, 2)


class TransactionIn(BaseModel):
    """Тело запроса на создание операции."""
    date: Date
    kind: Kind
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="Сумма в рублях, > 0")
    category: str = Field(min_length=1, max_length=50)
    source: str | None = Field(default=None, max_length=50)
    hours: float | None = Field(default=None, gt=0, le=744, description="Сколько часов заняла работа")
    note: str | None = Field(default=None, max_length=300)

    @field_validator("category", "source")
    @classmethod
    def normalize(cls, v: str | None) -> str | None:
        # «Kwork», «kwork », «KWORK» — должна быть одна категория, иначе отчёты раздробятся
        return v.strip().lower() if v else v

    @model_validator(mode="after")
    def hours_only_for_income(self):
        # Валидатор всей модели: проверяет связь между полями
        if self.hours is not None and self.kind != Kind.income:
            raise ValueError("hours можно указывать только для доходов")
        return self


class TransactionOut(BaseModel):
    """Как операция выглядит в ответе API."""
    # from_attributes: pydantic умеет читать поля прямо из объекта SQLAlchemy
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: Date
    kind: Kind
    amount: float
    category: str
    source: str | None
    hours: float | None
    note: str | None

    @classmethod
    def from_model(cls, tx) -> "TransactionOut":
        return cls(id=tx.id, date=tx.date, kind=tx.kind, amount=kop_to_rub(tx.amount_kop),
                   category=tx.category, source=tx.source, hours=tx.hours, note=tx.note)


class TransactionUpdate(BaseModel):
    """Тело PATCH-запроса: клиент присылает только те поля, которые хочет поменять.

    Все поля необязательные (дефолт None), но проверки те же, что в TransactionIn:
    иначе через PATCH можно было бы записать то, что POST не пропустил бы (сумму -5 и т.п.).
    """
    date: Date | None = None
    kind: Kind | None = None
    # Decimal, а не float — как в TransactionIn: rub_to_kop ждёт Decimal, и копейки не «гуляют»
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    source: str | None = Field(default=None, max_length=50)
    hours: float | None = Field(default=None, gt=0, le=744)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("category", "source")
    @classmethod
    def normalize(cls, v: str | None) -> str | None:
        # Как в TransactionIn: «Kwork» и «kwork» — одна категория
        return v.strip().lower() if v else v

    @model_validator(mode="after")
    def required_fields_not_null(self):
        # Важно различать «поле не прислали» и «прислали null»:
        # - {"note": "кофе"}     → category не трогаем;
        # - {"category": null}   → попытка стереть категорию, а в базе колонка NOT NULL → упали бы с 500.
        # model_fields_set — множество полей, которые клиент реально прислал.
        for name in ("date", "kind", "amount", "category"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"поле {name} нельзя очистить")
        return self


class ImportResult(BaseModel):
    imported: int
    skipped: int                 # строки с ошибками (дата, сумма, …)
    duplicates: int = 0          # корректные строки, которые уже есть в базе — их не добавили второй раз
    errors: list[str]


class SourceStats(BaseModel):
    source: str
    income: float
    hours: float | None
    per_hour: float | None  # доход в час; None, если часы не указывали


class Summary(BaseModel):
    date_from: Date | None
    date_to: Date | None
    income: float
    expense: float
    net: float
    savings_rate: float | None  # доля дохода, которая осталась: (доход - расход) / доход
    expense_by_category: dict[str, float]
    income_by_source: list[SourceStats]
    budget_split: dict[str, float] | None  # доли дохода по правилу 50/30/20


class MonthStats(BaseModel):
    month: str  # «2026-10»
    income: float
    expense: float
    net: float


class ForecastPoint(BaseModel):
    month: str
    income: float
    expense: float
    net: float
    net_low: float   # нижняя граница коридора
    net_high: float  # верхняя граница коридора


class Forecast(BaseModel):
    method: str
    based_on_months: int
    points: list[ForecastPoint]
