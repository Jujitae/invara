"""The release metadata must describe the same zero-dependency package."""
import json
from pathlib import Path
import tomllib

from test_intent import api


def test_release_metadata_and_plugin_agree_on_030():
    api()  # make the source package importable using the normal test path
    import invara
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    plugin = json.loads((root / 'plugin/.claude-plugin/plugin.json').read_text(encoding='utf-8'))
    server = json.loads((root / 'server.json').read_text(encoding='utf-8'))
    assert invara.__version__ == project['version'] == plugin['version'] == server['version'] == '0.3.0'
    assert all(package['version'] == '0.3.0' for package in server['packages'])
    assert project['dependencies'] == []
    assert project['scripts'] == {'invara':'invara.__main__:main', 'invara-mcp':'invara.mcp:main'}
    for source in (root / 'src/invara').rglob('*.py'):
        mirror = root / 'plugin/src/invara' / source.relative_to(root / 'src/invara')
        assert source.read_bytes() == mirror.read_bytes(), source
