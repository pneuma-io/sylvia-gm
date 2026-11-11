"""A disposable test cache preserves actual admission and strict authoring boundaries."""

import pytest

from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import PackageRuntimeError, _parse_runtime
from tests.test_alpha_f3_package_execution import _runtime_yaml


@pytest.fixture
def cache(tmp_path, monkeypatch):
    directory = tmp_path / "runtimes"
    monkeypatch.setenv("SYLVIA_RUNTIME_CACHE_DIR", str(directory))
    package_runtime._clear_runtime_cache()
    loads = []
    original = package_runtime._load_runtime

    def observed(**kwargs):
        loads.append((kwargs["package_id"], kwargs["runtime_bytes"], kwargs["authoring"]))
        return original(**kwargs)

    monkeypatch.setattr(package_runtime, "_load_runtime", observed)
    return directory, loads


def parse(source=None, **kwargs):
    return _parse_runtime(
        package_id=kwargs.pop("package_id", "synthetic.clockwork"),
        runtime_bytes=_runtime_yaml() if source is None else source,
        **kwargs,
    )


def test_warm_disk_load_reconstructs_the_same_admitted_runtime(cache):
    directory, loads = cache
    first = parse(authoring=True)
    package_runtime._clear_runtime_cache()
    second = parse(authoring=True)
    assert len(loads) == 1
    assert second is not first
    assert second.tools == first.tools
    assert second.procedures == first.procedures
    assert second.definitions == first.definitions
    assert len(list(directory.glob("*.json"))) == 1


def test_cache_keys_keep_bytes_package_and_authoring_validation_separate(cache):
    _, loads = cache
    duplicate = _runtime_yaml() + b"tools: {}\n"
    parse(duplicate)
    package_runtime._clear_runtime_cache()
    with pytest.raises(PackageRuntimeError, match="YAML is invalid"):
        parse(duplicate, authoring=True)
    with pytest.raises(PackageRuntimeError, match="does not match"):
        parse(package_id="another.ruleset")
    parse(_runtime_yaml() + b"\n")
    assert len(loads) == 4


def test_corrupt_cache_falls_back_to_real_validation(cache):
    directory, loads = cache
    expected = parse().tools
    next(directory.glob("*.json")).write_text("not JSON")
    package_runtime._clear_runtime_cache()
    assert parse().tools == expected
    assert len(loads) == 2


def test_changed_cache_version_is_a_miss(cache, monkeypatch):
    from sylvia_gm.application import _runtime_cache

    directory, loads = cache
    parse()
    monkeypatch.setattr(_runtime_cache, "_CACHE_VERSION", _runtime_cache._CACHE_VERSION + 1)
    package_runtime._clear_runtime_cache()
    parse()
    assert len(loads) == 2
    assert len(list(directory.glob("*.json"))) == 2


def test_failed_admission_never_creates_a_cache_entry(cache):
    directory, loads = cache
    for _ in range(2):
        with pytest.raises(PackageRuntimeError):
            parse(b"!!python/object:builtins.dict {}")
    assert len(loads) == 2
    assert not directory.exists()


def test_disabled_cache_and_failed_atomic_write_keep_fresh_validation(cache, monkeypatch):
    from sylvia_gm.application import _runtime_cache

    directory, loads = cache
    monkeypatch.delenv("SYLVIA_RUNTIME_CACHE_DIR")
    expected = parse().tools
    package_runtime._clear_runtime_cache()
    assert parse().tools == expected
    assert len(loads) == 2
    assert not directory.exists()
    monkeypatch.setenv("SYLVIA_RUNTIME_CACHE_DIR", str(directory))

    def unavailable(*args):
        raise OSError("Synthetic cache-write failure")

    monkeypatch.setattr(_runtime_cache.os, "replace", unavailable)
    package_runtime._clear_runtime_cache()
    assert parse().tools == expected
    assert len(loads) == 3
    assert not list(directory.iterdir())
