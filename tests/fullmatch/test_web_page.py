# ruff: noqa: E501  -- node harness strings contain long JavaScript assertions
"""Contract tests for the FastAPI-served full-match browser uploader page."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from football_intelligence.api import create_app
from football_intelligence.fullmatch.web import (
    ENDPOINTS,
    PAGE_PATH,
    expected_part_size_bytes,
    page_html,
)
from football_intelligence.storage import JobRepository

PART_SIZE = 16 * 1024 * 1024


def make_app(tmp_path: Path):
    return create_app(repository=JobRepository(tmp_path / "jobs.db"), data_root=tmp_path)


def test_full_match_page_is_served_as_html(tmp_path: Path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.get(PAGE_PATH)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers.get("cache-control") == "no-store"
    assert 'id="fm-endpoints"' in response.text
    assert 'type="file"' in response.text
    assert "checksum" in response.text.lower()


def test_page_endpoint_contract_matches_api_routes(tmp_path: Path):
    app = make_app(tmp_path)
    route_paths = {route.path for route in app.routes}

    for name, path in ENDPOINTS.items():
        assert path in route_paths, f"page endpoint {name} ({path}) is not an API route"


def test_page_declares_endpoints_in_html():
    html = page_html()
    match = re.search(
        r'id="fm-endpoints" type="application/json">(.*?)</script>', html, re.S
    )
    assert match is not None, "page must embed the endpoints JSON contract"
    assert json.loads(match.group(1)) == ENDPOINTS


def test_page_embeds_js_lib_once():
    html = page_html()
    assert html.count("fm-js-lib-begin") == 1
    assert html.count("fm-js-lib-end") == 1


@pytest.mark.parametrize(
    ("size_bytes", "expected_sizes"),
    [
        (1, [1]),
        (PART_SIZE, [PART_SIZE]),
        (PART_SIZE + 1, [PART_SIZE, 1]),
        (3 * PART_SIZE, [PART_SIZE, PART_SIZE, PART_SIZE]),
        (3 * PART_SIZE + 7, [PART_SIZE, PART_SIZE, PART_SIZE, 7]),
        (10 * PART_SIZE - 1, [PART_SIZE] * 9 + [PART_SIZE - 1]),
    ],
)
def test_part_size_mirror_matches_server_contract(size_bytes, expected_sizes):
    part_count = len(expected_sizes)
    actual = [
        expected_part_size_bytes(size_bytes, part_number, part_count)
        for part_number in range(1, part_count + 1)
    ]
    assert actual == expected_sizes
    assert sum(actual) == size_bytes


_LIB_BEGIN = r"/\* ==== fm-js-lib-begin ==== \*/"
_LIB_END = r"/\* ==== fm-js-lib-end ==== \*/"


def _extract_js_lib() -> str:
    """Extract the exact embedded JS lib from the source file so the tests run
    without importing the package (hosts without the Python deps can still run
    the node checks)."""
    source_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "football_intelligence"
        / "fullmatch"
        / "web.py"
    )
    source = source_path.read_text(encoding="utf-8")
    match = re.search(_LIB_BEGIN + "(.*?)" + _LIB_END, source, re.S)
    assert match is not None, "web.py must embed the JS lib between its markers"
    return match.group(1)


def _run_node(script_body: str) -> str:
    harness = _extract_js_lib() + "\n" + script_body
    process = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for JS tests")
def test_js_sha256_matches_node_crypto_vectors():
    _run_node(
        """
        const crypto = require('crypto');
        function nodeHex(str) { return crypto.createHash('sha256').update(str, 'utf8').digest('hex'); }
        const encoder = new TextEncoder();
        function assertEqual(actual, expected, label) {
          if (actual !== expected) throw new Error(label + ': ' + actual + ' != ' + expected);
        }
        assertEqual(sha256Hex(encoder.encode('')), nodeHex(''), 'empty');
        assertEqual(sha256Hex(encoder.encode('abc')), nodeHex('abc'), 'abc');
        assertEqual(sha256Hex(encoder.encode('a'.repeat(1000000))), nodeHex('a'.repeat(1000000)), '1M a');
        // incremental feeding one byte at a time equals one-shot
        const incremental = new Sha256();
        for (let i = 0; i < 1000; i++) incremental.update(encoder.encode('x'));
        assertEqual(incremental.digestHex(), sha256Hex(encoder.encode('x'.repeat(1000))), 'incremental');
        console.log('sha256 vectors ok');
        """
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for JS tests")
def test_js_sha256_matches_node_crypto_random_cross_check():
    _run_node(
        """
        const crypto = require('crypto');
        const encoder = new TextEncoder();
        let seed = 12345;
        function next() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed; }
        for (let trial = 0; trial < 40; trial++) {
          const length = next() % 200000;
          let chars = '';
          for (let i = 0; i < length; i++) chars += String.fromCharCode(32 + (next() % 95));
          const expected = crypto.createHash('sha256').update(chars, 'utf8').digest('hex');
          const actual = sha256Hex(encoder.encode(chars));
          if (actual !== expected) throw new Error('mismatch at length ' + length);
        }
        console.log('random cross-check ok');
        """
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for JS tests")
def test_js_part_planner_matches_python_mirror():
    cases = [
        (1, 1, 1),
        (PART_SIZE, 1, 1),
        (PART_SIZE + 1, 1, 2),
        (PART_SIZE + 1, 2, 2),
        (3 * PART_SIZE + 7, 3, 4),
        (3 * PART_SIZE + 7, 4, 4),
        (10 * PART_SIZE - 1, 9, 10),
        (10 * PART_SIZE - 1, 10, 10),
    ]
    payload = json.dumps(cases)
    expected = json.dumps(
        [
            {
                "size": size_bytes,
                "part_number": part_number,
                "part_count": part_count,
                "expected": expected_part_size_bytes(
                    size_bytes, part_number, part_count
                ),
            }
            for size_bytes, part_number, part_count in cases
        ]
    )
    _run_node(
        f"""
        const cases = JSON.parse({json.dumps(payload)});
        const expected = JSON.parse({json.dumps(expected)});
        for (let i = 0; i < cases.length; i++) {{
          const [sizeBytes, partNumber, partCount] = cases[i];
          const actual = expectedPartSizeBytes(sizeBytes, partNumber, partCount);
          if (actual !== expected[i].expected) {{
            throw new Error('part planner mismatch for case ' + JSON.stringify(cases[i]));
          }}
        }}
        if (FM_PART_SIZE_BYTES !== 16777216) throw new Error('part size constant drift');
        console.log('part planner ok');
        """
    )


def test_page_requires_owner_header_seam(tmp_path: Path):
    """The page must call the upload API with X-Owner-ID; assert the seam is documented in the HTML."""
    html = page_html()
    assert "X-Owner-ID" in html
