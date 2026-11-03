"""Load independently valid original messages after a damaged metadata record.

The original 25-message dungeon.proto remains untouched. This supplement
contains byte-identical DescriptorProto bodies, with offsets and hashes in
data/protocols/dungeon-recovered-origins.json; existing definitions always win.
"""
from pathlib import Path
from google.protobuf import descriptor_pb2


def add_recovered_dungeon_messages(pool):
    source = Path(__file__).parent / 'data/protocols/dungeon-recovered-original.pb'
    document = descriptor_pb2.FileDescriptorProto.FromString(source.read_bytes())
    try:
        pool.FindFileByName(document.name)
        return
    except KeyError:
        pass
    original = list(document.message_type)
    del document.message_type[:]
    for message in original:
        try:
            pool.FindMessageTypeByName(message.name)
        except KeyError:
            document.message_type.add().CopyFrom(message)
    if document.message_type:
        pool.Add(document)
