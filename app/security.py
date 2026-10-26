"""Защита API ключом в заголовке X-API-Key.

Без этого любой, кто узнает адрес сервера, увидит и удалит все твои операции.
Для личного сервиса ключа достаточно; для многопользовательского нужны учётные записи
и токены (OAuth2/JWT) — это следующий уровень.
"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from .config import Settings, get_settings

# auto_error=False: если заголовка нет, сами вернём понятную ошибку 401 вместо стандартной 403
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Depends(api_key_header),
                    settings: Settings = Depends(get_settings)) -> None:
    # compare_digest сравнивает строки за одинаковое время независимо от того, где первое различие.
    # Обычное == выходит на первом несовпавшем символе, и по времени ответа можно подбирать ключ
    # посимвольно (timing attack).
    if not key or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный или отсутствующий X-API-Key")
