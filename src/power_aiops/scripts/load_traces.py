"""Load trace data from OpenRCA dataset into Neo4j.

This script:
1. Reads trace data from OpenRCA telemetry
2. Stores traces and spans in Neo4j
3. Links traces to related fault cases

Usage:
    python -m power_aiops.scripts.load_traces --date 2021_03_04

    # Load all dates
    python -m power_aiops.scripts.load_traces --mode all
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from power_aiops.config import get_settings
from power_aiops.integrations.openrca import OpenRCAClient
from power_aiops.memory.graph_rag import GraphRAG, TraceSpan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_traces_for_date(
    rag: GraphRAG,
    client: OpenRCAClient,
    date: str,
    sample_limit: int = 0,
) -> int:
    """Load traces for a specific date.

    Args:
        rag: GraphRAG instance
        client: OpenRCA client
        date: Date string (YYYY_MM_DD)
        sample_limit: Limit number of traces (0 = no limit)

    Returns:
        Number of traces loaded
    """
    logger.info(f"Loading traces for date: {date}")

    trace_spans = {}  # trace_id -> list of spans
    count = 0

    for span in client.iter_trace_spans(date):
        if span.trace_id not in trace_spans:
            trace_spans[span.trace_id] = []

        trace_span = TraceSpan(
            span_id=span.span_id,
            trace_id=span.trace_id,
            parent_span_id=span.parent_span_id,
            service=span.service,
            operation=span.operation,
            start_time=span.start_time,
            duration_ms=span.duration_ms,
            status=span.status if span.status else "OK",
            error_message=span.tags.get("error", ""),
            tags=span.tags,
        )
        trace_spans[span.trace_id].append(trace_span)

        if sample_limit > 0 and len(trace_spans) >= sample_limit:
            break

    # Store each trace
    for trace_id, spans in trace_spans.items():
        try:
            rag.store_trace(trace_id, spans)
            count += 1

            if count % 100 == 0:
                logger.info(f"  Loaded {count} traces...")

        except Exception as e:
            logger.error(f"Failed to store trace {trace_id}: {e}")

    logger.info(f"Loaded {count} traces with {sum(len(s) for s in trace_spans.values())} spans")
    return count


def load_error_traces_for_date(
    rag: GraphRAG,
    client: OpenRCAClient,
    date: str,
) -> int:
    """Load only traces with errors for a specific date.

    Args:
        rag: GraphRAG instance
        client: OpenRCA client
        date: Date string (YYYY_MM_DD)

    Returns:
        Number of error traces loaded
    """
    logger.info(f"Loading error traces for date: {date}")

    error_traces = {}  # trace_id -> list of spans

    for span in client.iter_trace_spans(date):
        # Filter for error status
        if span.status and span.status.upper() in ("ERROR", "TIMEOUT", "FAILED"):
            if span.trace_id not in error_traces:
                error_traces[span.trace_id] = []

            trace_span = TraceSpan(
                span_id=span.span_id,
                trace_id=span.trace_id,
                parent_span_id=span.parent_span_id,
                service=span.service,
                operation=span.operation,
                start_time=span.start_time,
                duration_ms=span.duration_ms,
                status=span.status,
                error_message=span.tags.get("error", "") or span.tags.get("message", ""),
                tags=span.tags,
            )
            error_traces[span.trace_id].append(trace_span)

    # Store error traces
    count = 0
    for trace_id, spans in error_traces.items():
        try:
            rag.store_trace(trace_id, spans)
            count += 1
        except Exception as e:
            logger.error(f"Failed to store error trace {trace_id}: {e}")

    logger.info(f"Loaded {count} error traces")
    return count


def analyze_trace_stats(rag: GraphRAG) -> dict:
    """Analyze and display trace statistics."""
    logger.info("\n" + "=" * 60)
    logger.info("Trace Analysis Summary")
    logger.info("=" * 60)

    stats = rag.get_stats()

    logger.info(f"\nTotal Traces: {stats.get('total_traces', 0)}")
    logger.info(f"Total Spans: {stats.get('total_spans', 0)}")
    logger.info(f"Error Spans: {stats.get('error_spans', 0)}")

    # Get slow traces
    slow = rag.get_slow_traces(min_duration_ms=5000, limit=5)
    if slow:
        logger.info(f"\nTop 5 Slow Traces (>5s):")
        for t in slow:
            logger.info(f"  {t['trace_id']}: {t['duration_ms']:.0f}ms, "
                       f"{t['total_spans']} spans")

    # Get error traces
    errors = rag.get_error_traces(limit=5)
    if errors:
        logger.info(f"\nTop 5 Error Traces:")
        for t in errors:
            logger.info(f"  {t['trace_id']}: {t['error_spans']} errors, "
                       f"{t['total_spans']} spans")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Load trace data from OpenRCA")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Specific date to load (YYYY_MM_DD)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="all",
        choices=["all", "errors"],
        help="Load mode: all traces or only errors",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of traces to load (0 = no limit)",
    )
    parser.add_argument(
        "--system",
        type=str,
        default=None,
        choices=["Bank", "Telecom", "Market"],
        help="OpenRCA system type",
    )
    args = parser.parse_args()

    settings = get_settings()
    system = args.system or settings.openrca_system

    logger.info(f"Initializing OpenRCA client for system: {system}")

    client = OpenRCAClient(
        dataset_path=settings.openrca_dataset_path,
        system=system,
    )

    # Check available dates
    dates = client.list_available_dates()
    if not dates:
        logger.error("No telemetry dates found. Please download OpenRCA dataset first.")
        return 1

    logger.info(f"Found {len(dates)} dates: {dates[:3]}...")

    # Initialize Graph RAG
    rag = GraphRAG()
    try:
        rag.initialize_schema()

        total_traces = 0

        if args.date:
            # Load specific date
            if args.mode == "errors":
                total_traces = load_error_traces_for_date(rag, client, args.date)
            else:
                total_traces = load_traces_for_date(rag, client, args.date, args.limit)
        else:
            # Load all dates
            for date in dates:
                if args.mode == "errors":
                    count = load_error_traces_for_date(rag, client, date)
                else:
                    count = load_traces_for_date(rag, client, date, args.limit)
                total_traces += count

        logger.info(f"\nTotal traces loaded: {total_traces}")

        # Analyze
        analyze_trace_stats(rag)

        return 0

    finally:
        rag.close()


if __name__ == "__main__":
    sys.exit(main())