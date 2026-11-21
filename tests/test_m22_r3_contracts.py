"""M22 r3: strict OpenAPI YAML and declared Worker reason contract."""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


class StrictLoader(yaml.SafeLoader):
    """Reject duplicate YAML mapping keys instead of silently shadowing."""


def construct_unique_map(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ValueError(f"duplicate_yaml_key:{key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_map)


def test_openapi_loads_without_duplicate_keys():
    spec = yaml.load((ROOT / "docs/openapi.yaml").read_text(), Loader=StrictLoader)
    assert spec["openapi"] == "3.1.0"
    contract = spec["components"]["schemas"]["SearchResponse"]["properties"]
    assert contract["semantic_reason"]["type"] == "string"
    assert contract["semantic"]["enum"] == ["ready", "pending", "unavailable"]
    with pytest.raises(ValueError, match="duplicate_yaml_key:description"):
        yaml.load(
            "test:\n  description: first\n  description: second\n",
            Loader=StrictLoader,
        )


def test_semantic_reason_enum_covers_emitted_typescript_codes():
    spec = json.loads((ROOT / "schemas/search.schema.json").read_text())
    declared = set(spec["oneOf"][1]["properties"]["semantic_reason"]["enum"])
    source = (ROOT / "worker/src/semantic.ts").read_text()
    code_literals = set(re.findall(r'new SemanticFault\("([^"]+)"\)', source))
    reason_literals = set(re.findall(r'semantic_reason\s*:\s*"([^"]+)"', source))
    emitted = code_literals | reason_literals
    assert emitted <= declared, f"undocumented semantic_reason values: {emitted - declared}"
    assert {"no_semantic_match", "query_vector_invalid", "unsafe_query_encoder_url"} <= declared


def test_python_313_and_worker_unicode17_digest_fixture_parity():
    from clipoteca.media.shards import normalized_query, query_digest

    fixture = json.loads((ROOT / "tests/fixtures/m22/normalization_r3.json").read_text())
    assert fixture["unicode"] == "17.0.0"
    for case in fixture["cases"]:
        assert normalized_query(case["input"]) == case["normalized"]
        assert query_digest(case["input"]) == case["sha256"]
