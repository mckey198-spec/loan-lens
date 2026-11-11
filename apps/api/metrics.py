"""Prometheus-compatible process metrics without an unapproved third-party client.

TOOLS.md does not approve ``prometheus_client``. This module exposes the text
exposition format with a small in-process collector (Architecture §13).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Iterable

from fastapi import APIRouter, Response

# Histogram bucket upper bounds in seconds (inclusive).
_DURATION_BUCKETS: tuple[float, ...] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    float("inf"),
)


def _label_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _escape_label_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [
        f'{name}="{_escape_label_value(value)}"'
        for name, value in sorted(labels.items())
    ]
    return "{" + ",".join(parts) + "}"


class _Counter:
    def __init__(self, name: str, documentation: str, label_names: tuple[str, ...]) -> None:
        self.name = name
        self.documentation = documentation
        self.label_names = label_names
        self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def labels(self, **labels: str) -> _CounterChild:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"{self.name}: expected labels {self.label_names}, got {tuple(labels)}"
            )
        return _CounterChild(self, labels)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        self.labels(**labels).inc(amount)

    def collect_lines(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            label_map = dict(key)
            lines.append(f"{self.name}{_format_labels(label_map)} {value}")
        if not items:
            # Emit a zero sample so scrapers see the metric family.
            if self.label_names:
                zero_labels = {name: "" for name in self.label_names}
                lines.append(f"{self.name}{_format_labels(zero_labels)} 0")
            else:
                lines.append(f"{self.name} 0")
        return lines


class _CounterChild:
    def __init__(self, parent: _Counter, labels: dict[str, str]) -> None:
        self._parent = parent
        self._key = _label_key(labels)

    def inc(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        with self._parent._lock:
            self._parent._values[self._key] += float(amount)


class _Histogram:
    def __init__(self, name: str, documentation: str, label_names: tuple[str, ...]) -> None:
        self.name = name
        self.documentation = documentation
        self.label_names = label_names
        self._lock = threading.Lock()
        self._counts: dict[tuple[tuple[str, str], ...], list[float]] = {}
        self._sums: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self._observations: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def labels(self, **labels: str) -> _HistogramChild:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"{self.name}: expected labels {self.label_names}, got {tuple(labels)}"
            )
        return _HistogramChild(self, labels)

    def observe(self, value: float, **labels: str) -> None:
        self.labels(**labels).observe(value)

    def collect_lines(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            keys = sorted(self._counts.keys())
            snapshot = {
                key: (list(self._counts[key]), self._sums[key], self._observations[key])
                for key in keys
            }
        if not snapshot:
            empty = {name: "" for name in self.label_names} if self.label_names else {}
            cumulative = 0.0
            for bound in _DURATION_BUCKETS:
                bucket_labels = dict(empty)
                bucket_labels["le"] = "+Inf" if bound == float("inf") else f"{bound:g}"
                lines.append(
                    f"{self.name}_bucket{_format_labels(bucket_labels)} {cumulative}"
                )
            lines.append(f"{self.name}_sum{_format_labels(empty)} 0")
            lines.append(f"{self.name}_count{_format_labels(empty)} 0")
            return lines

        for key, (buckets, total_sum, count) in snapshot.items():
            base_labels = dict(key)
            cumulative = 0.0
            for index, bound in enumerate(_DURATION_BUCKETS):
                cumulative += buckets[index]
                bucket_labels = dict(base_labels)
                bucket_labels["le"] = "+Inf" if bound == float("inf") else f"{bound:g}"
                lines.append(
                    f"{self.name}_bucket{_format_labels(bucket_labels)} {cumulative}"
                )
            lines.append(f"{self.name}_sum{_format_labels(base_labels)} {total_sum}")
            lines.append(f"{self.name}_count{_format_labels(base_labels)} {count}")
        return lines


class _HistogramChild:
    def __init__(self, parent: _Histogram, labels: dict[str, str]) -> None:
        self._parent = parent
        self._key = _label_key(labels)

    def observe(self, value: float) -> None:
        if value < 0:
            raise ValueError("histogram observations must be non-negative")
        with self._parent._lock:
            buckets = self._parent._counts.get(self._key)
            if buckets is None:
                buckets = [0.0] * len(_DURATION_BUCKETS)
                self._parent._counts[self._key] = buckets
            for index, bound in enumerate(_DURATION_BUCKETS):
                if value <= bound:
                    buckets[index] += 1.0
                    break
            self._parent._sums[self._key] += float(value)
            self._parent._observations[self._key] += 1.0


applications_status_total = _Counter(
    "loanlens_applications_status_total",
    "Count of application status transitions by resulting status.",
    ("status",),
)

processing_runs_duration_seconds = _Histogram(
    "loanlens_processing_runs_duration_seconds",
    "Worker processing run wall-clock duration in seconds.",
    ("terminal_status",),
)

verification_findings_total = _Counter(
    "loanlens_verification_findings_total",
    "Verification findings by rule_code and status.",
    ("rule_code", "status"),
)

assessments_total = _Counter(
    "loanlens_assessments_total",
    "Assessment decisions by recommendation.",
    ("recommendation",),
)

_METRIC_FAMILIES: tuple[_Counter | _Histogram, ...] = (
    applications_status_total,
    processing_runs_duration_seconds,
    verification_findings_total,
    assessments_total,
)


def record_application_status(status: str) -> None:
    applications_status_total.labels(status=status).inc()


def record_processing_run_duration(
    duration_seconds: float,
    *,
    terminal_status: str,
) -> None:
    processing_runs_duration_seconds.labels(
        terminal_status=terminal_status
    ).observe(duration_seconds)


def record_verification_finding(*, rule_code: str, status: str) -> None:
    verification_findings_total.labels(rule_code=rule_code, status=status).inc()


def record_assessment_recommendation(recommendation: str) -> None:
    assessments_total.labels(recommendation=recommendation).inc()


def render_metrics() -> str:
    lines: list[str] = []
    for family in _METRIC_FAMILIES:
        lines.extend(family.collect_lines())
    lines.append("")
    return "\n".join(lines)


def reset_metrics_for_tests() -> None:
    """Clear in-memory samples between tests."""
    for family in _METRIC_FAMILIES:
        if isinstance(family, _Counter):
            with family._lock:
                family._values.clear()
        else:
            with family._lock:
                family._counts.clear()
                family._sums.clear()
                family._observations.clear()


router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics_endpoint() -> Response:
    """Prometheus text exposition."""
    body = render_metrics()
    return Response(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


class ProcessingRunTimer:
    """Context helper that observes run duration on close."""

    def __init__(self) -> None:
        self._started = time.perf_counter()

    def observe(self, terminal_status: str) -> None:
        elapsed = time.perf_counter() - self._started
        record_processing_run_duration(elapsed, terminal_status=terminal_status)


def iter_metric_names() -> Iterable[str]:
    return (family.name for family in _METRIC_FAMILIES)
