"""Auth gateway — accepte deux schémas en parallèle pour la transition Phase 4.6 :

1. **JWT OAuth** (nouveau) : token émis par notre endpoint /oauth/token, signé HS256
   avec OAUTH_SIGNING_KEY. Détecté automatiquement (contient des points).
2. **API key legacy** : hash sha256 lookuppé dans la table api_keys. Maintenu
   tant qu'on n'a pas migré tous les clients vers OAuth.

L'identité retournée est `sub` du JWT (client_id) ou `user_id` (api_key).
"""
import hashlib
import os

import jwt
from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


class _BearerAuth(HTTPBearer):
    def make_not_authenticated_error(self) -> HTTPException:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authenticated")


security = _BearerAuth()


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _looks_like_jwt(token: str) -> bool:
    # JWT = header.payload.signature → 3 segments non-vides séparés par '.'
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


async def _validate_jwt(token: str) -> str:
    signing_key = os.environ.get("OAUTH_SIGNING_KEY", "")
    if not signing_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAUTH_SIGNING_KEY not configured",
        )
    try:
        claims = jwt.decode(
            token, signing_key, algorithms=["HS256"],
            audience="anon-gateway",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub")
    return sub


async def _validate_legacy_key(request: Request, token: str) -> str:
    key_hash = hash_key(token)
    row = await request.app.state.db_pool.fetchrow(
        "SELECT user_id FROM api_keys WHERE key_hash = $1 AND active = true",
        key_hash,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )
    return str(row["user_id"])


async def validate_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> str:
    """Dispatcher : JWT si possible, fallback sur API key legacy.
    Le nom de la fonction est gardé pour rétrocompat avec les routers existants.
    """
    token = credentials.credentials
    if _looks_like_jwt(token):
        return await _validate_jwt(token)
    return await _validate_legacy_key(request, token)
