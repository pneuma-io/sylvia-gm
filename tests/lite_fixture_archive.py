"""Preserve closed synthetic databases losslessly within the retained output allowance."""
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from scripts.journey_progress import timed

ROOT = Path(os.environ.get("SYLVIA_QA_FIXTURE_ROOT", Path(__file__).resolve().parents[2])).resolve()
BLOCK_BYTES = 65536


@timed("database_archive")
def archive_closed_database(original, owned_directory):
    """Preserve a closed synthetic database in an archive verified by its restore digest."""
    if os.environ.get("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES") == "0":
        # This optional evidence copy is not a gameplay or save/recovery assertion.
        # Keep closed raw data available locally, including for a failing scenario.
        return
    assert not original.is_symlink()
    assert original.resolve().is_relative_to(owned_directory.resolve())
    assert original.resolve().is_relative_to((ROOT / 'attempts').resolve())
    archive = original.with_name(original.name + '.parts.json')
    assert not archive.exists()
    size = original.stat().st_size
    digest = hashlib.sha256()
    blocks = []
    with original.open('rb') as stream:
        while content := stream.read(BLOCK_BYTES):
            digest.update(content)
            block_digest = hashlib.sha256(content).hexdigest()
            blob = ROOT / 'fixture-store' / block_digest[:2] / (block_digest + '.zip')
            if not blob.exists():
                blob.parent.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix="block-", suffix=".zip", dir=blob.parent)
                os.close(descriptor)
                temporary = Path(name)
                try:
                    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
                        output.writestr('data', content)
                    # Publish a complete file exactly once. A competing worker
                    # must keep the first archive's digest for saved manifests.
                    try:
                        os.link(temporary, blob)
                    except FileExistsError:
                        pass
                finally:
                    assert temporary.resolve().is_relative_to(blob.parent.resolve())
                    temporary.unlink()
            with zipfile.ZipFile(blob) as check:
                assert check.namelist() == ['data']
                restored = check.read('data')
            assert len(restored) == len(content) and hashlib.sha256(restored).hexdigest() == block_digest
            blocks.append({'archive': str(blob), 'archive_sha256': hashlib.sha256(blob.read_bytes()).hexdigest(),
                'member': 'data', 'bytes': len(content), 'sha256': block_digest})
    archive.write_text(json.dumps({'format': 'sylvia-owned-fixture-blocks-v1',
        'original_bytes': size, 'original_sha256': digest.hexdigest(), 'blocks': blocks}, indent=2), encoding='utf-8')
    # Read the saved manifest and each saved archive, rather than trusting write buffers.
    manifest = json.loads(archive.read_text(encoding='utf-8'))
    restored_digest = hashlib.sha256()
    restored_size = 0
    for block in manifest['blocks']:
        blob = Path(block['archive'])
        assert hashlib.sha256(blob.read_bytes()).hexdigest() == block['archive_sha256']
        with zipfile.ZipFile(blob) as check:
            content = check.read(block['member'])
        assert hashlib.sha256(content).hexdigest() == block['sha256']
        restored_digest.update(content)
        restored_size += len(content)
    assert restored_size == size and restored_digest.hexdigest() == digest.hexdigest()
    original.with_suffix('.archive.json').write_text(json.dumps({
        'format': 'sylvia-owned-fixture-blocks-v1',
        'original': str(original), 'original_bytes': size, 'original_sha256': digest.hexdigest(),
        'archive': str(archive), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'closed_before_archiving': True, 'full_restore_digest_verified': True,
    }, indent=2), encoding='utf-8')
    original.unlink()


def archive_closed_case(case):
    """Close and preserve both the selected save and its automatic migration snapshots."""
    case.close()
    originals = [case.path] if case.path.exists() else []
    originals += sorted((case.path.parent / 'migration-sources').glob(
        case.path.stem + '.pre-migration-*.sqlite3'
    ))
    for original in originals:
        archive_closed_database(original, case.path.parent)
