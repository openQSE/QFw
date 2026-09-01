"""
OTLP/JSON file exporters.

The OpenTelemetry Python SDK can send OTLP over HTTP and can print spans to a
console, but it has no exporter that writes OTLP to a file. The file profile
in docs/benchmarking-design.md needs exactly that, so this module supplies
it for traces and for metrics.

Output is one JSON object per line. Each line is a complete OTLP export
request, so a line holds a whole batch, and the resource block is written
once per batch rather than once per span. Writing per-span JSON instead would
repeat the resource on every record, which grows into the dominant cost once
the run-context attributes land on the resource.

Importing this module requires the OpenTelemetry SDK. Import it lazily, only
once a profile other than off has been selected, so QFw still runs when the
SDK is absent.

On hex identifiers: OTLP/JSON deviates from the standard Protobuf JSON
mapping and requires trace and span identifiers as hex strings, where
protobuf would encode those bytes fields as base64. MessageToDict produces
base64, so _hexify_ids rewrites them. Without that step the output is
protobuf JSON of an OTLP message rather than OTLP/JSON, and a collector
reading it back would reject the identifiers.
"""

import base64
import json
import threading

from google.protobuf.json_format import MessageToDict
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import (
	encode_metrics)
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.metrics.export import (
	AggregationTemporality, MetricExporter, MetricExportResult)
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

# Keys whose values are identifier bytes in the OTLP schema. Covers spans,
# span links, log records, and metric exemplars.
_ID_KEYS = ("traceId", "spanId", "parentSpanId")


def _hexify_ids(node):
	"""Rewrite base64 identifier fields to the hex form OTLP/JSON requires."""
	if isinstance(node, dict):
		for key, value in node.items():
			if key in _ID_KEYS and isinstance(value, str):
				try:
					node[key] = base64.b64decode(value).hex()
				except (ValueError, TypeError):
					# Already hex, or not an identifier we recognise. Leave
					# it alone rather than corrupting it.
					pass
			else:
				_hexify_ids(value)
	elif isinstance(node, list):
		for item in node:
			_hexify_ids(item)
	return node


def _write(stream, lock, message):
	payload = _hexify_ids(MessageToDict(message))
	line = json.dumps(payload, separators=(",", ":"))
	with lock:
		stream.write(line + "\n")
		stream.flush()


class OtlpJsonFileSpanExporter(SpanExporter):
	"""Writes each exported batch of spans as one OTLP/JSON line."""

	def __init__(self, stream):
		self._stream = stream
		self._lock = threading.Lock()

	def export(self, spans):
		if not spans:
			return SpanExportResult.SUCCESS
		try:
			_write(self._stream, self._lock, encode_spans(spans))
		except Exception:
			# An export failure must never take down the workload being
			# measured. The batch processor logs and moves on.
			return SpanExportResult.FAILURE
		return SpanExportResult.SUCCESS

	def force_flush(self, timeout_millis=30000):
		return True

	def shutdown(self):
		pass


class OtlpJsonFileMetricExporter(MetricExporter):
	"""Writes each collection cycle of metrics as one OTLP/JSON line."""

	def __init__(self, stream):
		super().__init__(
			preferred_temporality={},
			preferred_aggregation={})
		self._stream = stream
		self._lock = threading.Lock()

	def export(self, metrics_data, timeout_millis=10000, **kwargs):
		if metrics_data is None:
			return MetricExportResult.SUCCESS
		try:
			_write(self._stream, self._lock, encode_metrics(metrics_data))
		except Exception:
			return MetricExportResult.FAILURE
		return MetricExportResult.SUCCESS

	def force_flush(self, timeout_millis=10000):
		return True

	def shutdown(self, timeout_millis=30000, **kwargs):
		pass


__all__ = [
	"AggregationTemporality",
	"OtlpJsonFileMetricExporter",
	"OtlpJsonFileSpanExporter",
]
