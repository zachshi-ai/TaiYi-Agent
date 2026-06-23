"""Prometheus-style metrics (stdlib, no client library).

Counters and gauges support labels; a simple histogram tracks count/sum/buckets.
`render_prometheus` emits the standard text exposition format, so a `/metrics`
endpoint can be scraped without any third-party dependency.
"""
from __future__ import annotations


def _key(labels: dict) -> tuple:
    return tuple(sorted(labels.items()))


def _fmt_labels(key: tuple) -> str:
    if not key:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in key)
    return "{" + inner + "}"


class Counter:
    def __init__(self, name: str, help: str = ""):
        self.name = name
        self.help = help
        self.values: dict[tuple, float] = {}

    def inc(self, amount: float = 1.0, **labels) -> None:
        k = _key(labels)
        self.values[k] = self.values.get(k, 0.0) + amount

    def value(self, **labels) -> float:
        return self.values.get(_key(labels), 0.0)

    def render(self) -> list[str]:
        lines = [f"# TYPE {self.name} counter"]
        if not self.values:
            lines.append(f"{self.name} 0")
        for k, v in sorted(self.values.items()):
            lines.append(f"{self.name}{_fmt_labels(k)} {v:g}")
        return lines


class Gauge:
    def __init__(self, name: str, help: str = ""):
        self.name = name
        self.help = help
        self.values: dict[tuple, float] = {}

    def set(self, value: float, **labels) -> None:
        self.values[_key(labels)] = value

    def value(self, **labels) -> float:
        return self.values.get(_key(labels), 0.0)

    def render(self) -> list[str]:
        lines = [f"# TYPE {self.name} gauge"]
        for k, v in sorted(self.values.items()):
            lines.append(f"{self.name}{_fmt_labels(k)} {v:g}")
        return lines


class Histogram:
    def __init__(self, name: str, help: str = "", buckets: tuple[float, ...] = (0.01, 0.05, 0.1, 0.5, 1, 5)):
        self.name = name
        self.help = help
        self.buckets = buckets
        self.count = 0
        self.sum = 0.0
        self.bucket_counts = [0] * len(buckets)

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        for i, b in enumerate(self.buckets):
            if value <= b:
                self.bucket_counts[i] += 1

    def render(self) -> list[str]:
        lines = [f"# TYPE {self.name} histogram"]
        for b, c in zip(self.buckets, self.bucket_counts):
            lines.append(f'{self.name}_bucket{{le="{b:g}"}} {c}')
        lines.append(f'{self.name}_bucket{{le="+Inf"}} {self.count}')
        lines.append(f"{self.name}_count {self.count}")
        lines.append(f"{self.name}_sum {self.sum:g}")
        return lines


class MetricsRegistry:
    def __init__(self):
        self._metrics: dict[str, Counter | Gauge | Histogram] = {}

    def counter(self, name: str, help: str = "") -> Counter:
        return self._metrics.setdefault(name, Counter(name, help))  # type: ignore[return-value]

    def gauge(self, name: str, help: str = "") -> Gauge:
        return self._metrics.setdefault(name, Gauge(name, help))  # type: ignore[return-value]

    def histogram(self, name: str, help: str = "") -> Histogram:
        return self._metrics.setdefault(name, Histogram(name, help))  # type: ignore[return-value]

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for metric in self._metrics.values():
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"
