"""
Virtual Network & IPAM (IP Address Management) Manager
Handles net-10.10.10, bridge virbr10, and IP allocation tracking
"""
import subprocess
import os
import re
from .config import DEFAULT_NETWORK_NAME, DEFAULT_SUBNET, DEFAULT_GATEWAY
from .libvirt_manager import list_domains, load_meta

def get_network_info() -> dict:
    """Retrieve libvirt network status and IPAM allocation map"""
    active = False
    autostart = False
    bridge = "virbr10"
    
    try:
        res = subprocess.run(["virsh", "net-info", DEFAULT_NETWORK_NAME], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "Active:" in line:
                    active = "yes" in line
                elif "Autostart:" in line:
                    autostart = "yes" in line
                elif "Bridge:" in line:
                    bridge = line.split(":", 1)[1].strip()
    except Exception:
        pass
        
    # Gather allocated IPs
    allocated = {}
    allocated[DEFAULT_GATEWAY] = {"vm": "Host Gateway (virbr10)", "status": "system"}
    
    # 1. From active domains
    domains = list_domains()
    for d in domains:
        ip = d.get("ip")
        if ip and ip.startswith("10.10.10."):
            allocated[ip] = {
                "vm": d.get("name"),
                "status": d.get("state", "unknown"),
                "vcpus": d.get("vcpus"),
                "memory_mb": d.get("memory_mb")
            }
            
    # 2. From metadata (in case domain is shut down or lease expired)
    meta = load_meta()
    for name, m in meta.items():
        ip = m.get("ip")
        if ip and ip.startswith("10.10.10.") and ip not in allocated:
            allocated[ip] = {
                "vm": name,
                "status": "configured",
                "vcpus": m.get("vcpus"),
                "memory_mb": m.get("memory_mb")
            }
            
    # Find next available IP starting from 10.10.10.10
    next_ip = "10.10.10.10"
    for i in range(10, 250):
        test_ip = f"10.10.10.{i}"
        if test_ip not in allocated:
            next_ip = test_ip
            break
            
    return {
        "network_name": DEFAULT_NETWORK_NAME,
        "bridge": bridge,
        "subnet": DEFAULT_SUBNET,
        "gateway": DEFAULT_GATEWAY,
        "active": active,
        "autostart": autostart,
        "allocated_ips": allocated,
        "total_allocated": len(allocated),
        "total_ips": 253,
        "next_available_ip": next_ip
    }
