#!/usr/bin/env python3
"""Entraîne GLiNER et Qwen en parallèle sur 2 pods RunPod (1 GPU chacun).

- Transfert code+data via SSH/scp (RunPod injecte PUBLIC_KEY).
- Fast-fail sur 200 lignes avant le run complet (cf. runpod_onpod_train.sh).
- Récupère les modèles (tar.gz) dans finetune_gliner/models/.
- TERMINE TOUJOURS les pods dans un finally (protège le budget).

Usage :
    RUNPOD_API_KEY=... .runpod-venv/bin/python scripts/runpod_train.py
Variables d'env optionnelles :
    GPU_MATCH   (défaut "H100 PCIe")  sous-chaîne pour choisir le type de GPU
    CLOUD_TYPE  (défaut "SECURE")     SECURE = IP publique fiable pour SSH
"""
from __future__ import annotations
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import runpod

ROOT = Path(__file__).resolve().parent.parent
FINETUNE = ROOT / "finetune_gliner"
ONPOD = ROOT / "scripts" / "runpod_onpod_train.sh"
WORKDIR = ROOT / ".runpod"
KEY = WORKDIR / "id_ed25519"
BUNDLE = WORKDIR / "finetune_bundle.tar.gz"
MODELS_OUT = FINETUNE / "models"

IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
GPU_MATCH = os.environ.get("GPU_MATCH", "H100 PCIe")
CLOUD_TYPE = os.environ.get("CLOUD_TYPE", "SECURE")
CONTAINER_DISK_GB = 40
# un pod par mode ; surchargeable via JOBS=gliner ou JOBS=qwen
JOBS = [j for j in os.environ.get("JOBS", "gliner,qwen").split(",") if j]

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ServerAliveInterval=30",
    "-o", "LogLevel=ERROR",
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_key() -> str:
    WORKDIR.mkdir(exist_ok=True)
    if not KEY.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(KEY), "-q"],
            check=True,
        )
    return (KEY.with_suffix(".pub")).read_text().strip()


def make_bundle() -> None:
    log(f"Bundle de {FINETUNE.name}/ (scripts + datasets)…")
    with tarfile.open(BUNDLE, "w:gz") as t:
        for p in sorted(FINETUNE.rglob("*")):
            if p.is_file() and "models" not in p.relative_to(FINETUNE).parts:
                t.add(p, arcname=f"finetune_gliner/{p.relative_to(FINETUNE)}")
    log(f"Bundle: {BUNDLE.stat().st_size/1e6:.1f} Mo")


def resolve_gpu_id() -> str:
    for g in runpod.get_gpus():
        if GPU_MATCH.lower() in g["id"].lower() or GPU_MATCH.lower() in g.get("displayName", "").lower():
            log(f"GPU choisi: {g['id']}")
            return g["id"]
    raise SystemExit(f"Aucun GPU ne matche '{GPU_MATCH}'")


