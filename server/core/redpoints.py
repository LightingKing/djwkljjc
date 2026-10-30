"""Durable per-player activity acknowledgements and canonical menu snapshots."""
from contextlib import closing
import json
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory


def redpoint_pool():
    # The recovered aggregate references absent guild-dungeon messages. Extract
    # the independent request and saved map verbatim instead of inventing them.
    source = Path(__file__).resolve().parents[2] / 'data' / 'descriptor_set.pb'
    files = descriptor_pb2.FileDescriptorSet.FromString(source.read_bytes())
    descriptor = descriptor_pb2.FileDescriptorProto(name='local_redpoint.proto', syntax='proto3')
    for file in files.file:
        for message in file.message_type:
            if message.name in ('ApiRedPointData', 'DBRedPoint'):
                copied = descriptor.message_type.add()
                copied.CopyFrom(message)
                if copied.name == 'ApiRedPointData':
                    del copied.nested_type[:]
    pool = descriptor_pool.DescriptorPool()
    pool.Add(descriptor)
    return pool


class LocalRedpoints:
    def __init__(self, game):
        self.game = game
        self.pool = redpoint_pool()
        with closing(game._connect()) as connection, connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS local_redpoint_views (
                account_id INTEGER NOT NULL, kind INTEGER NOT NULL, target INTEGER NOT NULL,
                viewed INTEGER NOT NULL, PRIMARY KEY(account_id,kind,target))''')

    def read(self, profile):
        result = {}
        with closing(self.game._connect()) as connection:
            rows = connection.execute('SELECT kind,target,viewed FROM local_redpoint_views WHERE account_id=?',
                                      (int(profile['account_id']),)).fetchall()
        for kind, target, viewed in rows:
            result.setdefault(str(kind), {'redpointId': {}})['redpointId'][str(target)] = {
                'redpointType': kind, 'redpointId': target, 'redpointValue': bool(viewed)}
        return {'redpointType': result}

    def mark(self, profile, body):
        cls = message_factory.GetMessageClass(self.pool.FindMessageTypeByName('ApiRedPointData'))
        message = cls()
        try:
            raw = body.encode('utf-8') if isinstance(body, str) else bytes(body or b'')
            if raw.lstrip().startswith(b'{'):
                json_format.ParseDict(json.loads(raw), message)
            else:
                message.ParseFromString(raw)
            if message.redpoint_type <= 0 or message.redpoint_id <= 0:
                raise ValueError('redpoint type and id required')
        except Exception:
            return 400, {'error': 'invalid_redpoint'}
        with closing(self.game._connect()) as connection, connection:
            connection.execute('INSERT OR REPLACE INTO local_redpoint_views VALUES (?,?,?,?)',
                               (int(profile['account_id']), message.redpoint_type, message.redpoint_id,
                                int(message.redpoint_value)))
        return 200, {'redpoint': self.read(profile)}

    def fields(self, profile):
        medals = self.game.profile_features.medal_reply('GET', '/fci/medal/', profile)[1]
        account = {'accountId': int(profile['account_id'])}
        shops = self.game.economy._shop_http('GET', '/fci/shop/', None, account)[1]
        return {'redpoint': self.read(profile), 'medalRedpoint': {'medal': medals},
                'shopRedpoint': {'shopInfo': shops},
                'racePvpSummaryResult': self.game.realtime_pvp.race.summary(profile)}
