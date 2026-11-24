"""Manual live smoke; not called by CI."""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from clipoteca.studio.related import related_search_query, resolve_jobs


async def main(queries):
    for query in queries:
        job = {"queries": {"en": [query], "es": []}}
        started = time.monotonic()
        diagnostics = await resolve_jobs([job])
        print(
            json.dumps(
                {
                    "query": query,
                    "elapsed_s": round(time.monotonic() - started, 3),
                    **diagnostics[0],
                    "related_terms": [
                        {**row, "search_query": related_search_query(job, row["term"])}
                        for row in job["related_terms"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queries", nargs="+")
    asyncio.run(main(parser.parse_args().queries))
