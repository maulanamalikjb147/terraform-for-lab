"""
System metrics and virtualization host status collector
"""
import os
import platform
import subprocess
import shutil
from .config import ISOS_DIR, VMS_DIR

_PREV_CPU_IDLE = 0
_PREV_CPU_TOTAL = 0

def get_cpu_usage() -> float:
    """Calculate CPU usage percentage from /proc/stat"""
    global _PREV_CPU_IDLE, _PREV_CPU_TOTAL
    if not os.path.exists("/proc/stat"):
        # Non-linux fallback or dev
        return 0.0
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = [float(x) for x in line.split()[1:8]]
        idle = parts[3] + parts[4]
        total = sum(parts)
        
        diff_idle = idle - _PREV_CPU_IDLE
        diff_total = total - _PREV_CPU_TOTAL
        _PREV_CPU_IDLE = idle
        _PREV_CPU_TOTAL = total
        
        if diff_total == 0:
            return 0.0
        usage = 100.0 * (1.0 - diff_idle / diff_total)
        return round(max(0.0, min(100.0, usage)), 1)
    except Exception:
        return 0.0

def get_cpu_info() -> dict:
    """Retrieve CPU model and core count"""
    cores = os.cpu_count() or 1
    model = platform.processor() or "Unknown CPU"
    if os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    return {"model": model, "cores": cores}

def get_memory_info() -> dict:
    """Read memory statistics from /proc/meminfo"""
    if not os.path.exists("/proc/meminfo"):
        return {"total_mb": 8192, "used_mb": 2048, "free_mb": 6144, "percent": 25.0}
    try:
        mem = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    mem[key] = int(val)
        total_kb = mem.get("MemTotal", 0)
        free_kb = mem.get("MemFree", 0)
        buffers_kb = mem.get("Buffers", 0)
        cached_kb = mem.get("Cached", 0)
        available_kb = mem.get("MemAvailable", free_kb + buffers_kb + cached_kb)
        used_kb = total_kb - available_kb
        
        total_mb = round(total_kb / 1024)
        used_mb = round(used_kb / 1024)
        free_mb = round(available_kb / 1024)
        percent = round((used_kb / total_kb) * 100, 1) if total_kb > 0 else 0
        return {
            "total_mb": total_mb,
            "used_mb": used_mb,
            "free_mb": free_mb,
            "percent": percent
        }
    except Exception:
        return {"total_mb": 0, "used_mb": 0, "free_mb": 0, "percent": 0}

def get_disk_info(path: str) -> dict:
    """Get disk space information for a directory"""
    if not os.path.exists(path):
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0, "exists": False}
    try:
        stat = os.statvfs(path)
        total_bytes = stat.f_blocks * stat.f_frsize
        free_bytes = stat.f_bavail * stat.f_frsize
        used_bytes = total_bytes - free_bytes
        
        total_gb = round(total_bytes / (1024**3), 1)
        used_gb = round(used_bytes / (1024**3), 1)
        free_gb = round(free_bytes / (1024**3), 1)
        percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0
        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
            "exists": True
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0, "exists": False}

def check_kvm_status() -> dict:
    """Check KVM virtualization host capabilities"""
    kvm_device = os.path.exists("/dev/kvm")
    
    nested = False
    intel_nested = "/sys/module/kvm_intel/parameters/nested"
    amd_nested = "/sys/module/kvm_amd/parameters/nested"
    if os.path.exists(intel_nested):
        try:
            with open(intel_nested, "r") as f:
                nested = f.read().strip() in ["1", "Y", "y"]
        except Exception:
            pass
    elif os.path.exists(amd_nested):
        try:
            with open(amd_nested, "r") as f:
                nested = f.read().strip() in ["1", "Y", "y"]
        except Exception:
            pass
            
    # Libvirtd active status
    libvirtd_active = False
    try:
        res = subprocess.run(["systemctl", "is-active", "libvirtd"], capture_output=True, text=True, timeout=2)
        libvirtd_active = (res.stdout.strip() == "active")
    except Exception:
        # Check virsh if systemctl fails
        try:
            res = subprocess.run(["virsh", "version"], capture_output=True, text=True, timeout=2)
            libvirtd_active = (res.returncode == 0)
        except Exception:
            libvirtd_active = False

    # Check bridge
    bridge_active = False
    if os.path.exists("/sys/class/net/virbr10"):
        bridge_active = True
    else:
        try:
            res = subprocess.run(["virsh", "net-info", "net-10.10.10"], capture_output=True, text=True, timeout=2)
            bridge_active = "Active:          yes" in res.stdout
        except Exception:
            pass

    return {
        "kvm_available": kvm_device,
        "nested_virtualization": nested,
        "libvirtd_active": libvirtd_active,
        "bridge_active": bridge_active,
        "arch": platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
        "hostname": platform.node()
    }

def get_system_summary() -> dict:
    """Return all system host statistics"""
    return {
        "cpu": {
            **get_cpu_info(),
            "usage_percent": get_cpu_usage()
        },
        "memory": get_memory_info(),
        "storage": {
            "vms": get_disk_info(VMS_DIR),
            "isos": get_disk_info(ISOS_DIR)
        },
        "virtualization": check_kvm_status()
    }
