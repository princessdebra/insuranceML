"""
Ensures the self-hosted Ollama instance (dockerized on the devserver) is
reachable before a member starts filing a claim -- narrative extraction,
coverage-check reasoning, physics explanations and AI Advisory all depend
on it, and this session has repeatedly hit two failure modes:

  1. The local SSH port-forward (see nlp/_staging/ollama_tunnel.py) silently
     dies while the process itself keeps running -- the local port stays
     bound but nothing gets forwarded.
  2. The Ollama docker container on the devserver itself stops.

This module does NOT power on the devserver machine -- it stays powered on;
SSH access assumes that. It only recovers the tunnel and the Ollama
container, both of which are things we've actually seen fail.
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time

import paramiko
import psutil
import requests

logger = logging.getLogger(__name__)

DEVSERVER_HOST = "41.90.122.129"
DEVSERVER_PORT = 2222
DEVSERVER_USER = "xeai"
DEVSERVER_PASSWORD = "Xe.Devserver.24"

OLLAMA_LOCAL_PORT = int(os.environ.get("OLLAMA_LOCAL_PORT", "11436"))
OLLAMA_CONTAINER_NAME = "xeai_local_ollama"

TUNNEL_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "nlp", "_staging", "ollama_tunnel.py"
)

# Only one recovery attempt runs at a time, no matter how many claim
# submissions land concurrently while the fix is in progress.
_recovery_lock = threading.Lock()


def is_ollama_reachable(timeout: float = 5.0) -> bool:
    """Quick check: does the local tunnel port actually forward to a live Ollama?"""
    try:
        resp = requests.get(f"http://127.0.0.1:{OLLAMA_LOCAL_PORT}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _local_port_is_bound() -> bool:
    """Is *something* already listening on the tunnel's local port?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", OLLAMA_LOCAL_PORT)) == 0


def _ensure_ollama_container_running() -> str:
    """
    SSH into the devserver directly (independent of the tunnel, which may be
    dead) and start the Ollama docker container if it isn't already running.
    `docker start` on an already-running container is a harmless no-op, so
    we don't need to check status first.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            DEVSERVER_HOST, port=DEVSERVER_PORT,
            username=DEVSERVER_USER, password=DEVSERVER_PASSWORD, timeout=15,
        )
        _, stdout, stderr = client.exec_command(
            f"docker start {OLLAMA_CONTAINER_NAME}", timeout=30
        )
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if err and OLLAMA_CONTAINER_NAME not in out:
            logger.warning(f"docker start reported: {err}")
            return f"docker start issued (stderr: {err})"
        return f"docker container '{OLLAMA_CONTAINER_NAME}' confirmed running"
    finally:
        client.close()


def _kill_stale_tunnel_processes() -> int:
    """
    Find and kill any process still bound to the tunnel's local port.

    This matters because the tunnel script sets SO_REUSEADDR, which lets a
    freshly-spawned tunnel bind to the same port even while an old, silently
    broken listener from a previous run is still alive -- Windows will then
    route new connections unpredictably between the two processes. This is
    the actual root cause of the intermittent "connection aborted" failures
    seen repeatedly this session: restarting the tunnel without first
    killing the old one just adds a second broken listener alongside it.
    """
    killed = 0
    for conn in psutil.net_connections(kind="tcp"):
        if (
            conn.laddr and conn.laddr.port == OLLAMA_LOCAL_PORT
            and conn.status == psutil.CONN_LISTEN
            and conn.pid
        ):
            try:
                psutil.Process(conn.pid).kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.warning(f"Could not kill stale tunnel process {conn.pid}: {e}")
    if killed:
        time.sleep(1.0)  # let the OS release the port before rebinding
    return killed


def _restart_tunnel_process() -> str:
    """
    Kill any stale listener on the tunnel port, then launch a fresh tunnel
    as a detached background process, using the same interpreter running
    this backend (paramiko is installed into this venv specifically so this
    works without a second Python install).
    """
    killed = _kill_stale_tunnel_processes()

    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "ollama_tunnel_watchdog.log"
    )
    log_file = open(log_path, "a")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [sys.executable, TUNNEL_SCRIPT_PATH],
        stdout=log_file, stderr=log_file,
        creationflags=creationflags,
        close_fds=True,
    )
    return f"killed {killed} stale listener(s), tunnel process relaunched"


def ensure_ollama_ready(wait_timeout: float = 20.0) -> dict:
    """
    Main entry point: check whether Ollama is reachable through the tunnel,
    and if not, attempt to recover both the docker container and the tunnel
    process, then poll until reachable or wait_timeout elapses.

    Never raises -- returns a status dict describing what happened, so the
    caller can proceed with the claim submission regardless (existing
    fallback behavior downstream already handles Ollama being unavailable).
    """
    if is_ollama_reachable():
        return {"status": "ready", "actions_taken": [], "elapsed_s": 0.0}

    if not _recovery_lock.acquire(blocking=False):
        # Another request is already fixing this -- just wait for it.
        start = time.time()
        while time.time() - start < wait_timeout:
            if is_ollama_reachable():
                return {"status": "ready", "actions_taken": ["waited_for_concurrent_recovery"], "elapsed_s": time.time() - start}
            time.sleep(1)
        return {"status": "unreachable", "actions_taken": ["waited_for_concurrent_recovery"], "elapsed_s": time.time() - start}

    start = time.time()
    actions = []
    try:
        logger.warning("Ollama unreachable at claim-intake start — attempting recovery")

        try:
            actions.append(_ensure_ollama_container_running())
        except Exception as e:
            logger.error(f"Failed to reach devserver via SSH: {e}")
            actions.append(f"SSH to devserver failed: {e}")
            # If SSH itself fails, the tunnel can't help either -- the
            # devserver or network is genuinely unreachable, not just the
            # container. Report and stop here rather than spin uselessly.
            return {"status": "unreachable", "actions_taken": actions, "elapsed_s": time.time() - start}

        if not _local_port_is_bound() or not is_ollama_reachable(timeout=3.0):
            actions.append(_restart_tunnel_process())

        while time.time() - start < wait_timeout:
            if is_ollama_reachable():
                return {"status": "recovered", "actions_taken": actions, "elapsed_s": time.time() - start}
            time.sleep(1.5)

        return {"status": "unreachable", "actions_taken": actions, "elapsed_s": time.time() - start}
    finally:
        _recovery_lock.release()
