import logging

import jwt
import requests

logger = logging.getLogger(__name__)


class JWTVerifier:
    """Verifies Gemini webhook JWT signatures using Google's JWKS endpoint."""

    def __init__(self, jwks_url: str, audience: str):
        self._jwks_url = jwks_url
        self._audience = audience

    def verify(self, token: str) -> dict:
        """Verify RS256 JWT. Returns decoded payload or raises InvalidTokenError."""
        logger.debug("Verifying JWT token (audience=%s)", self._audience)
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            logger.error("JWT header missing 'kid' field. Header: %s", unverified_header)
            raise jwt.exceptions.InvalidTokenError("No kid in JWT header")

        logger.debug("JWT kid=%s, fetching public key", kid)
        public_key = self._get_public_key(kid)
        if not public_key:
            logger.error("kid=%s not found in JWKS. Available kids: %s",
                         kid, [k.get("kid") for k in self._fetch_jwks().get("keys", [])])
            raise jwt.exceptions.InvalidTokenError(f"No matching public key for kid: {kid}")

        logger.debug("Decoding JWT with kid=%s", kid)
        try:
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=self._audience,
            )
            logger.debug("JWT verified successfully")
            return payload
        except Exception as e:
            logger.error("JWT decode failed: %s", e)
            raise

    def _get_public_key(self, kid: str):
        jwks = self._fetch_jwks()
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        return None

    def _fetch_jwks(self) -> dict:
        logger.info("Fetching JWKS from %s", self._jwks_url)
        response = requests.get(self._jwks_url, timeout=30)
        response.raise_for_status()
        return response.json()
