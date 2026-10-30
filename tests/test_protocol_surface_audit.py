from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.audit_protocol_surface import audit


class ProtocolSurfaceAuditTests(unittest.TestCase):
    def test_separates_raw_types_descriptor_interfaces_and_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            types = root / "types.json"
            interfaces = root / "interfaces.json"
            evidence = root / "evidence"
            evidence.mkdir()
            types.write_text(json.dumps({"types": [
                {"name": "Api"}, {"name": "ApiReflection"},
                {"name": "ApiFoo"}, {"name": "ApiBar"},
                {"name": "CsFoo"}, {"name": "ScFoo"}, {"name": "ScBar"},
            ]}), encoding="utf-8")
            interfaces.write_text(json.dumps({"interfaces": [
                {"interface": "ApiFoo"}, {"interface": "ApiMissing"},
            ]}), encoding="utf-8")
            (evidence / "runtime.jsonl").write_text('CsFoo ScBar\n', encoding="utf-8")
            # A static note may repeat a message name, but cannot count as a
            # runtime observation.  The audit accepts JSONL/NDJSON captures
            # only so descriptor inventories and prose cannot inflate the
            # evidence count.
            (evidence / "static-note.md").write_text('ScFoo ScBar ScFoo\n', encoding="utf-8")
            (evidence / "descriptor_set.json").write_text(json.dumps({"messages": ["ScFoo", "ScBar"]}), encoding="utf-8")

            result = audit(types, interfaces, evidence)
            self.assertEqual(result["counts"]["apiTypesRaw"], 4)
            self.assertEqual(result["counts"]["apiHelpersExcluded"], 2)
            self.assertEqual(result["counts"]["apiCandidates"], 2)
            self.assertEqual(result["counts"]["runtimeWebsocketEvidence"], 2)
            self.assertEqual(result["sources"]["runtimeEvidenceFiles"], [str(evidence / "runtime.jsonl")])
            self.assertEqual(result["http"]["apiCandidatesWithoutHttpDescriptor"], ["ApiBar"])
            self.assertEqual(result["websocket"]["candidatesWithoutRuntimeEvidence"], ["ScFoo"])


if __name__ == "__main__":
    unittest.main()
