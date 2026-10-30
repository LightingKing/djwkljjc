from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import yaml

from tools.audit_local_evidence import audit


class LocalEvidenceAuditTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, bad: bool = False) -> tuple[Path, Path, Path]:
        contracts = root / "contracts.yaml"
        coverage = root / "coverage.json"
        smoke = root / "smoke.json"
        contracts.write_text(yaml.safe_dump({"interfaces": [{"interface": "One"}, {"interface": "Two"}]}), encoding="utf-8")
        coverage.write_text(json.dumps({
            "totalInterfaces": 2,
            "implementedInterfaces": 1,
            "unimplementedInterfaces": 1,
            "coverage": 0.5,
            "interfaces": [
                {"interface": "One", "status": "implemented"},
                {"interface": "Two", "status": "unimplemented"},
            ],
        }), encoding="utf-8")
        smoke.write_text(json.dumps({
            "total": 2,
            "passed": 1 if bad else 2,
            "results": [
                {"interface": "One", "passed": True, "status": 501, "payload": {"error": "route_not_implemented"}},
                {"interface": "Two", "passed": not bad, "status": 501, "payload": {"error": "route_not_implemented"}},
            ],
        }), encoding="utf-8")
        return contracts, coverage, smoke

    def test_audit_accepts_matching_implementation_and_safety_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self._write_fixture(Path(temp))
            report = audit(contracts=paths[0], route_coverage=paths[1], contract_report=paths[2])
            self.assertTrue(report["passed"])
            self.assertEqual(report["contractSmoke"]["mode"], "unregistered_route_safety")

    def test_audit_rejects_failed_contract_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self._write_fixture(Path(temp), bad=True)
            report = audit(contracts=paths[0], route_coverage=paths[1], contract_report=paths[2])
            self.assertFalse(report["passed"])
            self.assertFalse(report["checks"]["contractSmokePassed"])


if __name__ == "__main__":
    unittest.main()
