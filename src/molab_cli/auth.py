"""
Authentication manager for MoLab via Clerk Frontend API with auto-discovery.
"""

import base64
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from molab_cli.config import (
    DEFAULT_SESSION_ID,
    DEFAULT_USER_AGENT,
    get_client_cookie,
    get_org_id,
    get_session_id,
    load_config,
    save_config,
    set_client_cookie,
)
from molab_cli.exceptions import AuthError, ClerkApiError

# Global in-memory cache: (jwt_token, expires_at_timestamp)
_TOKEN_CACHE: Optional[Tuple[str, float]] = None


def extract_client_cookie(raw: str) -> Optional[str]:
    """
    Intelligently extract the __client JWT from raw user input (full cookie string,
    cURL command, or direct token).
    """
    cleaned = raw.strip(' "\'\r\n\t')
    # Pattern for __client=<jwt>
    m = re.search(r"__client=([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)", cleaned)
    if m:
        return m.group(1)

    # Check if raw input is directly a 3-part JWT
    parts = cleaned.split(".")
    if len(parts) == 3 and len(cleaned) > 50:
        return cleaned

    return None


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    """Decode JWT payload without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        pass
    return {}


def discover_client_state(client_cookie: str) -> Dict[str, Any]:
    """
    Query Clerk Frontend API to discover user profile, active sessions, and organizations.
    """
    url = "https://clerk.marimo.io/v1/client"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Cookie": f"__client={client_cookie}",
            "Origin": "https://molab.marimo.io",
            "Referer": "https://molab.marimo.io/",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            resp_obj = data.get("response", {})
            sessions = resp_obj.get("sessions", [])

            last_session_id = resp_obj.get("last_active_session_id")
            active_session = None

            if last_session_id:
                for s in sessions:
                    if s.get("id") == last_session_id:
                        active_session = s
                        break

            if not active_session and sessions:
                active_session = sessions[0]
                last_session_id = active_session.get("id")

            # Extract user email
            user_email = "Unknown User"
            org_id = None
            if active_session:
                user_obj = active_session.get("user", {})
                emails = user_obj.get("email_addresses", [])
                if emails:
                    user_email = emails[0].get("email_address", user_email)
                org_id = active_session.get("last_active_organization_id")

            return {
                "success": True,
                "session_id": last_session_id or DEFAULT_SESSION_ID,
                "org_id": org_id or get_org_id(),
                "user_email": user_email,
                "client_id": resp_obj.get("id"),
            }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise ClerkApiError(
            f"Clerk verification failed (HTTP {e.code}): {err_body}",
            hint="Please check that you copied the complete, active __client cookie from clerk.marimo.io.",
        ) from e
    except Exception as e:
        raise AuthError(f"Network error communicating with Clerk: {e}") from e


def mint_session_token(force: bool = False) -> str:
    """
    Mint a fresh RS256 session token from Clerk Frontend API using master __client cookie.
    Automatically refreshes every 60 seconds.
    """
    global _TOKEN_CACHE

    now = time.time()
    if not force and _TOKEN_CACHE is not None:
        token, expires_at = _TOKEN_CACHE
        if expires_at - now > 10:
            return token

    client_cookie = get_client_cookie()
    if not client_cookie:
        raise AuthError(
            "No MoLab authentication cookie configured.",
            hint="Run 'molab login' or use the interactive setup wizard to log in.",
        )

    session_id = get_session_id()
    if not session_id:
        discovery = discover_client_state(client_cookie)
        session_id = discovery.get("session_id", "")
        if session_id:
            cfg = load_config()
            cfg["session_id"] = session_id
            if discovery.get("org_id"):
                cfg["org_id"] = discovery["org_id"]
            save_config(cfg)

    url = f"https://clerk.marimo.io/v1/client/sessions/{session_id}/tokens"

    req = urllib.request.Request(
        url,
        data=b"",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Cookie": f"__client={client_cookie}",
            "Origin": "https://molab.marimo.io",
            "Referer": "https://molab.marimo.io/",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            jwt = data.get("jwt")
            if not jwt:
                raise ClerkApiError("Clerk API response was missing session JWT.")

            payload = decode_jwt_payload(jwt)
            exp = payload.get("exp", now + 60)
            _TOKEN_CACHE = (jwt, float(exp))
            return jwt
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise ClerkApiError(
            f"Clerk session token minting failed (HTTP {e.code}): {err_body}",
            hint="Your master __client cookie may have expired. Re-authenticate via 'molab login'.",
        ) from e
    except Exception as e:
        raise AuthError(f"Network error minting token: {e}") from e


def get_cookie_header() -> str:
    """
    Construct the full Cookie header string needed for molab.marimo.io.
    """
    jwt = mint_session_token()
    session_id = get_session_id()
    org_id = get_org_id()
    cfg = load_config()
    uat = str(cfg.get("client_uat", "1791104535"))

    return (
        f"__session={jwt}; __session_DtbpIAMT={jwt}; "
        f"__client_uat={uat}; __client_uat_DtbpIAMT={uat}; "
        f"clerk_active_context={session_id}:{org_id}"
    )


def save_and_verify_auth(raw_input: str) -> Dict[str, Any]:
    """
    Process raw user input, extract cookie, query Clerk, save configuration,
    and verify session generation.
    """
    extracted = extract_client_cookie(raw_input)
    if not extracted:
        raise AuthError(
            "Could not parse a valid Clerk __client token from input.",
            hint="Make sure you copied either the full cookie string or the __client JWT value.",
        )

    # Auto-discover from Clerk
    discovery = discover_client_state(extracted)

    # Save to disk
    cfg = load_config()
    cfg["client_cookie"] = extracted
    if discovery.get("session_id"):
        cfg["session_id"] = discovery["session_id"]
    if discovery.get("org_id"):
        cfg["org_id"] = discovery["org_id"]
    save_config(cfg)

    # Verify minting
    jwt = mint_session_token(force=True)
    jwt_payload = decode_jwt_payload(jwt)

    return {
        "user_email": discovery.get("user_email") or jwt_payload.get("email", "Unknown"),
        "session_id": discovery.get("session_id"),
        "org_id": discovery.get("org_id"),
        "client_id": discovery.get("client_id"),
    }


def inspect_auth_status() -> Dict[str, Any]:
    """Return parsed metadata about the current authentication state."""
    client_cookie = get_client_cookie()
    if not client_cookie:
        return {"authenticated": False, "reason": "No __client cookie configured"}

    payload = decode_jwt_payload(client_cookie)
    client_id = payload.get("id", "Unknown")

    try:
        jwt = mint_session_token()
        jwt_payload = decode_jwt_payload(jwt)
        return {
            "authenticated": True,
            "client_id": client_id,
            "session_id": jwt_payload.get("sid", get_session_id()),
            "user_id": jwt_payload.get("sub", "Unknown"),
            "user_email": jwt_payload.get("email", "Unknown"),
            "org_id": jwt_payload.get("o", {}).get("id", get_org_id()),
            "org_slug": jwt_payload.get("o", {}).get("slg", "Personal Workspace"),
        }
    except Exception as e:
        return {
            "authenticated": False,
            "client_id": client_id,
            "error": str(e),
        }
