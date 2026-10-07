"""
Unit tests for authentication, cookie parsing, and Clerk token processing.
"""

import base64
import json
import pytest
from molab_cli.auth import decode_jwt_payload, extract_client_cookie
from molab_cli.exceptions import AuthError


def test_extract_client_cookie_raw_jwt():
    # Valid 3-part JWT
    token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    res = extract_client_cookie(token)
    assert res == token


def test_extract_client_cookie_from_header():
    token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    cookie_str = f"__session=abc123xyz; __client={token}; __client_uat=1791104535"
    res = extract_client_cookie(cookie_str)
    assert res == token


def test_extract_client_cookie_invalid():
    res = extract_client_cookie("not_a_cookie_or_token")
    assert res is None


def test_decode_jwt_payload():
    payload = {"sub": "user_123", "email": "test@example.com"}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    fake_jwt = f"header.{payload_b64}.signature"
    decoded = decode_jwt_payload(fake_jwt)
    assert decoded["sub"] == "user_123"
    assert decoded["email"] == "test@example.com"


def test_decode_jwt_payload_invalid():
    assert decode_jwt_payload("invalid") == {}
