"""
Libvirt & QEMU/KVM domain manager via virsh
Zero external C-library dependency, interacts with libvirtd cleanly
"""
import subprocess
import re
import os
import json
import time
import shutil
import threading
from .config import CONFIG_DIR, ISOS_DIR, VMS_DIR

INSTANCES_META_FILE = os.path.join(CONFIG_DIR, "instances_meta.json")
_SNAPSHOT_JOBS = {}
_SPARSIFY_JOBS = {}

def get_snapshot_job(job_id: str) -> dict:
    """Return snapshot export job details and logs"""
    return _SNAPSHOT_JOBS.get(job_id)

def get_sparsify_job(job_id: str) -> dict:
    """Return disk sparsify / shrink job details and logs"""
    return _SPARSIFY_JOBS.get(job_id)

def load_meta() -> dict:
    """Load metadata saved during provisioning"""
    if os.path.exists(INSTANCES_META_FILE):
        try:
            with open(INSTANCES_META_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_meta(data: dict):
    """Save metadata"""
    try:
        with open(INSTANCES_META_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def run_virsh(args: list) -> tuple:
    """Execute a virsh command safely"""
    cmd = ["virsh"] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def list_domains() -> list:
    """List all virtual machines with status, specs, IP and VNC port"""
    ok, stdout, _ = run_virsh(["list", "--all"])
    meta = load_meta()
    
    domains = []
    
    if ok and stdout:
        lines = stdout.splitlines()
        # Format usually:
        #  Id   Name        State
        # -------------------------
        #  1    ops         running
        #  -    test-vm     shut off
        for line in lines[2:]:
            parts = line.strip().split(None, 2)
            if len(parts) >= 3:
                dom_id, name, state = parts[0], parts[1], parts[2]
                dom_info = get_domain_details(name, dom_id, state)
                # Merge metadata if present
                if name in meta:
                    dom_info["created_at"] = meta[name].get("created_at")
                    dom_info["os"] = meta[name].get("os", dom_info.get("os"))
                    if not dom_info.get("ip"):
                        dom_info["ip"] = meta[name].get("ip")
                    dom_info["plan"] = meta[name].get("plan")
                domains.append(dom_info)
    else:
        # If virsh fails (e.g. offline dev test or libvirt starting), return recorded metadata
        for name, m in meta.items():
            domains.append({
                "id": "-",
                "name": name,
                "state": m.get("state", "shut off"),
                "vcpus": m.get("vcpus", 2),
                "memory_mb": m.get("memory_mb", 2048),
                "ip": m.get("ip", "10.10.10.10"),
                "vnc_port": m.get("vnc_port", 5900),
                "os": m.get("os", "ubuntu-jammy.img"),
                "created_at": m.get("created_at", 0)
            })
            
    return domains

def get_domain_details(name: str, dom_id: str = "-", state: str = "unknown") -> dict:
    """Get specs, IP address and VNC port for a single domain"""
    # 1. Inspect dominfo
    vcpus = 1
    memory_mb = 1024
    ok, info_out, _ = run_virsh(["dominfo", name])
    if ok:
        for line in info_out.splitlines():
            if "State:" in line:
                state = line.split(":", 1)[1].strip()
            elif "CPU(s):" in line:
                try:
                    vcpus = int(line.split(":", 1)[1].strip())
                except Exception:
                    pass
            elif "Max memory:" in line:
                try:
                    kb = int(line.split(":", 1)[1].strip().split()[0])
                    memory_mb = round(kb / 1024)
                except Exception:
                    pass

    # 2. Get IP address
    ip_address = None
    ok_ip, ip_out, _ = run_virsh(["domifaddr", name, "--source", "agent"])
    if not ok_ip or not ip_out:
        ok_ip, ip_out, _ = run_virsh(["domifaddr", name, "--source", "lease"])
        
    if ok_ip and ip_out:
        matches = re.findall(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+", ip_out)
        if matches:
            # Filter out 127.0.0.1
            non_local = [ip for ip in matches if not ip.startswith("127.")]
            if non_local:
                ip_address = non_local[0]

    # 3. Get VNC display
    vnc_port = None
    ok_vnc, vnc_out, _ = run_virsh(["vncdisplay", name])
    if ok_vnc and vnc_out:
        # Format usually ":0" or "127.0.0.1:0"
        m = re.search(r":(\d+)", vnc_out)
        if m:
            display_num = int(m.group(1))
            vnc_port = 5900 + display_num

    return {
        "id": dom_id,
        "name": name,
        "state": state,
        "vcpus": vcpus,
        "memory_mb": memory_mb,
        "ip": ip_address,
        "vnc_port": vnc_port
    }

def start_domain(name: str) -> dict:
    """Power on virtual machine"""
    ok, out, err = run_virsh(["start", name])
    if ok:
        return {"success": True, "message": f"Instance {name} started successfully"}
    return {"success": False, "error": err or out}

def stop_domain(name: str) -> dict:
    """Graceful ACPI power off"""
    ok, out, err = run_virsh(["shutdown", name])
    if ok:
        return {"success": True, "message": f"Instance {name} shutdown initiated"}
    return {"success": False, "error": err or out}

def force_stop_domain(name: str) -> dict:
    """Immediate hard power down (kill QEMU process)"""
    ok, out, err = run_virsh(["destroy", name])
    if ok:
        return {"success": True, "message": f"Instance {name} forcefully stopped"}
    return {"success": False, "error": err or out}

def reboot_domain(name: str) -> dict:
    """Reboot virtual machine"""
    ok, out, err = run_virsh(["reboot", name])
    if ok:
        return {"success": True, "message": f"Instance {name} rebooting"}
    return {"success": False, "error": err or out}

def undefine_domain(name: str) -> dict:
    """Undefine domain and remove associated storage volumes"""
    # First stop if running
    run_virsh(["destroy", name])
    ok, out, err = run_virsh(["undefine", name, "--remove-all-storage"])
    if ok:
        meta = load_meta()
        if name in meta:
            del meta[name]
            save_meta(meta)
        return {"success": True, "message": f"Instance {name} and its disks deleted"}
    return {"success": False, "error": err or out}

def create_vm_snapshot_async(name: str, params: dict) -> dict:
    """Create a reusable base OS template image from an existing VM disk"""
    raw_snap_name = params.get("snapshot_name", "").strip() or f"{name}-snapshot.img"
    if not (raw_snap_name.endswith(".img") or raw_snap_name.endswith(".qcow2")):
        raw_snap_name += ".img"
        
    target_path = os.path.join(ISOS_DIR, raw_snap_name)
    auto_restart = params.get("auto_restart", True)

    job_id = f"snap_{int(time.time())}_{name}"
    job = {
        "id": job_id,
        "vm_name": name,
        "action": "snapshot",
        "status": "queued",
        "logs": [f"[{time.strftime('%H:%M:%S')}] Snapshot job queued for instance '{name}' -> '{raw_snap_name}'..."],
        "started_at": time.time(),
        "finished_at": None,
        "error": None
    }
    _SNAPSHOT_JOBS[job_id] = job

    def _worker():
        def log(msg):
            timestamp = time.strftime('%H:%M:%S')
            job["logs"].append(f"[{timestamp}] {msg}")

        try:
            # 1. Locate primary disk path
            log(f"Finding virtual disk for instance '{name}'...")
            disk_path = None
            ok, out, _ = run_virsh(["domblklist", name])
            if ok and out:
                for line in out.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and ("/data/" in parts[1] or ".qcow2" in parts[1]):
                        disk_path = parts[1]
                        break

            if not disk_path:
                default_path = os.path.join(VMS_DIR, f"{name}-vda.qcow2")
                if os.path.exists(default_path):
                    disk_path = default_path

            if not disk_path or not os.path.exists(disk_path):
                raise RuntimeError(f"Could not locate virtual disk for instance '{name}'.")

            log(f"Identified source disk: {disk_path}")

            # 2. Check VM power state
            job["status"] = "checking"
            ok, info_out, _ = run_virsh(["dominfo", name])
            was_running = False
            if ok and "running" in info_out.lower():
                was_running = True
                log("VM is currently running. Performing graceful shutdown to ensure filesystem consistency...")
                run_virsh(["shutdown", name])
                # Wait up to 15s for shutdown
                for _ in range(15):
                    time.sleep(1)
                    ok2, state_out, _ = run_virsh(["domstate", name])
                    if ok2 and "shut off" in state_out.lower():
                        break

            # 3. Convert & compress to /data/isos/<snapshot_name>
            job["status"] = "converting"
            if shutil.which("virt-sparsify"):
                log(f"Using virt-sparsify to discard unallocated blocks and compress to '{target_path}'...")
                cmd = ["virt-sparsify", "--compress", "--tmp", VMS_DIR, disk_path, target_path]
            else:
                log(f"Converting and compressing QCOW2 image to '{target_path}'...")
                cmd = ["qemu-img", "convert", "-O", "qcow2", "-c", "-p", disk_path, target_path]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in proc.stdout:
                line_str = line.strip()
                if line_str:
                    log(line_str)
            proc.wait()

            if proc.returncode != 0:
                raise RuntimeError(f"Snapshot export command failed with code {proc.returncode}")

            log("Disk converted successfully.")

            # 4. Restart VM if requested
            if was_running and auto_restart:
                log(f"Restarting instance '{name}'...")
                run_virsh(["start", name])

            # 5. Refresh isos pool
            log("Refreshing Libvirt isos storage pool...")
            run_virsh(["pool-refresh", "isos"])

            job["status"] = "completed"
            job["finished_at"] = time.time()
            log(f"✓ Snapshot template '{raw_snap_name}' created in /data/isos! You can now deploy new VMs with it.")
        except Exception as e:
            job["status"] = "failed"
            job["finished_at"] = time.time()
            job["error"] = str(e)
            log(f"ERROR: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "job_id": job_id}

def sparsify_vm_disk_async(name: str, params: dict) -> dict:
    """Shrink and sparsify an existing VM disk using virt-sparsify to reclaim host disk space"""
    auto_restart = params.get("auto_restart", True)
    compress = params.get("compress", False)

    job_id = f"sparsify_{int(time.time())}_{name}"
    job = {
        "id": job_id,
        "vm_name": name,
        "action": "sparsify",
        "status": "queued",
        "logs": [f"[{time.strftime('%H:%M:%S')}] Sparsify job queued for instance '{name}'..."],
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
        "initial_size_mb": 0,
        "final_size_mb": 0,
        "reclaimed_mb": 0
    }
    _SPARSIFY_JOBS[job_id] = job

    def _worker():
        def log(msg):
            timestamp = time.strftime('%H:%M:%S')
            job["logs"].append(f"[{timestamp}] {msg}")

        try:
            # 1. Locate primary disk path
            log(f"Finding virtual disk for instance '{name}'...")
            disk_path = None
            ok, out, _ = run_virsh(["domblklist", name])
            if ok and out:
                for line in out.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and ("/data/" in parts[1] or ".qcow2" in parts[1]):
                        disk_path = parts[1]
                        break

            if not disk_path:
                default_path = os.path.join(VMS_DIR, f"{name}-vda.qcow2")
                if os.path.exists(default_path):
                    disk_path = default_path

            if not disk_path or not os.path.exists(disk_path):
                raise RuntimeError(f"Could not locate virtual disk for instance '{name}'.")

            init_size_bytes = os.path.getsize(disk_path)
            init_size_mb = round(init_size_bytes / (1024 * 1024), 2)
            job["initial_size_mb"] = init_size_mb
            log(f"Identified source disk: {disk_path} (Current host file size: {init_size_mb} MB)")

            # 2. Check VM power state & shutdown if running
            job["status"] = "checking"
            ok, info_out, _ = run_virsh(["dominfo", name])
            was_running = False
            if ok and "running" in info_out.lower():
                was_running = True
                log("VM is currently running. Performing graceful ACPI shutdown for safe disk compaction...")
                run_virsh(["shutdown", name])
                # Wait up to 20s for shutdown
                for _ in range(20):
                    time.sleep(1)
                    ok2, state_out, _ = run_virsh(["domstate", name])
                    if ok2 and "shut off" in state_out.lower():
                        break

            # Verify it is shut off
            ok_state, state_out, _ = run_virsh(["domstate", name])
            if ok_state and "running" in state_out.lower():
                raise RuntimeError(f"Instance '{name}' could not be stopped. Please stop the VM before running sparsify.")

            # 3. Execute virt-sparsify (or qemu-img fallback)
            job["status"] = "sparsifying"
            sparsified_tmp = disk_path + ".sparsified.tmp"
            if os.path.exists(sparsified_tmp):
                try:
                    os.remove(sparsified_tmp)
                except Exception:
                    pass

            has_virt_sparsify = shutil.which("virt-sparsify") is not None
            if has_virt_sparsify:
                log(f"Executing virt-sparsify to discard zero/unallocated filesystem blocks...")
                cmd = ["virt-sparsify", "--tmp", VMS_DIR]
                if compress:
                    cmd.append("--compress")
                cmd.extend([disk_path, sparsified_tmp])
            else:
                log("virt-sparsify not detected, falling back to qemu-img convert repack...")
                cmd = ["qemu-img", "convert", "-O", "qcow2"]
                if compress:
                    cmd.append("-c")
                cmd.extend(["-p", disk_path, sparsified_tmp])

            log(f"Running command: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in proc.stdout:
                line_str = line.strip()
                if line_str:
                    log(line_str)
            proc.wait()

            if proc.returncode != 0:
                if os.path.exists(sparsified_tmp):
                    try:
                        os.remove(sparsified_tmp)
                    except Exception:
                        pass
                raise RuntimeError(f"Sparsify command failed with exit code {proc.returncode}")

            # 4. Atomic replace
            log("Replacing original disk with reclaimed sparsified disk...")
            os.replace(sparsified_tmp, disk_path)

            final_size_bytes = os.path.getsize(disk_path)
            final_size_mb = round(final_size_bytes / (1024 * 1024), 2)
            reclaimed_mb = round(init_size_mb - final_size_mb, 2)
            job["final_size_mb"] = final_size_mb
            job["reclaimed_mb"] = max(0, reclaimed_mb)

            log(f"Disk sparsified successfully! Previous size: {init_size_mb} MB -> New size: {final_size_mb} MB (Reclaimed: {job['reclaimed_mb']} MB)")

            # 5. Restart VM if requested
            if was_running and auto_restart:
                log(f"Starting instance '{name}' back up...")
                run_virsh(["start", name])

            # 6. Refresh storage pool
            run_virsh(["pool-refresh", "vms"])

            job["status"] = "completed"
            job["finished_at"] = time.time()
            log(f"✓ Sparsify process completed for '{name}'. Storage reclaimed: {job['reclaimed_mb']} MB!")
        except Exception as e:
            job["status"] = "failed"
            job["finished_at"] = time.time()
            job["error"] = str(e)
            log(f"ERROR: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "job_id": job_id}
