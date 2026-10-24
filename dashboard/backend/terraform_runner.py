"""
Terraform VM Provisioner & Lifecycle Execution Engine
Generates HCL, cloud-init, network config and runs terraform apply/destroy asynchronously
"""
import os
import subprocess
import threading
import time
import json
import re
from .config import (
    INSTANCES_DIR, ISOS_DIR, VMS_DIR, DEFAULT_NETWORK_NAME,
    DEFAULT_GATEWAY, ensure_directories
)
from .libvirt_manager import load_meta, save_meta, run_virsh

_JOBS = {}  # job_id -> {id, vm_name, action, status, logs: [], started_at, finished_at, error}

def get_default_machine_type() -> str:
    """Detect appropriate QEMU machine type on Ubuntu host"""
    try:
        res = subprocess.run(["qemu-system-x86_64", "-machine", "help"], capture_output=True, text=True, timeout=2)
        out = res.stdout.lower()
        if "pc-i440fx-jammy" in out:
            return "pc-i440fx-jammy"
        elif "pc-i440fx-focal" in out:
            return "pc-i440fx-focal"
        elif "pc-i440fx" in out:
            return "pc"
    except Exception:
        pass
    return "pc"

def list_jobs() -> list:
    """Return all deployment/destruction jobs"""
    return sorted(list(_JOBS.values()), key=lambda j: j["started_at"], reverse=True)

def get_job(job_id: str) -> dict:
    """Get single job status and logs"""
    return _JOBS.get(job_id)

def render_terraform_hcl(params: dict, machine_type: str) -> str:
    """Generate main.tf HCL for the VM"""
    name = params["name"]
    memory_mb = params.get("memory_mb", 2048)
    vcpus = params.get("vcpus", 2)
    os_image = params.get("os", "ubuntu-jammy.img")
    disk_gb = params.get("disk_gb", 25)
    disk_bytes = disk_gb * 1073741824
    ip_addr = params.get("ip", "10.10.10.10")
    nested = params.get("nested", True)
    console_type = params.get("console", "vnc")

    nested_block = """
    cpu = {
        mode = "host-passthrough"
    }
""" if nested else ""

    hcl = f"""provider "libvirt" {{
    uri = "qemu:///system"
}}

terraform {{
  required_version = ">= 0.13"
  required_providers {{
    libvirt = {{
      source  = "dmacvicar/libvirt"
      version = "0.6.2"
    }}
  }}
}}

resource "libvirt_cloudinit_disk" "{name}-cloudinit" {{
    name = "{name}-cloudinit.iso"
    pool = "vms"
    user_data = data.template_file.user_data.rendered
    network_config = data.template_file.network_config.rendered
}}

data "template_file" "user_data" {{
    template = file("${{path.module}}/cloudinit1.cfg")
}}

data "template_file" "network_config" {{
    template = file("${{path.module}}/network1_config.cfg")
}}

resource "libvirt_volume" "{name}-vda" {{
    name = "{name}-vda.qcow2"
    pool = "vms"
    base_volume_name = "{os_image}"
    base_volume_pool = "isos"
    size = {disk_bytes}
    format = "qcow2"
}}

resource "libvirt_domain" "{name}" {{
    name = "{name}"
    memory = "{memory_mb}"
    vcpu = "{vcpus}"
    machine = "{machine_type}"
{nested_block}
    cloudinit = libvirt_cloudinit_disk.{name}-cloudinit.id

    console {{
        type        = "pty"
        target_port = "0"
        target_type = "serial"
    }}

    console {{
        type        = "pty"
        target_port = "1"
        target_type = "virtio"
    }}

    network_interface {{
        network_name = "{DEFAULT_NETWORK_NAME}"
        addresses = ["{ip_addr}"]
    }}

    disk {{
        volume_id = libvirt_volume.{name}-vda.id
    }}

    video {{
        type = "vga"
    }}

    graphics {{
        type = "{console_type}"
        listen_type = "address"
        autoport = true
    }}
}}
"""
    return hcl

def render_cloudinit(params: dict) -> str:
    """Generate cloud-init user data"""
    name = params["name"]
    ssh_key = params.get("ssh_key", "").strip()
    password = params.get("password", "student123")
    install_docker = params.get("install_docker", False)
    
    ssh_keys_yaml = f"      - {ssh_key}\n" if ssh_key else ""

    docker_block = """
packages:
  - ca-certificates
  - curl

runcmd:
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  - chmod a+r /etc/apt/keyrings/docker.asc
  - echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \\"$VERSION_CODENAME\\") stable" | tee /etc/apt/sources.list.d/docker.list
  - apt-get update -y
  - apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  - usermod -aG docker student || true
  - usermod -aG docker root || true
  - systemctl enable --now docker
""" if install_docker else ""

    return f"""#cloud-config
hostname: {name}
local-hostname: {name}
fqdn: {name}.localdomain
users:
  - name: root
    ssh_authorized_keys:
{ssh_keys_yaml}  - name: student
    ssh_authorized_keys:
{ssh_keys_yaml}    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    shell: /bin/bash
    groups: users, admin, sudo
ssh_pwauth: true
disable_root: false
chpasswd:
  list: |
    root:{password}
    student:{password}
  expire: false
{docker_block}
"""

def render_network_config(params: dict) -> str:
    """Generate netplan cloud-init network v2 configuration"""
    ip_addr = params.get("ip", "10.10.10.10")
    gateway = params.get("gateway", DEFAULT_GATEWAY)
    
    return f"""version: 2
ethernets:
  ens3:
    dhcp4: false
    addresses:
      - {ip_addr}/24
    gateway4: {gateway}
    nameservers:
      addresses: [8.8.8.8, 1.1.1.1]
"""

