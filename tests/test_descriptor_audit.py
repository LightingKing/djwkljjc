from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from google.protobuf import descriptor_pb2

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_descriptors import audit  # noqa: E402


class DescriptorAuditTests(unittest.TestCase):
    def test_audit_reports_resolved_and_missing_contract_types(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descriptor_path = root / "descriptors.pb"
            file_descriptor = descriptor_pb2.FileDescriptorProto(name="local.proto", package="local")
            file_descriptor.message_type.add(name="Known")
            descriptor_path.write_bytes(
                descriptor_pb2.FileDescriptorSet(file=[file_descriptor]).SerializeToString()
            )
            contracts_path = root / "contracts.yaml"
            contracts_path.write_text(
                "interfaces:\n"
                "  - interface: KnownApi\n"
                "    requestFields: []\n"
                "    responseFields:\n"
                "      result:\n"
                "        - type: message\n"
                "          type_name: .local.Known\n"
                "  - interface: MissingApi\n"
                "    requestFields: []\n"
                "    responseFields:\n"
                "      result:\n"
                "        - type: message\n"
                "          type_name: .local.Missing\n",
                encoding="utf-8",
            )
            result = audit(descriptor_path, contracts_path)
            types = result["contractMessageTypes"]
            self.assertEqual(types["resolved"], 1)
            self.assertEqual(types["missing"], 1)
            self.assertEqual(types["missingNames"], ["local.Missing"])
            self.assertEqual(types["declaredButUnresolved"], [])
            self.assertEqual(result["contractEnumTypes"]["total"], 0)
            self.assertEqual(result["descriptorTotals"]["messageCountIncludingNested"], 1)
            self.assertEqual(result["descriptorInventory"][0]["file"], "local.proto")
            self.assertEqual(result["descriptorInventory"][0]["syntaxStatus"], "as_extracted")

    def test_malformed_syntax_metadata_is_repaired_without_inventing_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descriptor_path = root / "descriptors.pb"
            file_descriptor = descriptor_pb2.FileDescriptorProto(
                name="recovered.proto", package="recovered", syntax=b"corrupt"
            )
            file_descriptor.message_type.add(name="Known")
            descriptor_path.write_bytes(
                descriptor_pb2.FileDescriptorSet(file=[file_descriptor]).SerializeToString()
            )
            contracts_path = root / "contracts.yaml"
            contracts_path.write_text(
                "interfaces:\n"
                "  - interface: KnownApi\n"
                "    requestFields: []\n"
                "    responseFields:\n"
                "      result:\n"
                "        - type: message\n"
                "          type_name: .recovered.Known\n",
                encoding="utf-8",
            )
            result = audit(descriptor_path, contracts_path)
            self.assertEqual(result["contractMessageTypes"]["missing"], 0)
            self.assertEqual(result["repairedFiles"][0]["file"], "recovered.proto")
            self.assertEqual(result["repairedFiles"][0]["originalHex"], b"corrupt".hex())
            self.assertEqual(result["descriptorInventory"][0]["syntaxStatus"], "normalized_metadata")

    def test_il2cpp_dump_is_recorded_as_candidate_evidence_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descriptor_path = root / "descriptors.pb"
            file_descriptor = descriptor_pb2.FileDescriptorProto(name="local.proto", package="local")
            message = file_descriptor.message_type.add(name="ApiFoo")
            message.field.add(name="trade_data", number=3, label=1, type=9)
            descriptor_path.write_bytes(
                descriptor_pb2.FileDescriptorSet(file=[file_descriptor]).SerializeToString()
            )
            contracts_path = root / "contracts.yaml"
            contracts_path.write_text(
                "interfaces:\n"
                "  - interface: ApiFoo\n"
                "    requestFields:\n"
                "      - type: message\n"
                "        type_name: .local.ApiFoo\n"
                "    responseFields: {}\n",
                encoding="utf-8",
            )
            dump_path = root / "dump.cs"
            dump_path.write_text(
                "public sealed class ApiFoo : IMessage<ApiFoo>, IMessage\n"
                "{\n"
                "\tpublic const int TradeDataFieldNumber = 3;\n"
                "\tprivate string tradeData_;\n"
                "\tpublic string TradeData { get; set; }\n"
                "}\n",
                encoding="utf-8",
            )
            result = audit(descriptor_path, contracts_path, dump_path)
            self.assertEqual(result["schemaVersion"], 2)
            self.assertEqual(result["il2cppDump"]["messageCount"], 1)
            evidence = result["il2cppMessageEvidence"]
            self.assertEqual(evidence["matchedContractTypes"], 1)
            self.assertEqual(evidence["matches"][0]["fieldNumbers"], {"TradeData": 3})
            self.assertEqual(evidence["matches"][0]["status"], "static_candidate_only")
            field_evidence = result["descriptorFieldEvidence"]
            self.assertEqual(field_evidence["matched"], 1)
            self.assertEqual(field_evidence["matches"][0]["status"], "field_numbers_match")
            self.assertEqual(field_evidence["matches"][0]["descriptorFieldNumbers"], [3])


if __name__ == "__main__":
    unittest.main()
