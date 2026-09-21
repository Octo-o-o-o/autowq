import tempfile
import unittest
from unittest.mock import Mock
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
