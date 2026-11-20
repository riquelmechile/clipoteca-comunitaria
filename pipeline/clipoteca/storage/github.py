"""Idempotent GitHub Releases storage.

The adapter never logs credentials and verifies both GitHub metadata and the
published bytes before an asset may be added to the catalogue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, Self

import httpx


class StorageError(RuntimeError):
    """A release operation did not complete safely."""


@dataclass(frozen=True)
class ReleaseBatch:
    id: int
    tag: str
    upload_url: str
    assets: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReleaseAsset:
    id: int
    name: str
    size: int
    state: str
    browser_download_url: str


class GitHubReleaseStorage:
    """Small REST client with deterministic, verified uploads."""

    def __init__(
        self,
        repository: str,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
        api_url: str = "https://api.github.com",
    ) -> None:
        self.repository = repository
        self._owns_client = client is None
        credential = token if token is not None else os.environ.get("GITHUB_TOKEN")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "clipoteca/1",
        }
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        self.client = client or httpx.Client(headers=headers, timeout=30.0, follow_redirects=True)
        self.api_url = api_url.rstrip("/")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def releases_url(self) -> str:
        return f"{self.api_url}/repos/{self.repository}/releases"

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def get_or_create_batch_release(self, day: date) -> ReleaseBatch:
        prefix = f"batch-{day.isoformat()}-"
        releases = self._request("GET", self.releases_url, params={"per_page": 100}).json()
        matching = sorted(
            (item for item in releases if str(item.get("tag_name", "")).startswith(prefix)),
            key=lambda item: item["tag_name"],
        )
        for item in reversed(matching):
            release = self._batch(item)
            if len(self._list_assets(release.id)) < 1000:
                return release
        next_number = 1
        if matching:
            next_number = int(matching[-1]["tag_name"].rsplit("-", 1)[1]) + 1
        tag = f"{prefix}{next_number:03d}"
        payload = {"tag_name": tag, "name": tag, "draft": False, "prerelease": False}
        return self._batch(self._request("POST", self.releases_url, json=payload).json())

    @staticmethod
    def _batch(payload: dict[str, Any]) -> ReleaseBatch:
        return ReleaseBatch(
            id=int(payload["id"]),
            tag=str(payload["tag_name"]),
            upload_url=str(payload["upload_url"]).split("{", 1)[0],
            assets=tuple(payload.get("assets", ())),
        )

    @staticmethod
    def _asset(payload: dict[str, Any]) -> ReleaseAsset:
        return ReleaseAsset(
            id=int(payload["id"]),
            name=str(payload["name"]),
            size=int(payload["size"]),
            state=str(payload["state"]),
            browser_download_url=str(payload["browser_download_url"]),
        )

    def find_existing_asset(
        self, release: ReleaseBatch, name: str, size: int, digest: str
    ) -> ReleaseAsset | None:
        payload = self._list_assets(release.id)
        for raw in payload:
            if raw.get("name") != name:
                continue
            asset = self._asset(raw)
            if asset.state == "uploaded" and asset.size == size:
                downloaded = self._request("GET", asset.browser_download_url).content
                if sha256(downloaded).hexdigest() == digest:
                    return asset
            self.delete_asset(asset.id)
        return None

    def upload_asset(self, release: ReleaseBatch, path: Path, name: str) -> ReleaseAsset:
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        existing = self.find_existing_asset(release, name, len(data), digest)
        if existing:
            return existing
        response = self._request(
            "POST",
            release.upload_url,
            params={"name": name},
            headers={"Content-Type": "application/octet-stream"},
            content=data,
        )
        return self._asset(response.json())

    def verify_asset(self, name: str, size: int, digest: str, release_id: int) -> ReleaseAsset:
        payload = self._list_assets(release_id)
        raw = next((entry for entry in payload if entry.get("name") == name), None)
        if raw is None:
            raise StorageError(f"asset not found: {name}")
        asset = self._asset(raw)
        if asset.state != "uploaded" or asset.size != size:
            self.delete_asset(asset.id)
            raise StorageError(f"incomplete asset: {name}")
        body = self._request("GET", asset.browser_download_url).content
        if sha256(body).hexdigest() != digest:
            self.delete_asset(asset.id)
            raise StorageError(f"asset digest mismatch: {name}")
        return asset

    def delete_asset(self, asset_id: int) -> None:
        self._request(
            "DELETE",
            f"{self.api_url}/repos/{self.repository}/releases/assets/{asset_id}",
        )

    def _list_assets(self, release_id: int) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for page in range(1, 11):
            batch = self._request(
                "GET",
                f"{self.releases_url}/{release_id}/assets",
                params={"per_page": 100, "page": page},
            ).json()
            assets.extend(batch)
            if len(batch) < 100:
                break
        return assets
