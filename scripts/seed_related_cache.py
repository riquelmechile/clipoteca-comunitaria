"""Resolve queries live and merge successful entries into the repository cache."""
import argparse
import asyncio
from pathlib import Path

from clipoteca.studio.related import resolve_jobs
from clipoteca.studio.related_cache import CACHE_PATH, load_cache, merge_entries, save_cache


async def seed(queries, path):
    jobs = [{"queries": {"en": [query], "es": []}} for query in queries]
    for offset in range(0, len(jobs), 4):
        await resolve_jobs(jobs[offset:offset + 4], persistent_cache={})
    entries = [entry for job in jobs for entry in job["related_cache_entries"]]
    save_cache(path, merge_entries(load_cache(path), entries))
    print(f"Resolved {len(entries)} cache entries")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queries", nargs="+")
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    args = parser.parse_args()
    asyncio.run(seed(args.queries, args.cache))
