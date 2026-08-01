import json
from artmach_assistant.core.build_manager import BuildManager

def test_package_scripts_filters_invalid_entries(tmp_path):
    path = tmp_path / 'package.json'
    path.write_text(json.dumps({'scripts': {' test ': 'ok', 'bad\u0000': 'x', 'huge': 'x' * 20001}}), encoding='utf-8')
    assert BuildManager._package_scripts(path) == {'test': 'ok'}

def test_package_scripts_rejects_symlink(tmp_path):
    target = tmp_path / 'real.json'
    target.write_text('{"scripts":{"test":"ok"}}', encoding='utf-8')
    link = tmp_path / 'package.json'
    try:
        link.symlink_to(target)
    except OSError:
        return
    assert BuildManager._package_scripts(link) == {}
