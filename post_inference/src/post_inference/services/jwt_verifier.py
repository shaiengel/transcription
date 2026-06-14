import logging

import jwt
import requests

logger = logging.getLogger(__name__)


class JWTVerifier:
    """Verifies Gemini webhook JWT signatures using Google's JWKS endpoint."""

    _jwks_cache: dict | None = None

    def __init__(self, jwks_url: str, audience: str):
        self._jwks_url = jwks_url
        self._audience = audience

    def verify(self, token: str) -> dict:
        """Verify RS256 JWT. Returns decoded payload or raises InvalidTokenError."""
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            raise jwt.exceptions.InvalidTokenError("No kid in JWT header")

        public_key = self._get_public_key(kid)
        if not public_key:
            JWTVerifier._jwks_cache = None
            public_key = self._get_public_key(kid)
            if not public_key:
                raise jwt.exceptions.InvalidTokenError(
                    f"No matching public key for kid: {kid}"
                )

        return jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=self._audience,
        )

    def _get_public_key(self, kid: str):
        jwks = self._fetch_jwks()
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        return None

    def _fetch_jwks(self) -> dict:
        if JWTVerifier._jwks_cache is not None:
            return JWTVerifier._jwks_cache

        logger.info("Fetching JWKS from %s", self._jwks_url)
        response = requests.get(self._jwks_url, timeout=10)
        response.raise_for_status()
        JWTVerifier._jwks_cache = response.json()
        return JWTVerifier._jwks_cache