def deploy_instance_async(params: dict) -> dict:
    """Validate parameters and dispatch asynchronous provisioning job"""
    name = params.get("name", "").strip().lower()
    if not name or not re.match(r"^[a-z0-9][a-z0-9\-]{1,30}[a-z0-9]$", name):
        return {
            "success": False,
            "error": "Instance name must be 3-32 characters, lowercase alphanumeric and hyphens only."
        }
        
    os_name = params.get("os", "ubuntu-jammy.img")
    iso_file = os.path.join(ISOS_DIR, os_name)
    if not os.path.exists(iso_file):
        return {
            "success": False,
            "error": f"Base OS image '{os_name}' not found in /data/isos. Please download it from Storage & ISOs tab."
        }
        
    job_id = f"deploy_{int(time.time())}_{name}"
    job = {
        "id": job_id,
        "vm_name": name,
        "action": "deploy",
        "status": "queued",
        "logs": [f"[{time.strftime('%H:%M:%S')}] Job queued for instance '{name}'..."],
        "started_at": time.time(),
        "finished_at": None,
        "error": None
    }
    _JOBS[job_id] = job
    
    def _worker():
        instance_dir = os.path.join(INSTANCES_DIR, name)
        os.makedirs(instance_dir, exist_ok=True)
        
        def log(msg):
            timestamp = time.strftime('%H:%M:%S')
            job["logs"].append(f"[{timestamp}] {msg}")
            
        try:
            job["status"] = "generating"
            log("Generating Terraform HCL & cloud-init configuration...")
            machine_type = get_default_machine_type()
            
            hcl_content = render_terraform_hcl(params, machine_type)
            cloudinit_content = render_cloudinit(params)
            network_content = render_network_config(params)
            
            with open(os.path.join(instance_dir, "main.tf"), "w", encoding="utf-8") as f:
                f.write(hcl_content)
            with open(os.path.join(instance_dir, "cloudinit1.cfg"), "w", encoding="utf-8") as f:
                f.write(cloudinit_content)
            with open(os.path.join(instance_dir, "network1_config.cfg"), "w", encoding="utf-8") as f:
                f.write(network_content)
                
            log("Configuration written successfully.")
            
            # Step 1: terraform init
            job["status"] = "initializing"
            log("Running 'terraform init'...")
            init_proc = subprocess.Popen(
                ["terraform", "init"],
                cwd=instance_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in init_proc.stdout:
                log(line.strip())
            init_proc.wait()
            
            if init_proc.returncode != 0:
                raise RuntimeError(f"terraform init failed with exit code {init_proc.returncode}")
                
            # Step 2: terraform apply
            job["status"] = "applying"
            log("Running 'terraform apply -auto-approve'...")
            apply_proc = subprocess.Popen(
                ["terraform", "apply", "-auto-approve"],
                cwd=instance_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in apply_proc.stdout:
                log(line.strip())
            apply_proc.wait()
            
            if apply_proc.returncode != 0:
                raise RuntimeError(f"terraform apply failed with exit code {apply_proc.returncode}")
                
            # Save metadata
            meta = load_meta()
            meta[name] = {
                "name": name,
                "os": os_name,
                "vcpus": params.get("vcpus", 2),
                "memory_mb": params.get("memory_mb", 2048),
                "disk_gb": params.get("disk_gb", 25),
                "ip": params.get("ip", "10.10.10.10"),
                "plan": params.get("plan", "Standard"),
                "state": "running",
                "created_at": int(time.time())
            }
            save_meta(meta)
            
            job["status"] = "completed"
            job["finished_at"] = time.time()
            log(f"Successfully deployed VM '{name}'! Virtual machine is now running.")
        except Exception as e:
            job["status"] = "failed"
            job["finished_at"] = time.time()
            job["error"] = str(e)
            log(f"ERROR: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "job_id": job_id}

def destroy_instance_async(name: str) -> dict:
    """Destroy VM using terraform destroy and libvirt cleanup"""
    job_id = f"destroy_{int(time.time())}_{name}"
    job = {
        "id": job_id,
        "vm_name": name,
        "action": "destroy",
        "status": "queued",
        "logs": [f"[{time.strftime('%H:%M:%S')}] Termination job queued for '{name}'..."],
        "started_at": time.time(),
        "finished_at": None,
        "error": None
    }
    _JOBS[job_id] = job
    
    def _worker():
        instance_dir = os.path.join(INSTANCES_DIR, name)
        def log(msg):
            timestamp = time.strftime('%H:%M:%S')
            job["logs"].append(f"[{timestamp}] {msg}")
            
        try:
            job["status"] = "destroying"
            if os.path.exists(instance_dir) and os.path.exists(os.path.join(instance_dir, "main.tf")):
                log("Executing 'terraform destroy -auto-approve'...")
                proc = subprocess.Popen(
                    ["terraform", "destroy", "-auto-approve"],
                    cwd=instance_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                for line in proc.stdout:
                    log(line.strip())
                proc.wait()
                
            # Fallback virsh cleanup
            log("Ensuring domain and disks are removed from libvirt...")
            run_virsh(["destroy", name])
            run_virsh(["undefine", name, "--remove-all-storage"])
            
            # Remove metadata
            meta = load_meta()
            if name in meta:
                del meta[name]
                save_meta(meta)
                
            # Cleanup dir
            if os.path.exists(instance_dir):
                import shutil
                try:
                    shutil.rmtree(instance_dir)
                except Exception:
                    pass
                    
            job["status"] = "completed"
            job["finished_at"] = time.time()
            log(f"Instance '{name}' destroyed and removed.")
        except Exception as e:
            job["status"] = "failed"
            job["finished_at"] = time.time()
            job["error"] = str(e)
            log(f"ERROR: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "job_id": job_id}
