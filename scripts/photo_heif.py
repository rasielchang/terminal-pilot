"""Bounded local HEIF decoding; untrusted names/MIME never select a file path."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def is_heif(raw):
    if len(raw)<16 or raw[4:8]!=b'ftyp':return False
    length=struct.unpack('>I',raw[:4])[0]
    if length<16 or length>min(len(raw),4096) or length%4:return False
    brands={raw[8:12],*[raw[n:n+4] for n in range(16,length,4)]}
    return bool(brands & {b'heic',b'heix',b'hevc',b'hevx',b'heim',b'heis',b'hevm',b'hevs',b'mif1',b'msf1'})


def decoder_path():
    here=Path(__file__).resolve().parent
    packaged=here/'photo_decode'
    return packaged if packaged.is_file() else here.parent/'.build/photo_decode'


def decode_heif(raw):
    decoder=decoder_path()
    if sys.platform!='darwin' or not decoder.is_file():
        raise ValueError('主機的HEIC解碼工具尚未就緒，請更新Mac上的Terminal Pilot。')
    with tempfile.TemporaryDirectory(prefix='pilot-photo-') as temporary:
        source=Path(temporary)/'input.heic';output=Path(temporary)/'decoded.png'
        source.write_bytes(raw)
        try:
            result=subprocess.run([str(decoder),str(source),str(output)],stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=20,check=False)
        except subprocess.TimeoutExpired:raise ValueError('這張HEIC照片解碼逾時，尚未上傳；可以重試。') from None
        except OSError:raise ValueError('主機照片解碼工具無法執行，請更新Terminal Pilot。') from None
        if result.returncode:
            if result.stderr.strip()==b'dimensions':raise ValueError('照片解析度超過50MP或邊長12000像素。')
            raise ValueError('無法解碼這張HEIC/HEIF照片，檔案可能損壞或不是支援的靜態照片。')
        if not output.is_file() or not 0<output.stat().st_size<=40*1024*1024:
            raise ValueError('照片解碼結果不完整或超過資源限制。')
        return output.read_bytes()
