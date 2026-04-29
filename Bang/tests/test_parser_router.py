from __future__ import annotations

from pathlib import Path

from app.core.compiler import compile_project
from app.parsers.registry import choose_parser


def test_site_list_routes_to_xlsx_parser(demo_project: Path) -> None:
    parser, match, _ = choose_parser(demo_project / "site_list.xlsx", domain_pack=None)
    assert parser is not None
    assert match.parser_name == "xlsx"


def test_vendor_quote_routes_to_quote_parser(demo_project: Path) -> None:
    parser, match, _ = choose_parser(demo_project / "vendor_quote.xlsx", domain_pack=None)
    assert parser is not None
    assert match.parser_name == "quote"
    assert match.confidence >= 0.8


def test_customer_email_routes_email(tmp_path: Path) -> None:
    artifact = tmp_path / "customer_email.txt"
    artifact.write_text("From: customer@example.com\nSent: Monday\nSubject: Scope\nNeed exclude west wing", encoding="utf-8")
    parser, match, _ = choose_parser(artifact, domain_pack=None)
    assert parser is not None
    assert match.parser_name == "email"


def test_kickoff_transcript_routes_transcript(tmp_path: Path) -> None:
    artifact = tmp_path / "kickoff_transcript.txt"
    artifact.write_text("Decisions:\n- Main campus first\nAction Items:\nAlex: schedule install", encoding="utf-8")
    parser, match, _ = choose_parser(artifact, domain_pack=None)
    assert parser is not None
    assert match.parser_name == "transcript"


def test_random_txt_produces_warning_no_crash(tmp_path: Path) -> None:
    artifact = tmp_path / "random.txt"
    artifact.write_text("just filler words with no structured signals", encoding="utf-8")
    result = compile_project(tmp_path, allow_errors=True)
    assert any("No parser matched artifact" in warning for warning in result.warnings)


def test_compile_trace_includes_parser_routing(tmp_path: Path) -> None:
    artifact = tmp_path / "customer_email.txt"
    artifact.write_text("From: customer@example.com\nSent: Monday\nSubject: Scope", encoding="utf-8")
    result = compile_project(tmp_path, allow_errors=True)
    assert result.trace is not None
    assert result.trace.parser_routing
    routing = result.trace.parser_routing[0]
    assert routing["filename"] == "customer_email.txt"
    assert routing["chosen_parser"] == "email"
