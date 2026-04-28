from datetime import datetime, timezone
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import ApiKey
from app.security import verify_secret


bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return "***"
    return f"{token[:6]}...{token[-4:]}"


def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> ApiKey:
    if credentials is None:
        logger.warning(
            "api_key_auth_failed reason=missing_token path=%s method=%s client=%s",
            request.url.path,
            request.method,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")

    raw_key = credentials.credentials
    prefix = raw_key[:12]
    # Include rows where prefix is NULL — these are keys that pre-date the
    # prefix column and need backfilling on first successful auth.
    keys = db.scalars(
        select(ApiKey).where(
            or_(ApiKey.prefix == prefix, ApiKey.prefix.is_(None)),
            ApiKey.is_active.is_(True),
        )
    ).all()
    logger.info(
        "api_key_auth_attempt path=%s method=%s client=%s token=%s prefix=%s candidates=%d",
        request.url.path,
        request.method,
        request.client.host if request.client else "unknown",
        _mask_token(raw_key),
        prefix,
        len(keys),
    )

    for api_key in keys:
        if verify_secret(raw_key, api_key.key_hash):
            key_name = api_key.name
            api_key.last_used_at = datetime.now(timezone.utc)
            if api_key.prefix is None:
                api_key.prefix = prefix  # backfill once
            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                # Do not fail the request if audit metadata writeback races.
                logger.warning(
                    "api_key_auth_writeback_failed key_name=%s path=%s method=%s client=%s error=%s",
                    key_name,
                    request.url.path,
                    request.method,
                    request.client.host if request.client else "unknown",
                    str(exc),
                )
            logger.info(
                "api_key_auth_success key_name=%s path=%s method=%s client=%s",
                key_name,
                request.url.path,
                request.method,
                request.client.host if request.client else "unknown",
            )
            return api_key

    logger.warning(
        "api_key_auth_failed reason=no_hash_match path=%s method=%s client=%s token=%s prefix=%s candidates=%d",
        request.url.path,
        request.method,
        request.client.host if request.client else "unknown",
        _mask_token(raw_key),
        prefix,
        len(keys),
    )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
