"""Recovered message definitions are exact original bytes, not guessed fields."""
import base64
import hashlib
import json
from pathlib import Path
import unittest

from google.protobuf import descriptor_pb2, message_factory
from external_assets import external_path, require_file
from game import _message_pool


class RecoveredDungeonProtocolTests(unittest.TestCase):
    def test_recovered_message_bytes_match_original_metadata_offsets(self):
        root=Path(__file__).resolve().parents[1]
        evidence=json.loads((root/'data/protocols/dungeon-recovered-origins.json').read_text(encoding='utf-8'))
        path=external_path('LJJC_PROTOCOL_FIELDS','analysis/ljjc-static/protocol-fields.txt')
        require_file(path,'LJJC_PROTOCOL_FIELDS')
        source=path.read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),evidence['sourceSHA256'])
        raw=base64.b64decode(source[evidence['base64Start']:evidence['base64End']],validate=True)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),evidence['originalSHA256'])
        data=descriptor_pb2.FileDescriptorProto.FromString((root/'data/protocols/dungeon-recovered-original.pb').read_bytes())
        messages={m.name:m for m in data.message_type}
        self.assertEqual(len(messages),79)
        for item in evidence['recoveredMessages']:
            with self.subTest(message=item['name']):
                original=raw[item['payloadStart']:item['end']]
                self.assertEqual(hashlib.sha256(original).hexdigest(),item['sha256'])
                self.assertEqual(messages[item['name']].SerializeToString(),original)

    def test_new_repeated_primitive_fields_use_original_proto3_packed_wire(self):
        root=Path(__file__).resolve().parents[1]
        pool=_message_pool(root/'data/descriptor_set.pb')
        cls=message_factory.GetMessageClass(pool.FindMessageTypeByName('ApiGuildDungeonProgressReward.get'))
        self.assertTrue(cls.DESCRIPTOR.fields_by_name['reward_got'].is_packed)
        self.assertEqual(cls(reward_got=[20,40]).SerializeToString(),b'\x0a\x02\x14\x28')
        self.assertEqual(list(cls.FromString(b'\x0a\x02\x14\x28').reward_got),[20,40])
