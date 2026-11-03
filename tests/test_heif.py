"""Real HEVC fixtures, orientation, host normalization, delivery and durability."""
import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from photo_heif import is_heif, decode_heif, decoder_path
from photo_store import PhotoStore, scope, wire
import test_photos as photo_tests

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=json.loads((ROOT/'tests/fixtures/heif.json').read_text())['fixtures']

def fixture(n=1):
    value=FIXTURES[n-1];raw=base64.b64decode(value['data'])
    assert hashlib.sha256(raw).hexdigest()==value['sha256']
    return raw

def ensure_decoder():
    source=ROOT/'scripts/photo_decode.swift';binary=ROOT/'.build/photo_decode'
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        binary.parent.mkdir(exist_ok=True)
        subprocess.run(['xcrun','swiftc','-O','-module-cache-path',str(binary.parent/'module-cache'),str(source),'-o',str(binary)],check=True)

class DetectionTests(unittest.TestCase):
    def test_genuine_container_and_content_detection(self):
        for n in range(1,9):
            raw=fixture(n);self.assertTrue(is_heif(raw));self.assertIn(b'hvcC',raw)
            self.assertIn(b'Exif',raw);self.assertFalse(raw.startswith(b'\xff\xd8'))
        for raw in (b'',b'not-heic',b'\xff\xd8'+fixture(),struct.pack('>I',9999)+fixture()[4:]):self.assertFalse(is_heif(raw))
        video=struct.pack('>I',20)+b'ftypqt  '+b'\x00'*4+b'qt  ';self.assertFalse(is_heif(video))
    def test_decoder_timeout_missing_and_bad_output_are_clear_errors(self):
        with tempfile.TemporaryDirectory() as d:
            exe=Path(d)/'helper';exe.touch()
            with patch('photo_heif.sys.platform','darwin'),patch('photo_heif.decoder_path',return_value=exe):
                with patch('photo_heif.subprocess.run',side_effect=subprocess.TimeoutExpired('helper',20)):
                    with self.assertRaisesRegex(ValueError,'逾時'):decode_heif(fixture())
                with patch('photo_heif.subprocess.run',return_value=subprocess.CompletedProcess([],2,stderr=b'dimensions\n')):
                    with self.assertRaisesRegex(ValueError,'50MP'):decode_heif(fixture())
                with patch('photo_heif.subprocess.run',return_value=subprocess.CompletedProcess([],0,stderr=b'')):
                    with self.assertRaisesRegex(ValueError,'結果不完整'):decode_heif(fixture())
            with patch('photo_heif.decoder_path',return_value=Path(d)/'missing'):
                with self.assertRaisesRegex(ValueError,'工具尚未就緒'):decode_heif(fixture())

@unittest.skipUnless(sys.platform=='darwin','Native ImageIO HEIF decoder is macOS-only')
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):ensure_decoder()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PhotoStore(self.tmp.name);self.addCleanup(self.store.close)
    def test_all_eight_orientations_and_exif_are_handled_once(self):
        # EXIF 1..8, expected quadrant order independent of ImageIO/Pillow orientation code.
        orders=['RGBY','GRYB','YBGR','BYRG','RBGY','BRYG','YGBR','GYRB']
        colors={'R':(255,0,0),'G':(0,255,0),'B':(0,0,255),'Y':(255,255,0)}
        for n,order in enumerate(orders,1):
            with self.subTest(orientation=n):
                p=self.store.upload({'requestID':str(n),'name':'IPHONE.HEIF','data':base64.b64encode(fixture(n)).decode()},'one')
                with Image.open(self.store.paths([p['id']],'one')[0]) as image:
                    self.assertEqual(image.size,(160,96) if n<=4 else (96,160));self.assertEqual(image.format,'JPEG')
                    self.assertFalse(image.getexif());self.assertNotIn('xmp',image.info)
                    raw=Path(image.filename).read_bytes();self.assertNotIn(b'synthetic metadata',raw);self.assertNotIn(b'Exif',raw)
                    positions=[(image.width//4,image.height//4),(3*image.width//4,image.height//4),(image.width//4,3*image.height//4),(3*image.width//4,3*image.height//4)]
                    for xy,color in zip(positions,order):
                        self.assertLess(max(abs(a-b) for a,b in zip(image.getpixel(xy),colors[color])),20)
    def test_corrupt_and_oversized_dimensions_leave_no_receipt(self):
        raw=fixture();ispe=raw.index(b'ispe')
        huge=raw[:ispe+8]+struct.pack('>II',12032,96)+raw[ispe+16:]
        over_pixels=raw[:ispe+8]+struct.pack('>II',8000,7000)+raw[ispe+16:]
        for data in (raw[:60],raw[:-400],huge,over_pixels):
            with self.assertRaises(ValueError):self.store.upload({'requestID':'bad','data':base64.b64encode(data).decode()},'one')
        self.assertEqual(self.store.catalog('one'),[])
    def test_name_independence_and_durable_history_forwarding(self):
        data={'requestID':'same','name':'CAMERA.JPG','data':base64.b64encode(fixture(6)).decode()}
        photo=self.store.upload(data,'brain');self.assertEqual(photo,self.store.upload(data,'brain'))
        with self.assertRaises(ValueError):self.store.upload(data,'foreign')
        reopened=PhotoStore(self.tmp.name);self.addCleanup(reopened.close)
        self.assertEqual(reopened.upload(data,'brain'),photo)
        reopened.grant([photo['id']],'brain','precise-worker')
        page=reopened.decorate({'messages':[{'role':'user','text':wire('Look',[photo['id']])}]},'precise-worker')
        self.assertEqual(page['messages'][0]['photos'],[photo]);self.assertNotIn('base64',json.dumps(page))
        with Image.open(reopened.paths([photo['id']],'precise-worker')[0]) as image:self.assertEqual(image.size,(96,160))
    def test_real_heif_api_codex_claude_and_brain_routing(self):
        # Exercise existing production API/RPC/Brain tests using real HEIF bytes,
        # including lost-response recovery, exact scopes and formal forwarding.
        encoded=base64.b64encode(fixture(6)).decode()
        methods=['test_actual_rpc_input_contains_image_and_retry_never_resends',
                 'test_claude_uses_readable_image_and_native_uncertainty_is_not_retyped',
                 'test_history_read_requires_auth_exact_thread_home_and_binding',
                 'test_lost_add_and_start_responses_reconcile_without_replay_after_restart',
                 'test_brain_acceptance_persists_ids_and_exact_thread_not_base64']
        with patch('test_photos.picture',return_value=encoded):
            for name in methods:
                with self.subTest(name=name):
                    case=photo_tests.PhotoTests(name);result=unittest.TestResult();case.run(result)
                    self.assertTrue(result.wasSuccessful(),result.errors+result.failures)
            for name in ['test_brain_formal_forward_retains_exact_job_and_replay_fence','test_brain_queue_receives_real_image_once_with_bounded_incremental_context']:
                case=photo_tests.FormalPhotoTests(name);result=unittest.TestResult();case.run(result)
                self.assertTrue(result.wasSuccessful(),result.errors+result.failures)

if __name__=='__main__':unittest.main()
