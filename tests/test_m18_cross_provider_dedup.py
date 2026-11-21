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
    duration: float = 12.0,
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
            "duration_seconds": duration,
        },
    }


def candidate_draft(
    *, source_url: str, source_title: str, fingerprint: str = "",
    duration: float | None = None,
) -> dict:
    return {
        "source": {
            "provider": "pixabay",
            "provider_id": "copy-9",
            "source_url": source_url,
            "source_title": source_title,
        },
        "media": {"perceptual_hash": fingerprint, "duration_seconds": duration},
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
                duration=12.2,
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


@pytest.mark.parametrize(
    "title", ["Newsreel 1945", "Rain over the city", "Untitled motion picture"]
)
def test_same_common_title_across_providers_different_content_is_not_duplicate(title):
    """An identical descriptive title cannot discard distinct content."""
    existing = clip(
        source_url="https://archive.example/item/A",
        source_title=title, fingerprint="aaaa",
        duration=10.0,
    )
    draft = candidate_draft(
        source_url="https://museum.example/item/B",
        source_title=title, fingerprint="bbbb",
        duration=16.0,
    )
    assert Catalog([existing]).duplicate_match(
        draft, phash_similar=lambda left, right: left == right
    ) is None
    # Even after download, title alone is not a signal.
    assert Catalog([existing]).duplicate_match(
        {"source": draft["source"], "media": {"perceptual_hash": "bbbb"}}
    ) is None


@pytest.mark.parametrize(
    "identity_a, identity_b",
    [
        ("https://video.example/watch/1?si=ONE", "https://video.example/watch/1?si=TWO"),
        (
            "https://video.example/watch/1?feature=main",
            "https://video.example/watch/1?feature=variant",
        ),
    ],
)
def test_query_identity_parameters_are_not_discarded(identity_a, identity_b):
    assert canonical_source_url(identity_a) != canonical_source_url(identity_b)


def test_same_provider_canonical_url_with_new_provider_id_is_duplicate():
    original = clip(provider="pexels", source_url="https://example.org/item/9?utm_source=x")
    changed = candidate_draft(
        source_url="http://www.example.org/item/9?utm_medium=y",
        source_title="Another documentary",
    )
    changed["source"]["provider"] = "pexels"
    changed["source"]["provider_id"] = "new-provider-id"
    match = Catalog([original]).duplicate_match(changed)
    assert match is not None and match[1] == "canonical_url"


def test_same_video_from_two_sources_is_duplicate_by_hash_or_phash():
    existing = clip(source_url="https://first.example/a", source_title="Newsreel 1945")
    draft = candidate_draft(
        source_url="https://second.example/b", source_title="Untitled motion picture",
        fingerprint="aaaa~100.00:bbbb~120.00",
    )
    matched = Catalog([existing]).duplicate_match(
        draft, phash_similar=lambda left, right: left == right
    )
    assert matched is not None and matched[1] == "perceptual_hash"
    draft["media"] = {"sha256": "a" * 64}
    matched = Catalog([existing]).duplicate_match(draft)
    assert matched is not None and matched[1] == "sha256"


def test_public_source_url_redacts_secrets_but_retains_identity():
    from clipoteca.catalog import safe_source_url

    source = "https://museum.example/movie?api_key=VERY_SECRET&si=keep&feature=variant"
    clean = safe_source_url(source)
    assert "VERY_SECRET" not in clean and "api_key" not in clean
    assert "si=keep" in clean and "feature=variant" in clean
    assert "VERY_SECRET" not in canonical_source_url(source)
