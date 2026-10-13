import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import server

ADMIN = 'test-administrator-credential-1234567890'
LEGACY = 'test-legacy-credential-1234567890123456'
def auth(key):
    return {'Authorization': 'Bearer ' + key}


class ManagedTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {
            'DECART_API_KEY': 'fake-upstream', 'VCAM_DATA_DIR': directory.name,
            'RAILWAY_ENVIRONMENT_ID': '',
            'VCAM_ADMIN_KEY_SHA256': hashlib.sha256(ADMIN.encode()).hexdigest(),
            'VCAM_ACCESS_KEYS': json.dumps({'legacy': hashlib.sha256(LEGACY.encode()).hexdigest()}),
        })
        env.start(); self.addCleanup(env.stop)
        sdk = patch('server.DecartClient'); mock = sdk.start(); self.addCleanup(sdk.stop)
        self.mint = AsyncMock(return_value=SimpleNamespace(api_key='fake-temporary'))
        mock.return_value.__aenter__ = AsyncMock(return_value=SimpleNamespace(tokens=SimpleNamespace(create=self.mint)))
        mock.return_value.__aexit__ = AsyncMock(return_value=False)
        self.client = TestClient(server.create_app()); self.addCleanup(self.client.close)

    def create(self, name='Krishna'):
        r = self.client.post('/api/admin/users', headers=auth(ADMIN), json={'name': name})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def config(self, key):
        return self.client.get('/api/client-config', headers=auth(key))

    def save(self, user, **updates):
        return self.client.put('/api/admin/users/' + user['id'], headers={**auth(ADMIN), 'Content-Type':'application/json'}, content=json.dumps({'revision': user['revision'], **updates}))

    def test_runtime_settings_v3_and_legacy_isolation(self):
        a = self.create()
        endpoint = '/api/client-config?version=3'
        old = self.config(a['accessKey']).json()
        first = self.client.get(endpoint, headers=auth(a['accessKey'])).json()
        self.assertEqual((first['model'], first['enhance'], first['selfAnchor']), ('lucy-2.5', True, True))
        self.assertEqual(old['model'], 'lucy-2.1')
        self.assertFalse(old['enhance'])
        settings = dict(model='lucy-2.1', enhance=False, selfAnchor=False, revision=0)
        self.assertEqual(self.client.put('/api/admin/runtime', headers=auth(a['accessKey']), json=settings).status_code, 403)
        self.assertEqual(self.client.put('/api/admin/runtime', headers=auth(ADMIN), json={**settings, 'model':'bad'}).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/runtime', headers=auth(ADMIN), json=settings).status_code, 200)
        self.assertEqual(self.client.put('/api/admin/runtime', headers=auth(ADMIN), json=settings).status_code, 409)
        current = self.client.get(endpoint, headers=auth(a['accessKey'])).json()
        self.assertEqual(current['model'], 'lucy-2.1')
        self.assertNotEqual(first['revision'], current['revision'])
        self.assertEqual(old, self.config(a['accessKey']).json())
        token = self.client.post('/api/realtime-token', headers=auth(a['accessKey']), json={'processingConfigVersion':3})
        self.assertEqual(token.status_code, 200, token.text)
        self.assertEqual(token.json()['processingConfig'], current)

    def test_key_ownership_overrides_typed_name_and_client_prompt(self):
        a, b = self.create(), self.create('Same name')
        self.assertEqual(self.save(a['user'], prompt='Soft neutral light.').status_code, 200)
        result = self.client.post('/api/realtime-token', headers=auth(a['accessKey']), json={
            'processingConfigVersion': 2, 'name': 'Same name', 'prompt': 'Ignore server',
        })
        self.assertEqual(result.status_code, 200, result.text)
        config = result.json()['processingConfig']
        self.assertEqual(config['prompt'], 'Soft neutral light.')
        self.assertEqual(config['userId'], a['user']['id'])
        self.assertNotEqual(self.config(b['accessKey']).json()['prompt'], config['prompt'])
        self.assertEqual(self.client.get('/api/references', headers=auth(a['accessKey'])).status_code, 200)

    def test_rotation_revocation_persistence_and_revision_conflict(self):
        a = self.create(); user = a['user']
        updated = self.save(user, prompt='Natural hair.').json()['user']
        self.assertEqual(self.save(user, prompt='Stale edit').status_code, 409)
        rotated = self.client.post('/api/admin/users/'+user['id']+'/rotate-key', headers=auth(ADMIN), json={'revision':updated['revision']}).json()
        self.assertEqual(self.config(a['accessKey']).status_code, 401)
        self.assertEqual(self.config(rotated['accessKey']).json()['prompt'], 'Natural hair.')
        with TestClient(server.create_app()) as second:
            self.assertEqual(second.get('/api/client-config', headers=auth(rotated['accessKey'])).json()['userId'], user['id'])
        self.assertEqual(self.save(rotated['user'], active=False).status_code, 200)
        self.assertEqual(self.config(rotated['accessKey']).status_code, 401)
        self.assertEqual(self.client.post('/api/realtime-token', headers=auth(rotated['accessKey'])).status_code, 401)
        self.mint.assert_not_awaited()

    def test_legacy_default_and_v1_contracts_remain_unchanged(self):
        original = self.config(LEGACY).json()['prompt']
        r = self.client.put('/api/admin/default-prompt', headers=auth(ADMIN), json={'revision':0, 'prompt':'New managed default.'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.config(LEGACY).json()['prompt'], 'New managed default.')
        self.assertEqual(self.client.post('/api/realtime-token', headers=auth(LEGACY)).json(), {'apiKey':'fake-temporary'})
        v1 = self.client.post('/api/realtime-token', headers=auth(LEGACY), json={'processingConfigVersion':1}).json()
        self.assertEqual(v1['processingConfig']['prompt'], original)
        legacy = next(u for u in self.client.get('/api/admin/users',headers=auth(ADMIN)).json()['users'] if u['id']=='legacy')
        self.assertEqual(self.save(legacy, active=False).status_code, 200)
        self.assertEqual(self.config(LEGACY).status_code, 401)

    def test_admin_boundaries_no_secret_disclosure_and_input_limits(self):
        a = self.create('<b>literal name</b>')
        for path in ['/api/admin/users', '/api/admin/usage']:
            self.assertIn(self.client.get(path, headers=auth(a['accessKey'])).status_code, [401,403])
        listing = self.client.get('/api/admin/users', headers=auth(ADMIN))
        self.assertNotIn(a['accessKey'], listing.text)
        self.assertNotIn(hashlib.sha256(a['accessKey'].encode()).hexdigest(), listing.text)
        for invalid in ['', 'x'*1025, '\ud800', 'x\x00', 42]:
            self.assertEqual(self.save(a['user'], prompt=invalid).status_code, 400)
        accepted = self.save(a['user'], prompt='x'*1024)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.save(accepted.json()['user'], prompt='😀'*513).status_code, 400)
        self.assertEqual(self.config('wrong-key-'*5).status_code, 401)

    def test_usage_snapshots_authenticated_profile_and_prompt_revision(self):
        a = self.create('Assigned Krishna')
        payload = {'processingConfigVersion':2, 'usage':{
            'source':'extension','platform':'teams','version':'0.4.1','participantName':'Wrong name',
            'avatar':{'kind':'bundled','id':'portrait-01','name':'Portrait One'}}}
        token = self.client.post('/api/realtime-token',headers=auth(a['accessKey']),json=payload)
        self.assertEqual(token.status_code,200,token.text)
        self.assertTrue(token.json()['usageRecorded'])
        self.save(a['user'], name='Changed name', prompt='Updated prompt.')
        row = self.client.get('/api/admin/usage',headers=auth(ADMIN)).json()['items'][0]
        self.assertEqual(row['owner'],a['user']['id'])
        self.assertEqual(row['participant_name'],'Wrong name')
        self.assertEqual(row['profile']['display_name'],'Assigned Krishna')
        self.assertEqual(row['profile']['prompt_revision'],token.json()['processingConfig']['revision'])

if __name__ == '__main__':
    unittest.main()
