from __future__ import annotations

from pathlib import Path

import pytest

from clipoteca.catalog import Catalog, canonical_source_url, normalized_source_title
from clipoteca.ingest import ingest
from clipoteca.jobs import JobStore
from clipoteca.providers.base import Adapter, Candidate, MediaRef, SearchLimits, SupportStatus


def clip(
    *,
    provider: str = "pexels",
    source_url: str = "https://www.example.com/watch/42?utm_source=feed",
    source_title: str = "Amanecer sobre el río de Valparaíso",
    fingerprint: str = "aaaa~100.00:bbbb~120.00",
) -> dict:
    return {
        "id": "clip-000042",
        "title": {"es": "Amanecer", "en": "Sunrise"},
        "description": {"es": "Río", "en": "River"},
        "tags": {"es": ["amanecer"], "en": ["sunrise"]},
        "source": {
            "provider": provider,
            "provider_id": "original-42",
            "source_language": "es",
            "source_title": source_title,
            "source_url": source_url,
        },
        "media": {
            "sha256": "a" * 64,
            "perceptual_hash": fingerprint,
        },
    }


def candidate_draft(*, source_url: str, source_title: str, fingerprint: str = "") -> dict:
    return {
        "source": {
            "provider": "pixabay",
            "provider_id": "copy-9",
            "source_url": source_url,
            "source_title": source_title,
        },
        "media": {"perceptual_hash": fingerprint},
    }


def test_canonical_url_and_title_remove_cross_provider_noise():
    assert (
        canonical_source_url("http://WWW.Example.com:80/watch/42/?b=2&utm_campaign=x&a=1#comments")
        == "https://example.com/watch/42?a=1&b=2"
    )
    assert normalized_source_title("  AMANÉCER: sobre el río, de Valparaíso!!! ") == (
        "amanecer sobre el rio de valparaiso"
    )


@pytest.mark.parametrize(
    ("draft", "reason"),
    [
        (
            candidate_draft(
                source_url="http://example.com/watch/42?fbclid=discard",
                source_title="Un título diferente y descriptivo",
            ),
            "canonical_url",
        ),
        (
            candidate_draft(
                source_url="https://other.example/video/9",
                source_title="AMANECER, SOBRE EL RIO DE VALPARAISO!",
            ),
            "normalized_title",
        ),
        (
            candidate_draft(
                source_url="https://other.example/video/9",
                source_title="Otro video",
                fingerprint="aaaa~100.00:bbbb~120.00",
            ),
            "perceptual_hash",
        ),
    ],
)
def test_cross_provider_duplicate_signals(draft, reason):
    match = Catalog([clip()]).duplicate_match(
        draft, phash_similar=lambda left, right: left == right
    )
    assert match is not None
    assert match[0]["id"] == "clip-000042"
    assert match[1] == reason


def test_generic_title_alone_does_not_collapse_distinct_stock_clips():
    existing = clip(source_title="Rain", source_url="https://one.example/rain")
    draft = candidate_draft(source_title="RAIN!", source_url="https://two.example/rain")
    assert Catalog([existing]).duplicate_match(draft) is None


class CrossProviderAdapter(Adapter):
    name = "pixabay"
    category = "stock_video"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self) -> None:
        self.direct_calls = 0

    async def search(self, term: str, lang: str, limits: SearchLimits):
        return [
            Candidate(
                self.name,
                "copy-9",
                term,
                lang,
                "http://example.com/watch/42?utm_medium=social",
                "Copia con otro título descriptivo",
                "es",
            )
        ]

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        self.direct_calls += 1
        return MediaRef("https://cdn.example/copy.mp4", "video/mp4")


class CatalogReader:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def read_catalog(self):
        return self.catalog, "revision-1"


class NoUploadStorage:
    def get_or_create_batch_release(self, day):  # pragma: no cover - safety tripwire
        raise AssertionError("a cross-provider duplicate must not allocate a release")


@pytest.mark.asyncio
async def test_ingest_discards_cross_provider_url_before_resolution_or_download(tmp_path: Path):
    adapter = CrossProviderAdapter()
    report = await ingest(
        "rain",
        [adapter],
        repository="owner/repo",
        token="token",
        job_root=tmp_path,
        writer=CatalogReader(Catalog([clip()])),
        storage=NoUploadStorage(),
    )

    assert report.status == "duplicate"
    assert report.published_ids == ("clip-000042",)
    assert adapter.direct_calls == 0
    persisted = JobStore(tmp_path).load(report.request_id)
    assert persisted is not None
    assert persisted["source_errors"]["pixabay:copy-9"] == (
        "duplicate_cross_provider:canonical_url:clip-000042"
    )
