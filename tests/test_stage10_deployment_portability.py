from __future__ import annotations
import json, zipfile
from pathlib import Path
from artmach_assistant.core.deployment_layout import DeploymentPaths, NodeIdentity, export_persistent_data, import_persistent_data, load_node_identity, save_node_identity

def test_application_and_persistent_data_are_separate(tmp_path: Path):
    paths=DeploymentPaths.resolve(tmp_path/'Program Files'/'ECHO', tmp_path/'LocalAppData'/'ECHO')
    paths.ensure_persistent_tree()
    assert paths.application_root != paths.data_root
    assert paths.engineering_root.is_dir()
    assert paths.local_memory_root.is_dir()
    assert paths.shared_memory_cache_root.is_dir()

def test_node_identity_is_persistent_and_project_specific(tmp_path: Path):
    paths=DeploymentPaths.resolve(tmp_path/'app', tmp_path/'data')
    identity=NodeIdentity('ALFA','machine-001',r'C:\\Projects\\Alpha',r'\\NAS\\ECHO\\shared')
    save_node_identity(identity, paths)
    assert load_node_identity(paths) == identity

def test_invalid_node_is_rejected(tmp_path: Path):
    paths=DeploymentPaths.resolve(tmp_path/'app', tmp_path/'data')
    try: save_node_identity(NodeIdentity('DELTA','machine-001','x'), paths)
    except ValueError: pass
    else: raise AssertionError('invalid node accepted')

def test_migration_excludes_cache_and_temp_and_restores_persistent_state(tmp_path: Path):
    src=DeploymentPaths.resolve(tmp_path/'app1', tmp_path/'data1'); src.ensure_persistent_tree()
    (src.config_root/'settings.json').write_text('{"ok": true}', encoding='utf-8')
    (src.local_memory_root/'memory.json').write_text('memory', encoding='utf-8')
    (src.cache_root/'drop.bin').write_bytes(b'drop'); (src.temp_root/'drop.tmp').write_bytes(b'drop')
    bundle=export_persistent_data(src, tmp_path/'migration.zip')
    with zipfile.ZipFile(bundle) as z:
        names=set(z.namelist()); assert 'data/config/settings.json' in names; assert not any(n.startswith('data/cache/') for n in names); assert not any(n.startswith('data/temp/') for n in names)
    dst=DeploymentPaths.resolve(tmp_path/'app2', tmp_path/'data2')
    import_persistent_data(dst,bundle)
    assert (dst.config_root/'settings.json').is_file()
    assert (dst.local_memory_root/'memory.json').read_text(encoding='utf-8') == 'memory'

def test_migration_rejects_traversal(tmp_path: Path):
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(bad,'w') as z:
        z.writestr('MIGRATION.json', json.dumps({'kind':'echo-persistent-data'})); z.writestr('../escape.txt','bad')
    paths=DeploymentPaths.resolve(tmp_path/'app', tmp_path/'data')
    try: import_persistent_data(paths,bad)
    except ValueError: pass
    else: raise AssertionError('unsafe archive accepted')
