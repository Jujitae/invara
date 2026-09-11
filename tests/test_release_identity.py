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


def test_current_public_instructions_and_publishers_are_030():
    root = Path(__file__).resolve().parents[1]
    quickstart = (root / 'docs/AI_AGENT_QUICKSTART.md').read_text(encoding='utf-8')
    intent = (root / 'docs/INTENT_PROMISE_QUICKSTART.md').read_text(encoding='utf-8')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    plugin_readme = (root / 'plugin/README.md').read_text(encoding='utf-8')
    pypi_workflow = (root / '.github/workflows/publish-pypi.yml').read_text(encoding='utf-8')
    mcp_workflow = (root / '.github/workflows/publish-mcp-registry.yml').read_text(encoding='utf-8')

    assert 'invara==0.3.0' in quickstart
    assert 'uvx --from invara==0.3.0 invara list' in quickstart
    assert 'v0.3.0/docs/AI_AGENT_QUICKSTART.md' in quickstart
    assert 'invara==0.2.1' not in quickstart
    assert 'invara==0.3.0' in intent
    assert '아직 공개하지 않은 로컬 후보' not in intent
    assert 'invara==0.3.0' in readme
    assert 'uvx --from invara==0.3.0 invara list' in readme
    assert '0.3.0' in plugin_readme
    assert 'io.github.Jujitae/invara' in plugin_readme
    assert '0.2.1' not in pypi_workflow
    assert '0.2.1' not in mcp_workflow
    assert '0.3.0' in pypi_workflow
    assert '0.3.0' in mcp_workflow
