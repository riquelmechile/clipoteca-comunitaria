"""Manual live smoke; not called by CI."""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from clipoteca.studio.related import related_search_query, resolve_jobs


async def main(queries, *, batch=False):
    groups = [queries] if batch else [[query] for query in queries]
    for group in groups:
        jobs = [{"queries": {"en": [query], "es": []}} for query in group]
        started = time.monotonic()
        diagnostics = await resolve_jobs(jobs)
        elapsed = round(time.monotonic() - started, 3)
        rows = [
            {
                "query": query,
                **diagnostic,
                "related_terms": [
                    {**row, "search_query": related_search_query(job, row["term"])}
                    for row in job["related_terms"]
                ],
            }
            for query, job, diagnostic in zip(group, jobs, diagnostics, strict=True)
        ]
        print(
            json.dumps(
                {"batch": batch, "elapsed_s": elapsed, "results": rows},
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", action="store_true", help="Resolve all queries in one render")
    parser.add_argument("queries", nargs="+")
    args = parser.parse_args()
    asyncio.run(main(args.queries, batch=args.batch))
