"""SOWSmith — deterministic Statement of Work generator from OrbitBrief envelopes.

Public API:
  * ``build_sow_markdown(envelope)`` — produce a contract-grade SOW
    markdown document from a parser-os ``orbitbrief.input.v2`` envelope
  * ``SOW_VERSION`` — the schema-stable identifier for this renderer
"""
from sowsmith.render import SOW_VERSION, build_sow_markdown

__all__ = ["build_sow_markdown", "SOW_VERSION"]
__version__ = "0.1.0"
