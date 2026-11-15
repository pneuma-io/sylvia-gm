"""Scoped safe reads observe resolver changes without retaining caller-owned registries."""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml
from yaml.nodes import ScalarNode

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "resolver_change_generator", ROOT / "scripts/srd_generator_yaml.py"
)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


@pytest.fixture
def loader(monkeypatch):
    class Loader(yaml.CSafeLoader):
        pass

    Loader.yaml_implicit_resolvers = {
        key: list(value) for key, value in Loader.yaml_implicit_resolvers.items()
    }
    Loader.yaml_path_resolvers = dict(Loader.yaml_path_resolvers)
    monkeypatch.setattr(yaml, "CSafeLoader", Loader)
    return Loader


@pytest.mark.parametrize("replacement", (False, True))
def test_same_source_observes_changed_implicit_resolver(loader, replacement):
    source = "probe: 123\n"

    @cache.scoped_generator_yaml
    def run():
        assert cache.load_generator_yaml(source)["probe"] == 123
        if replacement:
            loader.yaml_implicit_resolvers = {
                key: list(value) for key, value in loader.yaml_implicit_resolvers.items()
            }
        loader.yaml_implicit_resolvers["1"].insert(
            0, ("tag:yaml.org,2002:str", re.compile(r"^123$"))
        )
        expected = yaml.load(source, Loader=loader)
        assert expected == {"probe": "123"}
        assert cache.load_generator_yaml(source) == expected
        assert cache.load_generator_yaml(source) == expected

    run()


@pytest.mark.parametrize("replacement", (False, True))
def test_same_source_observes_changed_path_resolver(loader, replacement):
    source = "probe: '123'\n"
    loader.add_path_resolver("tag:yaml.org,2002:str", ["probe"], ScalarNode)

    @cache.scoped_generator_yaml
    def run():
        assert cache.load_generator_yaml(source) == {"probe": "123"}
        if replacement:
            loader.yaml_path_resolvers = dict(loader.yaml_path_resolvers)
        key = next(iter(loader.yaml_path_resolvers))
        loader.yaml_path_resolvers[key] = "tag:yaml.org,2002:int"
        expected = yaml.load(source, Loader=loader)
        assert expected == {"probe": 123}
        assert cache.load_generator_yaml(source) == expected
        assert cache.load_generator_yaml(source) == expected

    run()
