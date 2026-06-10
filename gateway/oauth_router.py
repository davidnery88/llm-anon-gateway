"""Phase 4.6 — endpoint OAuth 2.0 client_credentials grant (RFC 6749 §4.4).

Émet des access tokens JWT signés HS256 que le sidecar (et autres clients M2M)
peuvent utiliser pour s'authentifier auprès du gateway. Coexiste avec l'auth
bearer-key legacy via la table `api_keys` pendant la transition.

Pour la production, on bascule sur un IdP externe (Azure AD, Keycloak) en
pointant simplement OAUTH_JWKS_URL côté gateway au lieu de signer nous-mêmes —
le validateur JWT (gateway/auth.py) sait gérer les deux modes.

Endpoint :
    POST /oauth/token
    Content-Type: application/x-www-form-urlencoded
    body: grant_type=client_credentials&client_id=X&client_secret=Y[&scope=...]

Réponse RFC-compliant :
    {
      "access_token": "<JWT>",
      "token_type":   "Bearer",
      "expires_in":   3600,
      "scope":        "kb:read classify:write"
    }
"""
from __future__ import annotations

import hashlib
import os
import time

import jwt
from fastapi import APIRouter, Form, Header, HTTPException, Request, status
from pydantic import BaseModel

ISSUER = "anon-gateway"
ALGORITHM = "HS256"
DEFAULT_TTL_SECONDS = 3600

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _signing_key() -> str:
    key = os.environ.get("OAUTH_SIGNING_KEY", "")
    if not key:
        # Fail-safe : pas de clé → on refuse de signer. Évite des tokens
        # signés avec une chaîne vide (acceptée par PyJWT sans erreur).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAUTH_SIGNING_KEY not configured on gateway",
        )
    return key


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str


class SidecarTokenResponse(BaseModel):
    sidecar_token: str
    expires_in: int


@router.post("/sidecar_token", response_model=SidecarTokenResponse)
async def issue_sidecar_token(
    request: Request,
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_bearer_token"},
        )

    bearer = authorization[len("Bearer "):]
    try:
        claims = jwt.decode(bearer, _signing_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "token_expired"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token"},
        )

    scopes = set(claims.get("scope", "").split())
    if "sidecar:token" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "insufficient_scope", "required": "sidecar:token"},
        )

    client_id = claims.get("sub", "")
    now = int(time.time())
    sidecar_claims = {
        "iss": ISSUER,
        "sub": client_id,
        "aud": "anon-sidecar",
        "scope": "sidecar:access",
        "iat": now,
        "exp": now + DEFAULT_TTL_SECONDS,
        "nbf": now,
    }
    token = jwt.encode(sidecar_claims, _signing_key(), algorithm=ALGORITHM)

    return SidecarTokenResponse(
        sidecar_token=token,
        expires_in=DEFAULT_TTL_SECONDS,
    )


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    scope: str | None = Form(None),
):
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unsupported_grant_type"},
        )

    row = await request.app.state.db_pool.fetchrow(
        """
        SELECT client_secret_hash, scopes
        FROM oauth_clients
        WHERE client_id = $1 AND active = TRUE
        """,
        client_id,
    )
    if row is None or row["client_secret_hash"] != _hash_secret(client_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_client"},
        )

    granted_scopes = scope.strip() if scope else row["scopes"]
    requested = set(granted_scopes.split())
    allowed = set(row["scopes"].split())
    if not requested.issubset(allowed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_scope"},
        )

    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": client_id,
        "aud": "anon-gateway",
        "scope": " ".join(sorted(requested)),
        "iat": now,
        "exp": now + DEFAULT_TTL_SECONDS,
        "nbf": now,
    }
    token = jwt.encode(claims, _signing_key(), algorithm=ALGORITHM)

    # Best-effort tracking de la dernière utilisation
    await request.app.state.db_pool.execute(
        "UPDATE oauth_clients SET last_used_at = NOW() WHERE client_id = $1",
        client_id,
    )

    return TokenResponse(
        access_token=token,
        expires_in=DEFAULT_TTL_SECONDS,
        scope=claims["scope"],
    )
