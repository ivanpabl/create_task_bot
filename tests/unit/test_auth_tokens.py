"""JWT issuing and verification: browser session tokens vs. desktop agent tokens."""
import base64
import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from jose import jwt

from services.web import auth
from services.web.auth import (
    create_agent_token, create_jwt, verify_agent_token, verify_jwt, verify_user_jwt,
)
from services.web.config import settings


def encode(payload, secret=None):
    return jwt.encode(payload, secret or settings.jwt_secret, algorithm='HS256')


def future(**delta):
    return datetime.utcnow() + timedelta(**delta)


class TestUserToken:
    def test_roundtrip(self):
        user_id = uuid4()
        payload = verify_user_jwt(create_jwt(user_id))
        assert payload['sub'] == str(user_id)
        assert payload['typ'] == 'user'

    def test_lifetime_is_seven_days(self):
        payload = verify_jwt(create_jwt(uuid4()))
        lifetime = datetime.utcfromtimestamp(payload['exp']) - datetime.utcnow()
        assert timedelta(days=6, hours=23) < lifetime <= timedelta(days=7)

    def test_legacy_token_without_typ_is_accepted(self):
        assert verify_user_jwt(encode({'sub': 'abc', 'exp': future(hours=1)})) is not None

    def test_agent_token_is_not_a_user_token(self):
        assert verify_user_jwt(create_agent_token(uuid4(), '123')) is None

    def test_token_without_sub_is_rejected(self):
        assert verify_user_jwt(encode({'typ': 'user', 'exp': future(hours=1)})) is None


class TestAgentToken:
    def test_roundtrip(self):
        user_id = uuid4()
        payload = verify_agent_token(create_agent_token(user_id, '555'))
        assert payload['sub'] == str(user_id)
        assert payload['did'] == '555'

    def test_user_token_is_not_an_agent_token(self):
        assert verify_agent_token(create_jwt(uuid4())) is None

    @pytest.mark.parametrize('payload', [
        {'typ': 'agent', 'did': '555'},
        {'typ': 'agent', 'sub': 'abc'},
        {'typ': 'agent', 'sub': 'abc', 'did': ''},
    ], ids=['no-sub', 'no-did', 'empty-did'])
    def test_incomplete_claims_are_rejected(self, payload):
        assert verify_agent_token(encode({**payload, 'exp': future(hours=1)})) is None


class TestSignatureAndExpiry:
    def test_wrong_secret(self):
        assert verify_jwt(encode({'sub': 'abc', 'exp': future(hours=1)}, secret='attacker')) is None

    def test_expired(self):
        assert verify_jwt(encode({'sub': 'abc', 'exp': datetime.utcnow() - timedelta(seconds=1)})) is None

    def test_alg_none_is_rejected(self):
        def b64(data):
            return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()
        token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'abc'})}."
        assert verify_jwt(token) is None

    @pytest.mark.parametrize('token', ['', 'garbage', 'a.b.c'])
    def test_malformed(self, token):
        assert verify_jwt(token) is None

    def test_secret_comes_from_settings(self, monkeypatch):
        token = create_jwt(uuid4())
        monkeypatch.setattr(auth.settings, 'jwt_secret', 'rotated')
        assert verify_jwt(token) is None
