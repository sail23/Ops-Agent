"""Load OpenRCA dataset into Graph RAG knowledge base.

This script:
1. Loads query.csv for root cause information
2. Loads record.csv for fault records
3. Loads telemetry data (logs, metrics, traces)
4. Stores fault cases in Neo4j via Graph RAG

Usage:
    # Download dataset first:
    gdown https://drive.google.com/uc?id=1enBrdPT3wLG94ITGbSOwUFg9fkLR-16R  # Bank
    gdown https://drive.google.com/uc?id=1cyOKpqyAP4fy-QiJ6a_cKuwR7D46zyVe  # Telecom

    # Set PYTHONPATH and run:
    set PYTHONPATH=src;. && python -m power_aiops.scripts.load_openrca_data

    # Or with custom path:
    python -m power_aiops.scripts.load_openrca_data --dataset dataset/Bank
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from power_aiops.config import get_settings
from power_aiops.integrations.openrca import OpenRCAClient
from power_aiops.memory.graph_rag import FaultCase, GraphRAG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_scoring_points(text: str) -> dict[str, str]:
    """Extract root cause info from scoring_points text."""
    result: dict[str, str] = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if "root cause occurrence time" in line.lower():
            result["datetime"] = line
        elif "root cause component" in line.lower():
            parts = line.split(" is ")
            result["component"] = parts[-1].strip() if len(parts) > 1 else line
        elif "root cause reason" in line.lower():
            parts = line.split(" is ")
            result["reason"] = parts[-1].strip() if len(parts) > 1 else line
    return result


def _parse_instruction_date(instruction: str) -> str:
    """Extract date from instruction text."""
    import re
    m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4}', instruction)
    if m:
        return m.group(0)
    m = re.search(r'\d{4}-\d{2}-\d{2}', instruction)
    return m.group(0) if m else ""


def convert_query_to_case(task_index: str, instruction: str, scoring_points: str) -> dict[str, Any]:
    """Convert OpenRCA benchmark query to fault case dict."""
    info = _parse_scoring_points(scoring_points)
    component = info.get("component", "")
    reason = info.get("reason", "")
    rca_datetime = info.get("datetime", "")
    case_date = _parse_instruction_date(instruction)

    title = f"RCA: {component} - {reason[:60]}" if component else f"RCA Task {task_index}"
    return {
        "case_id": f"OPENRCA-{task_index}",
        "title": title,
        "summary": instruction[:500] if instruction else "",
        "symptoms": [
            reason or "see scoring_points",
            f"Affected component: {component}" if component else "",
        ],
        "services": [component] if component else [],
        "hosts": [],
        "root_cause": reason or "see scoring_points",
        "resolution": "",
        "severity": "P2",
        "duration_minutes": 0,
        "tags": ["openrca", "benchmark"],
        "metadata": {
            "task_index": task_index,
            "rca_datetime": rca_datetime,
            "component": component,
            "case_date": case_date,
        },
    }


def convert_record_to_case(record: "RecordFault") -> dict[str, Any]:
    """Convert OpenRCA fault injection record to fault case dict."""
    return {
        "case_id": f"OPENRCA-REC-{record.timestamp}-{record.component}",
        "title": f"[{record.level}] {record.component} — {record.reason}",
        "summary": f"{record.datetime}: {record.level} {record.component} experienced {record.reason}",
        "symptoms": [
            f"Level: {record.level}",
            f"Component: {record.component}",
            f"Reason: {record.reason}",
        ],
        "services": [record.component],
        "hosts": [],
        "root_cause": record.reason,
        "resolution": "",
        "severity": "P1" if record.level == "node" else "P2",
        "duration_minutes": 0,
        "tags": ["openrca", "fault-record", record.level],
        "metadata": {
            "level": record.level,
            "component": record.component,
            "timestamp": record.timestamp,
            "datetime": record.datetime,
        },
    }


def load_openrca_queries(rag: GraphRAG, client: OpenRCAClient) -> int:
    """Load OpenRCA query records as fault cases."""
    queries = client.load_queries()
    if not queries:
        logger.warning("No queries found to load")
        return 0

    logger.info(f"Processing {len(queries)} OpenRCA queries...")

    for query in queries:
        case_data = convert_query_to_case(
            task_index=query.task_index,
            instruction=query.instruction,
            scoring_points=query.scoring_points,
        )
        rag.store_case_dict(case_data)

    logger.info(f"Loaded {len(queries)} queries as fault cases")
    return len(queries)


def load_openrca_records(rag: GraphRAG, client: OpenRCAClient) -> int:
    """Load OpenRCA fault records as fault cases."""
    records = client.load_records()
    if not records:
        logger.warning("No records found to load")
        return 0

    logger.info(f"Processing {len(records)} OpenRCA fault records...")

    for record in records:
        case_data = convert_record_to_case(record)
        rag.store_case_dict(case_data)

    logger.info(f"Loaded {len(records)} records as fault cases")
    return len(records)


def summarize_telemetry(client: OpenRCAClient, date: str) -> dict[str, Any]:
    """Summarize telemetry data for a specific date (count only, don't load all data)."""
    summary = {
        "date": date,
        "error_logs": 0,
        "total_logs": 0,
        "metric_points": 0,
        "trace_spans": 0,
    }

    # Count logs without keeping in memory
    for _log in client.iter_log_events(date):
        summary["total_logs"] += 1
        if summary["total_logs"] % 100000 == 0:
            logger.info(f"  counted {summary['total_logs']} logs...")

    # Count metrics without keeping in memory
    for _pt in client.iter_metric_points(date):
        summary["metric_points"] += 1

    # Count trace spans without keeping in memory
    for _span in client.iter_trace_spans(date):
        summary["trace_spans"] += 1

    return summary


def load_telemetry_summary(rag: GraphRAG, client: OpenRCAClient) -> int:
    """Load telemetry data summary as events."""
    dates = client.list_available_dates()
    if not dates:
        logger.warning("No telemetry dates found")
        return 0

    logger.info(f"Processing telemetry for {len(dates)} dates...")

    for date in dates:
        summary = summarize_telemetry(client, date)
        if summary["error_logs"] > 0 or summary["total_logs"] > 0:
            logger.info(f"  {date}: {summary['total_logs']} logs, "
                       f"{summary['metric_points']} metrics, "
                       f"{summary['trace_spans']} trace spans")

    return len(dates)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load OpenRCA dataset into Graph RAG")
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Dataset path (e.g., dataset/Bank)",
    )
    parser.add_argument(
        "--system",
        type=str,
        default=None,
        choices=["Bank", "Telecom", "Market"],
        help="System type",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="all",
        choices=["all", "queries", "records", "telemetry"],
        help="Load mode",
    )
    args = parser.parse_args()

    settings = get_settings()

    # Determine dataset path
    if args.dataset:
        dataset_path = args.dataset
        system = args.dataset.split("/")[-1] if "/" in args.dataset else args.dataset
    else:
        dataset_path = settings.openrca_dataset_path
        system = args.system or settings.openrca_system

    logger.info(f"Loading OpenRCA dataset: {dataset_path}/{system}")

    # Initialize OpenRCA client
    client = OpenRCAClient(dataset_path=dataset_path, system=system)

    # Get dataset info
    info = client.get_dataset_info()
    logger.info(f"Dataset info: {info}")

    if info["num_queries"] == 0 and info["num_records"] == 0:
        logger.error("No data found in dataset. Please download the dataset first:")
        logger.error("  gdown https://drive.google.com/uc?id=1enBrdPT3wLG94ITGbSOwUFg9fkLR-16R  # Bank")
        logger.error("  gdown https://drive.google.com/uc?id=1cyOKpqyAP4fy-QiJ6a_cKuwR7D46zyVe  # Telecom")
        logger.error("  Then extract to: dataset/Bank/ or dataset/Telecom/")
        return 1

    # Initialize Graph RAG
    rag = GraphRAG()
    try:
        rag.initialize_schema()

        total_cases = 0

        if args.mode in ("all", "queries"):
            total_cases += load_openrca_queries(rag, client)

        if args.mode in ("all", "records"):
            total_cases += load_openrca_records(rag, client)

        if args.mode in ("all", "telemetry"):
            load_telemetry_summary(rag, client)

        logger.info(f"\n{'='*60}")
        logger.info(f"Total cases loaded: {total_cases}")

        # Print stats
        stats = rag.get_stats()
        logger.info(f"\nKnowledge Base Statistics:")
        logger.info(f"  Total Cases: {stats['total_cases']}")
        logger.info(f"  Total Symptoms: {stats['total_symptoms']}")
        logger.info(f"  Total Root Causes: {stats['total_root_causes']}")
        logger.info(f"  Total Services: {stats['total_services']}")

        return 0

    finally:
        rag.close()


if __name__ == "__main__":
    sys.exit(main())
