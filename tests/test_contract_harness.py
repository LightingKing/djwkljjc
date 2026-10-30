from pathlib import Path
import json
import tempfile
import unittest

from server.core.routes import RouteRegistry
from tools.contract_harness import (
    ReplayFormatError,
    cases_from_contracts,
    read_replay,
    report,
    run_contract_smoke,
    run_replay,
    sample_path,
    sample_request,
    write_replay,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "api.yaml"


class ContractHarnessTests(unittest.TestCase):
    def setUp(self):
        self.registry = RouteRegistry.from_yaml(CONTRACTS)

    def test_smoke_probes_every_contract_and_unimplemented_is_safe(self):
        results = run_contract_smoke(self.registry)
        self.assertEqual(len(results), 84)
        self.assertTrue(all(result.passed for result in results))
        summary = report(results)
        self.assertEqual(summary["total"], 84)
        self.assertEqual(summary["passed"], 84)
        self.assertTrue(summary["ok"])
        self.assertTrue(all(result.status == 501 for result in results))
        self.assertTrue(all(result.payload["error"] == "route_not_implemented" for result in results))

    def test_factory_is_deterministic_and_fills_dynamic_paths(self):
        first = cases_from_contracts(self.registry.contracts)
        second = cases_from_contracts(self.registry.contracts)
        self.assertEqual(first, second)
        app = self.registry.contract("ApiAppById")
        self.assertEqual(sample_path(app), "/App/sample-app_id/")
        self.assertIn("client_version", sample_request(self.registry.contract("ReqDir")))

    def test_jsonl_replay_round_trip_and_unknown_route(self):
        cases = cases_from_contracts(self.registry.contracts[:2])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            write_replay(path, cases)
            loaded = read_replay(path)
            self.assertEqual(loaded, cases)
            results = run_replay(self.registry, path)
            self.assertEqual([row.status for row in results], [501, 501])

            path.write_text(
                json.dumps({"interface": "unknown", "method": "POST", "path": "/missing", "request": {}, "expectedStatus": 404}) + "\n",
                encoding="utf-8",
            )
            replay = run_replay(self.registry, path)
            self.assertTrue(replay[0].passed)
            self.assertEqual(replay[0].status, 404)

    def test_malformed_replay_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaises(ReplayFormatError):
                read_replay(path)
            path.write_text(json.dumps({"path": "missing-leading-slash"}) + "\n", encoding="utf-8")
            with self.assertRaises(ReplayFormatError):
                read_replay(path)

    def test_registered_handler_can_be_replayed_as_success(self):
        self.registry.register("ReqDir", lambda request, params: {"ok": bool(request), "params": params})
        case = cases_from_contracts((self.registry.contract("ReqDir"),), expected_status=200)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "success.jsonl"
            write_replay(path, (case,))
            result = run_replay(self.registry, path)[0]
            self.assertTrue(result.passed)
            self.assertEqual(result.status, 200)
            self.assertTrue(result.payload["ok"])


if __name__ == "__main__":
    unittest.main()
