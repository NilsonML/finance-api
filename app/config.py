"""Настройки приложения.

pydantic-settings читает значения из переменных окружения и файла .env
и сразу проверяет типы: если в API_KEY пусто или в DATABASE_URL мусор —
приложение упадёт при запуске с понятной ошибкой, а не посреди работы.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file — откуда читать; extra="ignore" — лишние переменные в .env не считаются ошибкой
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite — один файл, сервер не нужен. Для PostgreSQL достаточно поменять строку:
    # postgresql+psycopg://user:password@host:5432/dbname
    database_url: str = "sqlite:///./data/finance.db"

    # Ключ доступа к API. Каждый запрос должен прислать его в заголовке X-API-Key.
    api_key: str = "change-me"
    
# Категория → группа правила 50/30/20. Категории пишем в нижнем регистре — так они хранятся в базе.
BUDGET_GROUPS = {
    "еда": "needs", "жильё": "needs", "транспорт": "needs", "связь": "needs",
    "кафе": "wants", "развлечения": "wants", "спорт": "wants", "такси": "wants",
    "вклад": "savings", "инвестиции": "savings",
}



@lru_cache  # настройки читаются один раз за всё время работы, а не на каждый запрос
def get_settings() -> Settings:
    return Settings()
