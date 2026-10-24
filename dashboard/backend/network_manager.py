"""
Virtual Network & IPAM (IP Address Management) Manager
Handles multi-network support, XML generation, virsh network lifecycle, and IP allocation tracking
"""
import subprocess
import os
import re
import ipaddress
import xml.etree.ElementTree as ET
from .config import (
    CONFIG_DIR, DEFAULT_NETWORK_NAME, DEFAULT_BRIDGE_NAME, 
    DEFAULT_SUBNET, DEFAULT_GATEWAY
)
from .libvirt_manager import list_domains, load_meta

def get_network_xml(network_name: str) -> str:
    """Retrieve libvirt network XML definition from virsh or config files"""
    # 1. Try querying virsh net-dumpxml
    try:
        res = subprocess.run(["virsh", "net-dumpxml", network_name], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
        
    # 2. Try looking in CONFIG_DIR
    for fname in [f"{network_name}.xml", f"{network_name.replace('.', '-')}.xml"]:
        xml_path = os.path.join(CONFIG_DIR, fname)
        if os.path.isfile(xml_path):
            try:
                with open(xml_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
                
    return ""

def parse_network_xml(xml_content: str, default_name: str = "") -> dict:
    """Parse libvirt XML network definition to extract bridge, gateway, subnet, and mode"""
    info = {
        "name": default_name,
        "bridge": "virbr10",
        "gateway": DEFAULT_GATEWAY,
        "netmask": "255.255.255.0",
        "subnet": DEFAULT_SUBNET,
        "forward_mode": "route"
    }
    if not xml_content:
        return info

    try:
        root = ET.fromstring(xml_content)
        name_el = root.findtext("name")
        if name_el:
            info["name"] = name_el
            
        bridge_el = root.find("bridge")
        if bridge_el is not None and bridge_el.get("name"):
            info["bridge"] = bridge_el.get("name")
            
        forward_el = root.find("forward")
        if forward_el is not None:
            info["forward_mode"] = forward_el.get("mode", "route")
        else:
            info["forward_mode"] = "isolated"
            
        ip_el = root.find("ip")
        if ip_el is not None:
            if ip_el.get("address"):
                info["gateway"] = ip_el.get("address")
            if ip_el.get("netmask"):
                info["netmask"] = ip_el.get("netmask")
                
        # Calculate CIDR subnet
        try:
            net = ipaddress.IPv4Network(f"{info['gateway']}/{info['netmask']}", strict=False)
            info["subnet"] = str(net)
        except Exception:
            pass
    except Exception:
        pass

    return info

def list_all_networks() -> list:
    """List all virtual networks detected in libvirt and local configuration"""
    networks_dict = {}
    
    # 1. Query virsh net-list --all
    try:
        res = subprocess.run(["virsh", "net-list", "--all"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            lines = res.stdout.strip().splitlines()
            for line in lines[2:]:
                parts = line.split()
                if len(parts) >= 2:
                    net_name = parts[0]
                    state = parts[1]
                    autostart = parts[2] if len(parts) > 2 else "unknown"
                    persistent = parts[3] if len(parts) > 3 else "yes"
                    
                    networks_dict[net_name] = {
                        "name": net_name,
                        "active": state.lower() == "active",
                        "autostart": autostart.lower() == "yes",
                        "persistent": persistent.lower() == "yes"
                    }
    except Exception:
        pass
        
    # 2. Check CONFIG_DIR for any XML files (e.g. net-10-10-10.xml)
    if os.path.isdir(CONFIG_DIR):
        for fname in os.listdir(CONFIG_DIR):
            if fname.endswith(".xml"):
                try:
                    fpath = os.path.join(CONFIG_DIR, fname)
                    with open(fpath, "r", encoding="utf-8") as f:
                        xml_str = f.read()
                    parsed = parse_network_xml(xml_str)
                    net_name = parsed.get("name") or fname[:-4]
                    if net_name not in networks_dict:
                        networks_dict[net_name] = {
                            "name": net_name,
                            "active": False,
                            "autostart": False,
                            "persistent": True
                        }
                except Exception:
                    pass

    # Ensure DEFAULT_NETWORK_NAME is present if nothing detected
    if not networks_dict:
        networks_dict[DEFAULT_NETWORK_NAME] = {
            "name": DEFAULT_NETWORK_NAME,
            "active": False,
            "autostart": True,
            "persistent": True
        }

    # Enrich each network with XML details and IPAM stats
    enriched = []
    domains = list_domains()
    meta = load_meta()

    for net_name, net_meta in networks_dict.items():
        xml_content = get_network_xml(net_name)
        parsed = parse_network_xml(xml_content, default_name=net_name)
        
        # Merge properties
        net_info = {
            **net_meta,
            "bridge": parsed["bridge"],
            "gateway": parsed["gateway"],
            "netmask": parsed["netmask"],
            "subnet": parsed["subnet"],
            "forward_mode": parsed["forward_mode"],
        }

        # IPAM calculation for this network
        ipam_data = calculate_ipam_for_network(net_info, domains, meta)
        net_info.update(ipam_data)
        enriched.append(net_info)

    # Sort so default network or active networks are first
    enriched.sort(key=lambda x: (not x.get("active", False), x["name"] != DEFAULT_NETWORK_NAME, x["name"]))
    return enriched

def calculate_ipam_for_network(net_info: dict, domains: list = None, meta: dict = None) -> dict:
    """Calculate IP allocation map and next available IP for a specific network"""
    if domains is None:
        domains = list_domains()
    if meta is None:
        meta = load_meta()

    gateway = net_info.get("gateway", DEFAULT_GATEWAY)
    subnet_str = net_info.get("subnet", DEFAULT_SUBNET)
    bridge = net_info.get("bridge", "virbr10")
    
    try:
        net_obj = ipaddress.IPv4Network(subnet_str, strict=False)
    except Exception:
        net_obj = ipaddress.IPv4Network(DEFAULT_SUBNET, strict=False)

    allocated = {}
    allocated[gateway] = {
        "vm": f"Host Gateway ({bridge})", 
        "status": "system",
        "vcpus": "-",
        "memory_mb": "-"
    }

    # 1. Active domains
    for d in domains:
        ip = d.get("ip")
        if ip:
            try:
                if ipaddress.IPv4Address(ip) in net_obj:
                    allocated[ip] = {
                        "vm": d.get("name"),
                        "status": d.get("state", "running"),
                        "vcpus": d.get("vcpus"),
                        "memory_mb": d.get("memory_mb")
                    }
            except Exception:
                pass

    # 2. Configured metadata (in case VM is stopped or off)
    for name, m in meta.items():
        ip = m.get("ip")
        if ip and ip not in allocated:
            try:
                if ipaddress.IPv4Address(ip) in net_obj:
                    allocated[ip] = {
                        "vm": name,
                        "status": "configured",
                        "vcpus": m.get("vcpus"),
                        "memory_mb": m.get("memory_mb")
                    }
            except Exception:
                pass

    # Find next available IP
    # Default start at .10 or .2 if subnet is small
    hosts = list(net_obj.hosts())
    total_usable = len(hosts)
    next_ip = ""
    
    start_idx = 9 if total_usable > 10 else 1
    for h in hosts[start_idx:]:
        s = str(h)
        if s != gateway and s not in allocated:
            next_ip = s
            break
            
    if not next_ip:
        for h in hosts:
            s = str(h)
            if s != gateway and s not in allocated:
                next_ip = s
                break

    return {
        "allocated_ips": allocated,
        "total_allocated": len(allocated),
        "total_ips": total_usable,
        "next_available_ip": next_ip
    }

def get_network_info(network_name: str = None) -> dict:
    """Retrieve details and IPAM allocation map for a specific network (or default)"""
    networks = list_all_networks()
    if not networks:
        return {
            "network_name": DEFAULT_NETWORK_NAME,
            "bridge": DEFAULT_BRIDGE_NAME,
            "subnet": DEFAULT_SUBNET,
            "gateway": DEFAULT_GATEWAY,
            "active": False,
            "autostart": False,
            "allocated_ips": {},
            "total_allocated": 0,
            "total_ips": 253,
            "next_available_ip": "10.10.10.10"
        }

    target = None
    if network_name:
        for n in networks:
            if n["name"] == network_name:
                target = n
                break
                
    if not target:
        # Default to net-10.10.10 or the first network
        for n in networks:
            if n["name"] == DEFAULT_NETWORK_NAME:
                target = n
                break
        if not target:
            target = networks[0]

    return {
        "network_name": target["name"],
        "bridge": target.get("bridge", DEFAULT_BRIDGE_NAME),
        "subnet": target.get("subnet", DEFAULT_SUBNET),
        "gateway": target.get("gateway", DEFAULT_GATEWAY),
        "active": target.get("active", False),
        "autostart": target.get("autostart", False),
        "forward_mode": target.get("forward_mode", "route"),
        "allocated_ips": target.get("allocated_ips", {}),
        "total_allocated": target.get("total_allocated", 0),
        "total_ips": target.get("total_ips", 253),
        "next_available_ip": target.get("next_available_ip", "")
    }

def generate_network_xml(name: str, bridge: str, gateway: str, netmask: str = "255.255.255.0", forward_mode: str = "route") -> str:
    """Generate libvirt XML definition string for virtual network"""
    forward_line = f"<forward mode='{forward_mode}'/>" if forward_mode and forward_mode != "isolated" else ""
    return f"""<network>
  <name>{name}</name>
  {forward_line}
  <bridge name='{bridge}' stp='on' delay='0'/>
  <domain name='network'/>
  <ip address='{gateway}' netmask='{netmask}'>
  </ip>
</network>
""".strip()

def create_virtual_network(params: dict) -> dict:
    """Create, define, autostart, and activate a new virtual network with optional iptables NAT"""
    name = params.get("name", "").strip()
    bridge = params.get("bridge", "").strip()
    gateway = params.get("gateway", "").strip()
    netmask = params.get("netmask", "255.255.255.0").strip()
    forward_mode = params.get("forward_mode", "route").strip().lower()
    enable_nat = params.get("enable_nat", True)

    # 1. Validation
    if not name or not re.match(r"^[a-zA-Z0-9_\.\-]+$", name):
        return {"success": False, "error": "Invalid network name. Use letters, numbers, hyphens, and dots only."}

    if not bridge or not re.match(r"^[a-zA-Z0-9_\-]+$", bridge):
        return {"success": False, "error": "Invalid bridge name (e.g. virbr20)."}

    try:
        ipaddress.IPv4Address(gateway)
    except Exception:
        return {"success": False, "error": f"Invalid gateway IP address: '{gateway}'."}

    try:
        net_obj = ipaddress.IPv4Network(f"{gateway}/{netmask}", strict=False)
        subnet_cidr = str(net_obj)
    except Exception as e:
        return {"success": False, "error": f"Invalid IP / Netmask configuration: {str(e)}."}

    if forward_mode not in ["route", "nat", "isolated"]:
        forward_mode = "route"

    # 2. Generate XML
    xml_content = generate_network_xml(name, bridge, gateway, netmask, forward_mode)
    
    # Ensure config dir
    os.makedirs(CONFIG_DIR, exist_ok=True)
    xml_path = os.path.join(CONFIG_DIR, f"{name}.xml")
    
    try:
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml_content + "\n")
    except Exception as e:
        return {"success": False, "error": f"Failed to save network XML: {str(e)}"}

    # 3. Libvirt execution
    cmds = [
        ["virsh", "net-define", xml_path],
        ["virsh", "net-autostart", name],
        ["virsh", "net-start", name]
    ]

    executed_logs = []
    for cmd in cmds:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                executed_logs.append(f"Command {' '.join(cmd)}: {res.stderr.strip()}")
            else:
                executed_logs.append(f"Command {' '.join(cmd)}: OK")
        except Exception as e:
            executed_logs.append(f"Command {' '.join(cmd)} failed: {str(e)}")

    # 4. Optional IPTables NAT Masquerade
    if enable_nat and forward_mode != "isolated":
        try:
            # Check existing rule
            chk = subprocess.run(["iptables", "-t", "nat", "-C", "POSTROUTING", "-s", subnet_cidr, "-j", "MASQUERADE"], capture_output=True, text=True)
            if chk.returncode != 0:
                subprocess.run(["iptables", "-t", "nat", "-A", "POSTROUTING", "-s", subnet_cidr, "-j", "MASQUERADE"], capture_output=True, text=True)
                executed_logs.append(f"IPTables NAT rule added for {subnet_cidr}")
                
            # Try saving via netfilter-persistent
            subprocess.run(["netfilter-persistent", "save"], capture_output=True, text=True)
        except Exception as e:
            executed_logs.append(f"IPTables NAT setup note: {str(e)}")

    return {
        "success": True,
        "message": f"Virtual network '{name}' successfully created and started.",
        "network": {
            "name": name,
            "bridge": bridge,
            "gateway": gateway,
            "netmask": netmask,
            "subnet": subnet_cidr,
            "forward_mode": forward_mode,
            "xml_path": xml_path
        },
        "logs": executed_logs
    }

def delete_virtual_network(name: str) -> dict:
    """Destroy and undefine a virtual network if no active VMs are using it"""
    if name == DEFAULT_NETWORK_NAME:
        # Don't allow accidental deletion of default homelab network
        return {"success": False, "error": f"Cannot delete default network '{DEFAULT_NETWORK_NAME}'."}

    # Check if any VM is using this network
    domains = list_domains()
    meta = load_meta()
    
    # Get network info to inspect subnet
    info = get_network_info(name)
    subnet_str = info.get("subnet")
    if subnet_str:
        try:
            net_obj = ipaddress.IPv4Network(subnet_str, strict=False)
            for d in domains:
                ip = d.get("ip")
                if ip and ipaddress.IPv4Address(ip) in net_obj:
                    return {"success": False, "error": f"Cannot delete network '{name}': VM '{d.get('name')}' is currently using IP {ip}."}
            for vm_name, m in meta.items():
                ip = m.get("ip")
                if ip and ipaddress.IPv4Address(ip) in net_obj:
                    return {"success": False, "error": f"Cannot delete network '{name}': Configured VM '{vm_name}' has allocated IP {ip}."}
        except Exception:
            pass

    # Libvirt undefine
    logs = []
    try:
        subprocess.run(["virsh", "net-destroy", name], capture_output=True, text=True, timeout=5)
    except Exception:
        pass

    try:
        res = subprocess.run(["virsh", "net-undefine", name], capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            logs.append(f"virsh net-undefine warning: {res.stderr.strip()}")
    except Exception as e:
        logs.append(f"virsh net-undefine failed: {str(e)}")

    # Clean up xml file in CONFIG_DIR if exists
    xml_path = os.path.join(CONFIG_DIR, f"{name}.xml")
    if os.path.isfile(xml_path):
        try:
            os.remove(xml_path)
        except Exception:
            pass

    return {
        "success": True,
        "message": f"Virtual network '{name}' has been deleted.",
        "logs": logs
    }
