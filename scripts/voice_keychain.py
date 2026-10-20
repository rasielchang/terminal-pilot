"""Read the existing OpenAI Keychain service in memory; never print the credential."""
import subprocess

SERVICE = 'openai-platform-api'


def read_key():
    try:
        result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', SERVICE, '-w'],
                                capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('無法讀取 Mac 鑰匙圈，請到 Terminal Pilot「環境設定」檢查語音金鑰。') from None
    if result.returncode:
        raise ValueError('請先在 Mac 的 Terminal Pilot「環境設定」設定 OpenAI API key。')
    key = result.stdout.decode('utf-8').strip()
    if not key or len(key) > 1024 or any(c.isspace() for c in key):
        raise ValueError('鑰匙圈的 OpenAI 金鑰格式不正確，請到「環境設定」更新。')
    return key