def wait_ssh(pod_id: str, timeout: int = 600) -> tuple[str, int]:
    """Attend que le pod expose le port SSH (22/tcp public)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pod = runpod.get_pod(pod_id)
        rt = (pod or {}).get("runtime") or {}
        for port in rt.get("ports") or []:
            if port.get("privatePort") == 22 and port.get("type") == "tcp" and port.get("isIpPublic"):
                return port["ip"], int(port["publicPort"])
        time.sleep(8)
    raise TimeoutError(f"SSH indisponible sur {pod_id} après {timeout}s")


def ssh_ready(ip: str, port: int, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            ["ssh", *SSH_OPTS, "-i", str(KEY), "-p", str(port),
             f"root@{ip}", "echo ok"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and "ok" in r.stdout:
            return
        time.sleep(8)
    raise TimeoutError(f"SSH pas prêt sur {ip}:{port}")


def scp_to(ip: str, port: int, local: Path, remote: str) -> None:
    subprocess.run(
        ["scp", *SSH_OPTS, "-i", str(KEY), "-P", str(port), str(local), f"root@{ip}:{remote}"],
        check=True, timeout=300,
    )


def scp_from(ip: str, port: int, remote: str, local: Path) -> None:
    subprocess.run(
        ["scp", *SSH_OPTS, "-i", str(KEY), "-P", str(port), f"root@{ip}:{remote}", str(local)],
        check=True, timeout=600,
    )


def ssh_run(ip: str, port: int, cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", *SSH_OPTS, "-i", str(KEY), "-p", str(port), f"root@{ip}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def drive_pod(mode: str, pod_id: str, results: dict) -> None:
    try:
        ip, port = wait_ssh(pod_id)
        log(f"[{mode}] SSH {ip}:{port} — attente readiness…")
        ssh_ready(ip, port)
        log(f"[{mode}] Upload bundle + script…")
        ssh_run(ip, port, "mkdir -p /workspace && cd /workspace && rm -rf finetune_gliner")
        scp_to(ip, port, BUNDLE, "/workspace/bundle.tar.gz")
        scp_to(ip, port, ONPOD, "/workspace/run.sh")
        ssh_run(ip, port, "cd /workspace && tar xzf bundle.tar.gz && chmod +x run.sh")
        # Exécution SYNCHRONE en foreground : le flux de sortie maintient le canal
        # SSH actif (pas de hang comme avec nohup &). Le thread bloque jusqu'à la fin.
        log(f"[{mode}] Entraînement (synchrone, jusqu'à ~60min)…")
        r = subprocess.run(
            ["ssh", *SSH_OPTS, "-i", str(KEY), "-p", str(port), f"root@{ip}",
             f"bash -lc 'cd /workspace && bash run.sh {mode}'"],
            capture_output=True, text=True, timeout=3900,
        )
        (WORKDIR / f"{mode}_remote.log").write_text((r.stdout or "") + "\n--STDERR--\n" + (r.stderr or ""))
        if r.returncode == 0:
            MODELS_OUT.mkdir(parents=True, exist_ok=True)
            dest = MODELS_OUT / f"model_{mode}.tar.gz"
            scp_from(ip, port, f"/workspace/model_{mode}.tar.gz", dest)
            log(f"[{mode}] Terminé OK — modèle récupéré → {dest}")
            results[mode] = ("ok", str(dest))
        else:
            tail_out = (r.stdout or "")[-2500:] + "\n--STDERR--\n" + (r.stderr or "")[-1200:]
            log(f"[{mode}] ÉCHEC (exit={r.returncode}). Fin du log:\n{tail_out}")
            results[mode] = ("fail", tail_out)
        return
    except Exception as e:  # noqa: BLE001
        results[mode] = ("error", repr(e))
        log(f"[{mode}] Exception: {e!r}")


def main() -> int:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise SystemExit("RUNPOD_API_KEY manquant (export depuis .env)")
    runpod.api_key = api_key

    pubkey = ensure_key()
    make_bundle()
    gpu_id = resolve_gpu_id()

    pods: dict[str, str] = {}
    results: dict[str, tuple] = {}
    try:
        for mode in JOBS:
            log(f"Création pod '{mode}' ({gpu_id}, {CLOUD_TYPE})…")
            pod = runpod.create_pod(
                name=f"anon-train-{mode}",
                image_name=IMAGE,
                gpu_type_id=gpu_id,
                cloud_type=CLOUD_TYPE,
                gpu_count=1,
                container_disk_in_gb=CONTAINER_DISK_GB,
                support_public_ip=True,
                start_ssh=True,
                ports="22/tcp",
                env={"PUBLIC_KEY": pubkey},
            )
            pods[mode] = pod["id"]
            log(f"  → pod {mode}: {pod['id']}")

        threads = []
        for mode, pid in pods.items():
            th = threading.Thread(target=drive_pod, args=(mode, pid, results), daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
    finally:
        for mode, pid in pods.items():
            try:
                runpod.terminate_pod(pid)
                log(f"Pod {mode} ({pid}) TERMINÉ.")
            except Exception as e:  # noqa: BLE001
                log(f"⚠ Échec terminate {mode} ({pid}): {e!r} — VÉRIFIER MANUELLEMENT sur runpod.io")
        # Vérif post-terminate
        time.sleep(5)
        for mode, pid in pods.items():
            try:
                still = runpod.get_pod(pid)
                if still:
                    log(f"⚠ Pod {mode} ({pid}) encore présent — vérifier sur runpod.io")
            except Exception:
                pass

    log("=== RÉSUMÉ ===")
    ok = True
    for mode in JOBS:
        status = results.get(mode, ("absent", ""))
        log(f"  {mode}: {status[0]}")
        if status[0] != "ok":
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
