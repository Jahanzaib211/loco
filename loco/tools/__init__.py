"""Agentic toolkit for Loco — real tools any agent can call.

Rules: read-only by default · bounded I/O · no shell · localhost-only network
(unless LOCO_TOOLS_ALLOW_NET=1) · everything deterministic.
"""
from . import summarizer, search, calculator, registry  # noqa: F401