"""Admitted file streaming reuses directory work while retaining exact bounded bytes."""

import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from sylvia_gm.infrastructure.package_archive import PACKAGE_CHUNK_LIMIT, admit_package_archive
from tests.test_alpha_f3_package_publication import _archive, _package_record


def _source():
    payloads = {f"data/{index}.txt": bytes([65 + index]) * (PACKAGE_CHUNK_LIMIT + index + 1)
                for index in range(6)}
    record = _package_record("synthetic.streaming", "1", payloads)
    return _archive(((record, payloads),)), payloads


def test_many_admitted_reads_do_not_reparse_the_zip_directory(monkeypatch):
    source, payloads = _source()
    with admit_package_archive(io.BytesIO(source)) as admitted:
        directories = []
        original = zipfile.ZipFile._RealGetContents

        def observed(archive):
            directories.append(1)
            return original(archive)

        monkeypatch.setattr(zipfile.ZipFile, "_RealGetContents", observed)
        for _ in range(2):
            for file in admitted.files:
                chunks = tuple(admitted.iter_file_chunks(file))
                assert chunks and max(map(len, chunks)) <= PACKAGE_CHUNK_LIMIT
                value = b"".join(chunks)
                assert value == payloads[file.file.path]
                assert hashlib.sha256(value).hexdigest() == file.file.sha256
        assert len(directories) <= 1, "Directory parsing must not grow with streamed file count"


def test_interleaved_and_concurrent_streams_remain_independent():
    source, payloads = _source()
    with admit_package_archive(io.BytesIO(source)) as admitted:
        first, second = admitted.files[:2]
        a, b = admitted.iter_file_chunks(first), admitted.iter_file_chunks(second)
        prefix_a, prefix_b = next(a), next(b)
        assert prefix_a + b"".join(a) == payloads[first.file.path]
        assert prefix_b + b"".join(b) == payloads[second.file.path]
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda f: b"".join(admitted.iter_file_chunks(f)),
                                        admitted.files))
        assert results == [payloads[f.file.path] for f in admitted.files]
    admitted.close()
    with pytest.raises(RuntimeError, match="closed"):
        next(admitted.iter_file_chunks(first))


def test_changed_archive_bytes_still_require_admission():
    source, _ = _source()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(output, "w") as changed:
        for item in original.infolist():
            value = original.read(item.filename)
            if item.filename.endswith("/0.txt"):
                value = b"x" + value[1:]
            changed.writestr(item.filename, value)
    from sylvia_gm.domain.errors import CommandRejected

    with pytest.raises(CommandRejected, match="admission failed"):
        admit_package_archive(io.BytesIO(output.getvalue()))
