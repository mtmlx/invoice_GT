import json
import os
import sys
from types import SimpleNamespace

import pytest

from clickup_integration import cli
from clickup_integration.auth import OAuthTokenResponse


@pytest.mark.parametrize('command', ['exchange-code', 'oauth-listen'])
def test_credentials_saved_privately_without_logging(monkeypatch, tmp_path, capsys, command):
    path = tmp_path / 'credentials.json'
    arguments = ['cli', command, '--token-output', str(path)]
    if command == 'exchange-code':
        arguments += ['--code', 'private-code-marker']
    monkeypatch.setattr(sys, 'argv', arguments)
    monkeypatch.setattr(cli.ClickUpSettings, 'from_env', lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(cli, 'build_authorization_url', lambda settings, state: 'https://example.invalid/authorize')
    monkeypatch.setattr(cli, 'wait_for_oauth_callback', lambda *a, **kw: {'code': 'private-code-marker', 'state': 'private-state-marker'})
    codes = []
    def exchange(settings, code):
        codes.append(code)
        return OAuthTokenResponse('private-token-marker', 'Bearer')
    monkeypatch.setattr(cli, 'exchange_code_for_token', exchange)
    cli.main()
    assert codes == ['private-code-marker']
    assert json.loads(path.read_text()) == {'CLICKUP_ACCESS_TOKEN': 'private-token-marker', 'CLICKUP_TOKEN_TYPE': 'Bearer'}
    assert path.stat().st_mode & 0o777 == 0o600
    printed = capsys.readouterr().out
    assert all(marker not in printed for marker in ['private-token-marker', 'private-code-marker', 'private-state-marker'])
    assert 'credentials_saved' in printed


@pytest.mark.parametrize('symlink', [False, True])
def test_existing_destination_rejected_before_exchange(monkeypatch, tmp_path, symlink):
    target = tmp_path / 'existing'
    target.write_text('preserve')
    path = tmp_path / 'link' if symlink else target
    if symlink:
        path.symlink_to(target)
    monkeypatch.setattr(sys, 'argv', ['cli', 'exchange-code', '--code', 'unused', '--token-output', str(path)])
    monkeypatch.setattr(cli.ClickUpSettings, 'from_env', lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(cli, 'exchange_code_for_token', lambda *a, **kw: pytest.fail('must not consume code'))
    with pytest.raises(FileExistsError):
        cli.main()
    assert target.read_text() == 'preserve'
