import tempfile
import unittest
from unittest.mock import Mock, patch
from wq.brain_client import BrainClient
from wq.errors import AdapterError

class BrainClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.client=BrainClient(self.temp.name)
        self.client.opener=Mock()
    def test_post_timeout_is_unknown_single_attempt(self):
        self.client.opener.open.side_effect=TimeoutError()
        with self.assertRaises(AdapterError) as caught:self.client.request('POST','/simulations',{})
        self.assertEqual(caught.exception.kind,AdapterError.UNKNOWN_REMOTE)
        self.assertEqual(self.client.opener.open.call_count,1)
    def test_read_timeout_after_acceptance_is_unknown(self):
        r=Mock(status=201,headers={'Location':'/simulations/abc'});r.read.side_effect=TimeoutError()
        self.client.opener.open.return_value=r
        with self.assertRaises(AdapterError) as caught:self.client.request('POST','/simulations',{})
        self.assertEqual(caught.exception.kind,AdapterError.UNKNOWN_REMOTE)
        r.close.assert_called_once()
    def test_incomplete_response_is_unknown_and_not_reposted(self):
        import http.client
        r=Mock(status=201,headers={});r.read.side_effect=http.client.IncompleteRead(b'partial')
        self.client.opener.open.return_value=r
        with self.assertRaises(AdapterError) as caught:self.client.request('POST','/simulations',{})
        self.assertEqual(caught.exception.kind,AdapterError.UNKNOWN_REMOTE)
        self.assertEqual(self.client.opener.open.call_count,1)

    def test_redirect_not_followed(self):
        self.client.opener.open.return_value=Mock(status=302,headers={'Location':'https://evil.example'},read=Mock(return_value=b''))
        with self.assertRaises(AdapterError) as caught:self.client.request('POST','/simulations',{})
        self.assertEqual(caught.exception.kind,AdapterError.UNKNOWN_REMOTE)
        self.assertEqual(self.client.opener.open.call_count,1)
    def test_401_distinct_from_remote_unknown(self):
        self.client.opener.open.return_value=Mock(status=401,headers={},read=Mock(return_value=b'{}'))
        with self.assertRaises(AdapterError) as caught:self.client.request('POST','/authentication')
        self.assertEqual(caught.exception.kind,AdapterError.AUTH)
    def test_unsafe_cookie_permissions_rejected(self):
        self.client.path.write_text('placeholder');self.client.path.chmod(0o644)
        with self.assertRaises(ValueError):BrainClient(self.temp.name)

    def test_keychain_login_keeps_password_out_of_command_arguments(self):
        with patch('wq.brain_client.sys.platform','darwin'), \
             patch('wq.brain_client.subprocess.run', return_value=Mock(returncode=0, stdout='secret\n')) as run, \
             patch.object(self.client, 'login', return_value={'authenticated': True}) as login:
            result=self.client.login_from_keychain('user@example.com','test-service')
        self.assertEqual(result, {'authenticated': True})
        self.assertEqual(run.call_args.args[0], [
            '/usr/bin/security','find-generic-password','-a','user@example.com',
            '-s','test-service','-w'])
        self.assertNotIn('secret', ' '.join(run.call_args.args[0]))
        login.assert_called_once_with('user@example.com','secret')

    def test_preflight_refreshes_keychain_once_after_auth_failure(self):
        self.client.jar.set_cookie(__import__('http.cookiejar').cookiejar.Cookie(
            version=0, name='session', value='expired', port=None, port_specified=False,
            domain='api.worldquantbrain.com', domain_specified=True, domain_initial_dot=False,
            path='/', path_specified=True, secure=True, expires=None, discard=True,
            comment=None, comment_url=None, rest={}, rfc2109=False))
        with patch.object(self.client, 'request', side_effect=[
                AdapterError(AdapterError.AUTH,'expired'), (200,{}, {})]) as request, \
             patch.object(self.client, 'login_from_keychain', return_value={'authenticated': True}) as login:
            result=self.client.preflight(type('Cfg', (), {'get': lambda self,*args,**kwargs: {
                'auto_login': True,
                'auto_login_email': 'user@example.com',
                'keychain_service': 'test-service',
            }.get(args[-1], kwargs.get('default'))})())
        self.assertEqual(result, (200,{},{}))
        self.assertEqual(request.call_count, 2)
        login.assert_called_once_with('user@example.com','test-service')
