"""Импорт операций из CSV — например, из выгрузки банка или старой таблицы.

Поддерживаем два вида файлов:
1) с колонкой kind (income / expense) и положительной суммой;
2) без kind, со знаком у суммы: «-350,00» — расход, «1500» — доход (так выгружают многие банки).

Каждая строка проверяется той же схемой TransactionIn, что и запрос к API.
Одна логика проверки на всё приложение — нельзя «протащить» мусор через импорт.
Битые строки не роняют импорт: они пропускаются и перечисляются в ответе.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from ..models import Kind
from ..schemas import TransactionIn

DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")


def parse_date(value: str) -> dt.date:
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"неизвестный формат даты «{value}»")


def parse_amount(value: str) -> Decimal:
    # «1 499,90» → «1499.90»: убираем пробелы (в т.ч. неразрывные) и меняем запятую на точку
    cleaned = value.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"не число: «{value}»") from None


def parse_csv(content: bytes) -> tuple[list[TransactionIn], list[str]]:
    """Возвращает (корректные операции, описания ошибок)."""
    # utf-8-sig — понимает и обычный UTF-8, и файл из Excel с невидимым BOM в начале
    text = content.decode("utf-8-sig")
    # Sniffer угадывает разделитель: русский Excel сохраняет CSV через «;», остальные — через «,»
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames or not {"date", "amount", "category"} <= {f.strip() for f in reader.fieldnames}:
        return [], ["нужны колонки date, amount, category (и необязательные kind, source, hours, note)"]

    items, errors = [], []
    # start=2: первая строка файла — заголовки, поэтому данные начинаются со строки 2 (как в Excel)
    for line_no, raw in enumerate(reader, start=2):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        try:
            amount = parse_amount(row["amount"])
            kind = row.get("kind") or (Kind.expense if amount < 0 else Kind.income)
            items.append(TransactionIn(
                date=parse_date(row["date"]), kind=kind, amount=abs(amount),
                category=row["category"], source=row.get("source") or None,
                hours=float(row["hours"].replace(",", ".")) if row.get("hours") else None,
                note=row.get("note") or None,
            ))
        except (ValueError, ValidationError) as e:
            # ValidationError от pydantic многословен — берём первое сообщение
            msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
            errors.append(f"строка {line_no}: {msg}")
    return items, errors
