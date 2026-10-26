"""Модели — описание таблиц базы данных в виде Python-классов (ORM).

ORM (Object-Relational Mapping) позволяет работать со строками таблицы как с объектами:
tx.amount_kop вместо row[3], а SQL-запросы SQLAlchemy составит сама.
"""
import datetime as dt
from enum import Enum

from sqlalchemy import CheckConstraint, Date, DateTime, Index, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Kind(str, Enum):
    """Тип операции. Наследование от str — чтобы значение красиво выглядело в JSON: "income"."""
    income = "income"
    expense = "expense"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Тип пишем как dt.date, а не date: поле тоже называется date, и одинаковые имена
    # в аннотации путают Python (особенно в 3.14, где аннотации вычисляются отложенно)
    date: Mapped[dt.date] = mapped_column(Date, index=True)  # индекс: почти все отчёты фильтруют по дате
    kind: Mapped[Kind] = mapped_column(SAEnum(Kind, native_enum=False, length=10))

    # ДЕНЬГИ ХРАНИМ В КОПЕЙКАХ, ЦЕЛЫМ ЧИСЛОМ. Почему не float:
    # в двоичной арифметике 0.1 + 0.2 = 0.30000000000000004, и копейки начинают «гулять» в суммах.
    # Целые числа складываются точно. В рубли переводим только при выводе (schemas.py).
    amount_kop: Mapped[int] = mapped_column(Integer)

    category: Mapped[str] = mapped_column(String(50))           # еда, транспорт, фриланс…
    source: Mapped[str | None] = mapped_column(String(50))      # для доходов: kwork, стипендия, работа
    # Сколько часов ушло на этот доход — из этого считаем «рублей в час» по каждому источнику.
    # Mapped[float | None] → колонка может быть пустой (NULL)
    hours: Mapped[float | None]
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))

    __table_args__ = (
        # Проверка на уровне базы: даже если кто-то обойдёт API и запишет напрямую,
        # отрицательная сумма не пройдёт. Знак задаёт kind, а не минус в сумме.
        CheckConstraint("amount_kop > 0", name="amount_positive"),
        Index("ix_transactions_kind_date", "kind", "date"),
    )
