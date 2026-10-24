"""
Libvirt & QEMU/KVM domain manager via virsh
Zero external C-library dependency, interacts with libvirtd cleanly
"""
import subprocess
import re
import os
import json
from .config import CONFIG_DIR

INSTANCES_META_FILE = os.path.join(CONFIG_DIR, "instances_meta.json")

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
