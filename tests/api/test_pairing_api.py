"""Pairing flow: the dashboard issues a one-time code, the desktop agent trades it for a token."""
import re
from datetime import datetime, timedelta

import pytest

from services.web.auth import verify_agent_token
from services.web.routers import pairing

CODE_RE = re.compile(rf'^[{pairing.CODE_ALPHABET}]{{{pairing.CODE_LENGTH}}}$')


@pytest.fixture
def code(client, auth_headers):
    return client.get('/api/pairing/code', headers=auth_headers).json()['code']


def claim(client, code):
    return client.post('/api/pairing/claim', json={'code': code})


class TestIssueCode:
    def test_code_format(self, client, auth_headers):
        body = client.get('/api/pairing/code', headers=auth_headers).json()
        assert CODE_RE.match(body['code'])
        assert body['expires_in'] == 600

    def test_code_has_no_ambiguous_characters(self, client, auth_headers):
        codes = {client.get('/api/pairing/code', headers=auth_headers).json()['code'] for _ in range(30)}
        assert not set(''.join(codes)) & set('0O1IL')

    def test_new_code_invalidates_previous(self, client, auth_headers):
        first = client.get('/api/pairing/code', headers=auth_headers).json()['code']
        second = client.get('/api/pairing/code', headers=auth_headers).json()['code']
        assert claim(client, first).status_code == 404
        assert claim(client, second).status_code == 200

    def test_codes_of_different_users_coexist(self, client, auth_headers, other_user):
        from services.web.auth import create_jwt
        mine = client.get('/api/pairing/code', headers=auth_headers).json()['code']
        theirs = client.get('/api/pairing/code',
                            headers={'Authorization': f'Bearer {create_jwt(other_user["id"])}'}).json()['code']
        assert claim(client, mine).json()['discord_id'] == '111222333'
        assert claim(client, theirs).json()['discord_id'] == '999888777'


class TestClaim:
    def test_returns_agent_token_for_owner(self, client, user, code):
        resp = claim(client, code)
        assert resp.status_code == 200
        body = resp.json()
        assert body['discord_id'] == user['discord_id']
        claims = verify_agent_token(body['agent_token'])
        assert claims['sub'] == user['id']
        assert claims['did'] == user['discord_id']

    def test_code_is_single_use(self, client, code):
        assert claim(client, code).status_code == 200
        assert claim(client, code).status_code == 404

    @pytest.mark.parametrize('transform', [
        str.lower,
        lambda c: f'{c[:4]}-{c[4:]}',
        lambda c: f'  {c[:4]} {c[4:]}  ',
    ], ids=['lowercase', 'dash', 'spaces'])
    def test_hand_typed_variants_are_accepted(self, client, code, transform):
        assert claim(client, transform(code)).status_code == 200

    @pytest.mark.parametrize('bad', ['', 'AAAAAAAA', 'ABC'])
    def test_unknown_code(self, client, code, bad):
        resp = claim(client, bad)
        assert resp.status_code == 404
        assert resp.json()['detail'] == 'Invalid or expired pairing code'

    @pytest.mark.parametrize('length, status', [(32, 404), (33, 422)])
    def test_code_max_length(self, client, length, status):
        assert claim(client, 'A' * length).status_code == status

    @pytest.mark.parametrize('body', [{}, {'code': None}, {'code': 12345678}, {'kode': 'ABCDEFGH'}])
    def test_malformed_body(self, client, body):
        assert client.post('/api/pairing/claim', json=body).status_code == 422


class TestExpiry:
    def _shift_expiry(self, code, delta):
        pairing._pairing_codes[code]['expires_at'] = datetime.utcnow() + delta

    def test_valid_just_before_expiry(self, client, code):
        self._shift_expiry(code, timedelta(seconds=5))
        assert claim(client, code).status_code == 200

    def test_rejected_after_expiry(self, client, code):
        self._shift_expiry(code, timedelta(seconds=-1))
        assert claim(client, code).status_code == 404

    async def test_cleanup_removes_only_expired(self, monkeypatch):
        pairing._pairing_codes['OLD'] = {'expires_at': datetime.utcnow() - timedelta(seconds=1), 'user_id': 'a'}
        pairing._pairing_codes['NEW'] = {'expires_at': datetime.utcnow() + timedelta(minutes=5), 'user_id': 'b'}

        class Stop(Exception):
            pass

        async def stop(_):
            raise Stop
        monkeypatch.setattr(pairing.asyncio, 'sleep', stop)
        with pytest.raises(Stop):
            await pairing._cleanup_expired()
        assert set(pairing._pairing_codes) == {'NEW'}


class TestBruteForceProtection:
    def test_blocked_after_ten_failures(self, client, code):
        for _ in range(10):
            assert claim(client, 'WRONGCODE').status_code == 404
        assert claim(client, 'WRONGCODE').status_code == 429
        # Even the correct code is refused while blocked.
        assert claim(client, code).status_code == 429

    def test_nine_failures_still_allow_valid_code(self, client, code):
        for _ in range(9):
            claim(client, 'WRONGCODE')
        assert claim(client, code).status_code == 200

    def test_successful_claims_do_not_count(self, client, auth_headers):
        for _ in range(12):
            fresh = client.get('/api/pairing/code', headers=auth_headers).json()['code']
            assert claim(client, fresh).status_code == 200
