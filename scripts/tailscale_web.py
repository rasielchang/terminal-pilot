"""Configure a dedicated, private Tailscale Serve listener without touching other ports."""
import json
from pathlib import Path
import re
import shutil
import subprocess

from web_push import private_write
from runtime_paths import find_tool

HTTPS_PORT = 8443


def run_cli(arguments):
    executable = find_tool("tailscale")
    if not executable:
        raise ValueError("找不到 Tailscale，請先安裝、登入，或到 Mac 的環境設定選擇執行檔。")
    try:
        result = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        output = ((error.stdout or b"") + (error.stderr or b"")).decode(errors="replace")
        links = re.findall(r"https://login\.tailscale\.com/[^\s]+", output)
        if links:
            raise ValueError("請先開啟 Tailscale HTTPS：" + links[0]) from None
        raise ValueError("Tailscale 設定逾時，請確認 Mac 已連線且已啟用 HTTPS。") from None
    except OSError:
        raise ValueError("找不到 Tailscale CLI，請先在 Mac 安裝並連上 Tailscale。") from None
    if result.returncode:
        raise ValueError("無法設定 Tailscale HTTPS：" + (result.stderr.strip() or result.stdout.strip())[:600])
    return result.stdout


def configure(store, backend_port):
    status = json.loads(run_cli(["status", "--json"]))
    if status.get("BackendState") != "Running":
        raise ValueError("請先讓這台 Mac 連上 Tailscale。")
    name = status.get("Self", {}).get("DNSName", "").rstrip(".")
    if not re.fullmatch(r"[a-z0-9.-]+\.ts\.net", name):
        raise ValueError("Tailscale 尚未提供這台 Mac 的 HTTPS 名稱。")
    authority = f"{name}:{HTTPS_PORT}"
    origin = "https://" + authority
    config = json.loads(run_cli(["serve", "status", "--json"]) or "{}")
    owned_file = Path(store) / "https-target.json"
    owned = json.loads(owned_file.read_text()) if owned_file.exists() else {}
    web = config.get("Web", {}).get(authority, {})
    handler = web.get("Handlers", {}).get("/", {})
    has_port = str(HTTPS_PORT) in config.get("TCP", {}) or bool(web)
    if has_port and (handler.get("Proxy") != owned.get("target") or set(web.get("Handlers", {})) != {"/"}):
        raise ValueError("Tailscale 的 8443 埠已有其他服務；未修改它，請先改用其他埠。")
    if config.get("AllowFunnel", {}).get(authority):
        raise ValueError("此 Tailscale 埠已啟用公開 Funnel，請先關閉；Pilot 只使用私人 Serve。")
    target = f"http://127.0.0.1:{backend_port}"
    run_cli(["serve", "--bg", "--yes", f"--https={HTTPS_PORT}", target])
    confirmed = json.loads(run_cli(["serve", "status", "--json"]))
    if confirmed.get("Web", {}).get(authority, {}).get("Handlers", {}).get("/", {}).get("Proxy") != target:
        raise ValueError("Tailscale 未確認 HTTPS 轉送設定，請重新啟用。")
    private_write(owned_file, json.dumps({"target": target, "origin": origin}).encode())
    return origin


def stop(store, backend_port):
    """Remove only the route still pointing at our own live listener."""
    try:
        owned = json.loads((Path(store) / "https-target.json").read_text())
        target = f"http://127.0.0.1:{backend_port}"
        if owned.get("target") != target:
            return
        config = json.loads(run_cli(["serve", "status", "--json"]))
        authority = owned["origin"].removeprefix("https://")
        handler = config.get("Web", {}).get(authority, {}).get("Handlers", {}).get("/", {})
        if handler.get("Proxy") == target:
            run_cli(["serve", f"--https={HTTPS_PORT}", "off"])
    except (ValueError, OSError, KeyError):
        pass
