"""M10 catalogue rollback uses schema checks and SHA optimistic guard."""

import hashlib
import json
from pathlib import Path

import pytest

from clipoteca.catalog import Catalog
from clipoteca.recovery import restore_catalog_snapshot


def test_validated_snapshot_rollback_rebuilds_indexes(tmp_path):
    actual = Path(__file__).resolve().parents[1] / "data/clips.json"
    records = json.loads(actual.read_text(encoding="utf-8"))
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(records), encoding="utf-8")
    live = tmp_path / "clips.json"
    revised = [dict(row) for row in records]
    revised[0]["title"] = {"es": "Cambio", "en": "Change"}
    Catalog(revised).save(live)
    index_root = tmp_path / "index"
    Catalog(revised).write_indexes(index_root)
    digest = hashlib.sha256(live.read_bytes()).hexdigest()
    assert restore_catalog_snapshot(
        catalog_path=live,
        snapshot_path=snapshot,
        index_root=index_root,
        expected_current_sha256=digest,
    ) == len(records)
    assert json.loads(live.read_text(encoding="utf-8")) == records
    assert (index_root / "es").is_dir()
    assert (index_root / "en").is_dir()


def test_stale_sha_and_invalid_snapshot_leave_live_untouched(tmp_path):
    actual = Path(__file__).resolve().parents[1] / "data/clips.json"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(actual.read_bytes())
    live = tmp_path / "clips.json"
    live.write_bytes(actual.read_bytes())
    before = live.read_bytes()
    index_root = tmp_path / "indexes"
    with pytest.raises(ValueError, match="changed"):
        restore_catalog_snapshot(
            catalog_path=live,
            snapshot_path=snapshot,
            index_root=index_root,
            expected_current_sha256="0" * 64,
        )
    assert live.read_bytes() == before
    snapshot.write_text("[{}]", encoding="utf-8")
    with pytest.raises(Exception):
        restore_catalog_snapshot(
            catalog_path=live,
            snapshot_path=snapshot,
            index_root=index_root,
            expected_current_sha256=hashlib.sha256(before).hexdigest(),
        )
    assert live.read_bytes() == before
    assert not index_root.exists()
