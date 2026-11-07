"""Repeatable smoke/latency check for a shared public response cache.

Example:
    python scripts/check_multi_instance_cache.py \
        --base-url-a http://127.0.0.1:18081 \
        --base-url-b http://127.0.0.1:18083
"""
import argparse
import json
import statistics
import time

import httpx


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url-a", required=True)
    parser.add_argument("--base-url-b", required=True)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--max-p95-ms", type=float, default=500.0,
                        help="local isolated-stack smoke threshold (default: 500 ms)")
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("--samples must be at least 2")

    urls = [args.base_url_a.rstrip("/"), args.base_url_b.rstrip("/")]
    latencies: list[float] = []
    statuses: list[int] = []
    cache_headers: list[str] = []
    with httpx.Client(timeout=10) as client:
        for index in range(args.samples):
            started = time.perf_counter()
            response = client.get(urls[index % 2] + "/api/categories")
            latencies.append((time.perf_counter() - started) * 1000)
            statuses.append(response.status_code)
            cache_headers.append(response.headers.get("X-Cache", "NONE"))

    p95 = percentile(latencies, 0.95)
    report = {
        "samples": args.samples,
        "instances": 2,
        "http_200": statuses.count(200),
        "errors": len(statuses) - statuses.count(200),
        "cache_hits": cache_headers.count("HIT"),
        "cache_misses": cache_headers.count("MISS"),
        "other_cache_header": cache_headers.count("NONE"),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2),
            "p95": round(p95, 2),
            "max": round(max(latencies), 2),
        },
        "p95_threshold_ms": args.max_p95_ms,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["errors"] == 0 and p95 <= args.max_p95_ms else 1


if __name__ == "__main__":
    raise SystemExit(main())
