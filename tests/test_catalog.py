import tempfile
import unittest
from pathlib import Path
from wq import catalog, util

QUERY = {'instrumentType': 'EQUITY', 'region': 'USA', 'delay': 1, 'universe': 'TOP3000'}
SETTINGS = {'region': 'USA', 'universe': 'TOP3000', 'delay': 1, 'decay': 0, 'truncation': 0.08, 'neutralization': 'INDUSTRY'}


class FakeClient:
    def __init__(self, fields):
        self.fields = fields; self.calls = []

    def request(self, method, path):
        self.calls.append(path)
        if path.startswith('/data-fields?'):
            params = dict(x.split('=') for x in path.split('?')[1].split('&'))
            offset, limit = int(params['offset']), int(params['limit'])
            return 200, {}, {'count': len(self.fields), 'results': self.fields[offset:offset+limit]}
        fid = path.rsplit('/', 1)[1]
        for f in self.fields:
            if f['id'] == fid:
                return 200, {}, {**f, 'data': [{'region': 'USA', 'delay': 1, 'universe': 'TOP3000', 'coverage': 0.9},
                                              {'region': 'EUR', 'delay': 1, 'universe': 'TOP1200', 'coverage': 0.5}]}
        return 404, {}, {}


def fields(n):
    return [{'id': f'f{i}', 'type': 'MATRIX' if i % 7 else 'GROUP', 'description': f'desc {i}', 'coverage': i / n,
             'userCount': n - i, 'alphaCount': i, 'dataset': {'id': 'ds' + str(i % 3)}, 'category': {'id': 'cat'}} for i in range(n)]


class CatalogTests(unittest.TestCase):
    def test_pagination_is_complete_and_spaced(self):
        client = FakeClient(fields(123)); sleeps = []
        doc = catalog.fetch_catalog(client, QUERY, sleep=sleeps.append)
        self.assertEqual(doc['count'], 123); self.assertTrue(doc['complete'])
        self.assertEqual(len(client.calls), 3); self.assertEqual(len(sleeps), 2)
        self.assertEqual(len(catalog.search(doc, dataset='ds1', field_type='MATRIX', min_coverage=0.5)),
                         len([f for f in doc['fields'] if f['dataset'] == 'ds1' and f['type'] == 'MATRIX' and f['coverage'] >= 0.5]))
        self.assertEqual(catalog.search(doc, text='desc 12', limit=1)[0]['id'], 'f12')
        self.assertEqual(catalog.datasets(doc)[0]['fields'], 41)

    def test_truncated_pagination_is_rejected(self):
        client = FakeClient(fields(60))
        with self.assertRaisesRegex(ValueError, '分页不完整'):
            catalog.fetch_catalog(client, QUERY, sleep=lambda s: None, max_pages=1)

    def test_snapshot_requires_matching_context(self):
        client = FakeClient(fields(3))
        snap = catalog.field_snapshot(client, 'f1', QUERY)
        self.assertEqual(snap['schema'], catalog.SNAPSHOT_SCHEMA)
        self.assertEqual([c['universe'] for c in snap['context']], ['TOP3000'])
        with self.assertRaisesRegex(ValueError, '无覆盖记录'):
            catalog.field_snapshot(client, 'f1', {**QUERY, 'universe': 'TOP500'})
        with self.assertRaises(ValueError):
            catalog.field_snapshot(client, 'bad;id', QUERY)

    def test_add_role_binds_evidence_and_rejects_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(fields(3)); policy = Path(tmp) / 'policy.json'
            util.write_json(str(policy), {'settings': SETTINGS, 'bindings': {}, 'evidence_files': []})
            paths = []
            for fid in ('f1', 'f2'):
                path = catalog.evidence_path(tmp, fid, QUERY)
                util.write_json(path, catalog.field_snapshot(client, fid, QUERY)); paths.append(path)
            with self.assertRaisesRegex(ValueError, '缺证据快照'):
                catalog.add_role(str(policy), 'ratio', '(f1 / f2)', ['f1', 'f2'], '过去已知的比率代理', paths[:1])
            with self.assertRaisesRegex(ValueError, '表达式未使用字段'):
                catalog.add_role(str(policy), 'ratio', 'rank(f1)', ['f1', 'f2'], '过去已知的比率代理', paths)
            binding = catalog.add_role(str(policy), 'ratio', '(f1 / f2)', ['f1', 'f2'], '过去已知的比率代理', paths, 'fundamental')
            saved = util.read_json(str(policy))
            self.assertEqual(saved['bindings']['ratio'], binding); self.assertEqual(binding['cluster'], 'fundamental')
            self.assertEqual({e['path'] for e in saved['evidence_files']}, set(paths))
            self.assertTrue(all(util.sha256_json(util.read_json(e['path'])) == e['sha256'] for e in saved['evidence_files']))
            with self.assertRaisesRegex(ValueError, '角色已存在'):
                catalog.add_role(str(policy), 'ratio', '(f1 / f2)', ['f1', 'f2'], '过去已知的比率代理', paths)
            with self.assertRaisesRegex(ValueError, '角色名'):
                catalog.add_role(str(policy), '1bad', 'f1', ['f1'], '过去已知的比率代理', paths[:1])
            # 证据与策略设置不一致时拒绝
            other = catalog.evidence_path(tmp, 'f0', {**QUERY, 'universe': 'TOP500'})
            util.write_json(other, {**catalog.field_snapshot(client, 'f0', QUERY), 'query': {**QUERY, 'universe': 'TOP500'}})
            with self.assertRaisesRegex(ValueError, '不一致'):
                catalog.add_role(str(policy), 'other', 'f0', ['f0'], '过去已知的比率代理', [other])


if __name__ == '__main__':
    unittest.main()
