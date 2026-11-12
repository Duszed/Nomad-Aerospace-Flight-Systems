"""Nomad Atlas - spray-record platform (prototype).

Pipeline: receive (DataFlash .bin / gateway NDJSON) -> compute coverage ->
signed record -> PDF -> Telegram -> revocable shares -> verify page.

Ground software only observes. Nothing here ever commands the aircraft.
"""

__version__ = "0.1.0"

SAMPLE_DATA_NOTICE = "Sample data – not a customer record"
