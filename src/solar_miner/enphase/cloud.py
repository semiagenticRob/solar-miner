"""Enphase Enlighten Cloud API client.

Used for:
- Historical production/consumption data export (for simulation)
- Checking whether consumption monitoring CTs are reporting
- Fallback when local IQ Gateway is unreachable

Rate limits (Watt plan): ~10,000 requests/month (~1 every 4.3 min).
NOT suitable for the real-time control loop — use the local API for that.

OAuth2 flow:
1. User visits authorization URL in browser
2. Enphase redirects with ?code=<auth_code>
3. Exchange auth_code for access_token + refresh_token
4. Use access_token in API calls
5. Refresh when expired
"""

import json
import logging
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ENPHASE_API_BASE = "https://api.enphaseenergy.com/api/v4"
ENPHASE_TOKEN_URL = "https://api.enphaseenergy.com/oauth/token"


@dataclass
class EnphaseCloudConfig:
    api_key: str
    client_id: str
    client_secret: str
    redirect_uri: str = "https://api.enphaseenergy.com/oauth/redirect_uri"
    token_path: str = "./data/enphase_tokens.json"


@dataclass
class TokenData:
    access_token: str
    refresh_token: str
    expires_at: float = 0.0  # Unix timestamp


class EnphaseCloudClient:
    """Enphase Enlighten v4 Cloud API client."""

    def __init__(self, config: EnphaseCloudConfig):
        self.config = config
        self._tokens: TokenData | None = None
        self._client = httpx.Client(timeout=30.0)
        self._load_tokens()

    def get_authorization_url(self) -> str:
        """Get the URL the user needs to visit to authorize the app."""
        return (
            f"https://api.enphaseenergy.com/oauth/authorize"
            f"?response_type=code"
            f"&client_id={self.config.client_id}"
            f"&redirect_uri={self.config.redirect_uri}"
        )

    def exchange_code(self, auth_code: str) -> TokenData:
        """Exchange an authorization code for access + refresh tokens."""
        # Basic auth header: base64(client_id:client_secret)
        credentials = b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode()
        ).decode()

        resp = self._client.post(
            ENPHASE_TOKEN_URL,
            headers={"Authorization": f"Basic {credentials}"},
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": self.config.redirect_uri,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        self._tokens = TokenData(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data.get("expires_at", 0),
        )
        self._save_tokens()
        logger.info("Enphase OAuth tokens obtained and saved")
        return self._tokens

    def refresh_access_token(self) -> TokenData:
        """Refresh the access token using the refresh token."""
        if not self._tokens or not self._tokens.refresh_token:
            raise RuntimeError("No refresh token available — re-authorize the app")

        credentials = b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode()
        ).decode()

        resp = self._client.post(
            ENPHASE_TOKEN_URL,
            headers={"Authorization": f"Basic {credentials}"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._tokens.refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        self._tokens = TokenData(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", self._tokens.refresh_token),
            expires_at=data.get("expires_at", 0),
        )
        self._save_tokens()
        logger.info("Enphase access token refreshed")
        return self._tokens

    def get_systems(self) -> list[dict]:
        """List all solar systems on the account."""
        resp = self._api_get("/systems")
        return resp.get("systems", [])

    def get_system_summary(self, system_id: int) -> dict:
        """Get summary for a system (includes whether consumption data is available)."""
        return self._api_get(f"/systems/{system_id}/summary")

    def get_production_stats(
        self,
        system_id: int,
        start_at: int | None = None,
        end_at: int | None = None,
    ) -> list[dict]:
        """Get 15-minute production intervals.

        Args:
            system_id: Enphase system ID
            start_at: Unix timestamp for start (default: start of today)
            end_at: Unix timestamp for end (default: now)
        """
        params = {}
        if start_at:
            params["start_at"] = start_at
        if end_at:
            params["end_at"] = end_at

        resp = self._api_get(f"/systems/{system_id}/telemetry/production_micro", params=params)
        return resp.get("intervals", [])

    def get_consumption_stats(
        self,
        system_id: int,
        start_at: int | None = None,
        end_at: int | None = None,
    ) -> list[dict]:
        """Get 15-minute consumption intervals (if CTs are installed).

        Returns empty list if no consumption monitoring is available.
        """
        params = {}
        if start_at:
            params["start_at"] = start_at
        if end_at:
            params["end_at"] = end_at

        try:
            resp = self._api_get(f"/systems/{system_id}/telemetry/consumption_meter", params=params)
            return resp.get("intervals", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 422):
                logger.info("No consumption data available for system %d (no CTs?)", system_id)
                return []
            raise

    def get_energy_lifetime(self, system_id: int) -> dict:
        """Get lifetime energy production data (daily totals)."""
        return self._api_get(f"/systems/{system_id}/energy_lifetime")

    def _api_get(self, path: str, params: dict | None = None) -> dict:
        """Make an authenticated GET request to the Enphase API."""
        if not self._tokens:
            raise RuntimeError("Not authenticated — run the OAuth flow first")

        all_params = {"key": self.config.api_key}
        if params:
            all_params.update(params)

        resp = self._client.get(
            f"{ENPHASE_API_BASE}{path}",
            headers={"Authorization": f"Bearer {self._tokens.access_token}"},
            params=all_params,
        )

        if resp.status_code == 401:
            logger.info("Access token expired — refreshing")
            self.refresh_access_token()
            resp = self._client.get(
                f"{ENPHASE_API_BASE}{path}",
                headers={"Authorization": f"Bearer {self._tokens.access_token}"},
                params=all_params,
            )

        resp.raise_for_status()
        return resp.json()

    def _save_tokens(self):
        """Persist tokens to disk."""
        if not self._tokens:
            return
        path = Path(self.config.token_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "access_token": self._tokens.access_token,
                "refresh_token": self._tokens.refresh_token,
                "expires_at": self._tokens.expires_at,
            }, f)

    def _load_tokens(self):
        """Load tokens from disk if available."""
        path = Path(self.config.token_path)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            self._tokens = TokenData(**data)
            logger.info("Loaded Enphase tokens from %s", path)

    def close(self):
        self._client.close()
