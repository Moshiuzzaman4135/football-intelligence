# ruff: noqa: E501  -- node harness strings contain long JavaScript assertions
#!/usr/bin/env python3
"""Host-side verification of the browser page's embedded JavaScript.

The Docker test image does not ship Node, so the JS-level checks in
``tests/fullmatch/test_web_page.py`` are skipped inside the container. This
script runs the same checks directly on a host that has Node.js:

    python3 tools/check_web_js.py

It requires only the Python standard library plus ``node`` on PATH.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SOURCE = ROOT / "src" / "football_intelligence" / "fullmatch" / "web.py"
PART_SIZE = 16 * 1024 * 1024

LIB_BEGIN = r"/\* ==== fm-js-lib-begin ==== \*/"
LIB_END = r"/\* ==== fm-js-lib-end ==== \*/"


def extract_lib() -> str:
    source = WEB_SOURCE.read_text(encoding="utf-8")
    match = re.search(LIB_BEGIN + "(.*?)" + LIB_END, source, re.S)
    if match is None:
        raise SystemExit("web.py must embed the JS lib between its markers")
    return match.group(1)


def expected_part_size_bytes(size_bytes: int, part_number: int, part_count: int) -> int:
    if part_number < part_count:
        return PART_SIZE
    return size_bytes - PART_SIZE * (part_count - 1)


def run_node(script_body: str) -> None:
    harness = extract_lib() + "\n" + script_body
    process = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if process.returncode != 0:
        raise SystemExit(f"node check failed:\n{process.stderr}")
    print(process.stdout.strip())


def sha256_vectors() -> str:
    return """
        const crypto = require('crypto');
        function nodeHex(str) { return crypto.createHash('sha256').update(str, 'utf8').digest('hex'); }
        const encoder = new TextEncoder();
        function assertEqual(actual, expected, label) {
          if (actual !== expected) throw new Error(label + ': ' + actual + ' != ' + expected);
        }
        assertEqual(sha256Hex(encoder.encode('')), nodeHex(''), 'empty');
        assertEqual(sha256Hex(encoder.encode('abc')), nodeHex('abc'), 'abc');
        assertEqual(sha256Hex(encoder.encode('a'.repeat(1000000))), nodeHex('a'.repeat(1000000)), '1M a');
        const incremental = new Sha256();
        for (let i = 0; i < 1000; i++) incremental.update(encoder.encode('x'));
        assertEqual(incremental.digestHex(), sha256Hex(encoder.encode('x'.repeat(1000))), 'incremental');
        console.log('sha256 vectors ok');
        """


def sha256_random_cross_check() -> str:
    return """
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


def part_planner() -> str:
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
    expected = [
        expected_part_size_bytes(size_bytes, part_number, part_count)
        for size_bytes, part_number, part_count in cases
    ]
    payload = json.dumps(cases)
    expected_json = json.dumps(expected)
    return f"""
        const cases = JSON.parse({json.dumps(payload)});
        const expected = JSON.parse({json.dumps(expected_json)});
        for (let i = 0; i < cases.length; i++) {{
          const [sizeBytes, partNumber, partCount] = cases[i];
          const actual = expectedPartSizeBytes(sizeBytes, partNumber, partCount);
          if (actual !== expected[i]) {{
            throw new Error('part planner mismatch for case ' + JSON.stringify(cases[i]));
          }}
        }}
        if (FM_PART_SIZE_BYTES !== 16777216) throw new Error('part size constant drift');
        console.log('part planner ok');
        """


def main() -> None:
    checks = [
        ("sha256 vectors", sha256_vectors()),
        ("sha256 random cross-check", sha256_random_cross_check()),
        ("part planner", part_planner()),
    ]
    for name, script in checks:
        print(f"running: {name}")
        run_node(script)
    print("all web JS checks passed")


if __name__ == "__main__":
    main()
