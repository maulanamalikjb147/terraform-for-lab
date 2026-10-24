"""
Configuration and constants for CloudLab VPS Hypervisor Dashboard
"""
import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("TF_DATA_DIR", "/data")
ISOS_DIR = os.path.join(DATA_DIR, "isos")
VMS_DIR = os.path.join(DATA_DIR, "vms")
INSTANCES_DIR = os.environ.get("TF_INSTANCES_DIR", os.path.join(BASE_DIR, "instances"))
CONFIG_DIR = os.environ.get("TF_CONFIG_DIR", os.path.join(BASE_DIR, "config"))
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
JOBS_FILE = os.path.join(CONFIG_DIR, "jobs.json")

DEFAULT_NETWORK_NAME = "net-10.10.10"
DEFAULT_BRIDGE_NAME = "virbr10"
DEFAULT_SUBNET = "10.10.10.0/24"
DEFAULT_GATEWAY = "10.10.10.1"

# Popular Cloud Images Catalog
POPULAR_IMAGES = [
    {
        "id": "ubuntu-22.04",
        "name": "Ubuntu 22.04 LTS (Jammy Jellyfish)",
        "filename": "ubuntu-jammy.img",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        "size_estimate": "650 MB",
        "distro": "ubuntu",
        "default_user": "student"
    },
    {
        "id": "ubuntu-22.04-docker",
        "name": "Ubuntu 22.04 LTS + Docker Pre-baked",
        "filename": "ubuntu-jammy-docker.img",
        "url": "",
        "size_estimate": "1.1 GB",
        "distro": "ubuntu",
        "default_user": "student"
    },
    {
        "id": "ubuntu-20.04",
        "name": "Ubuntu 20.04 LTS (Focal Fossa)",
        "filename": "ubuntu-focal.img",
        "url": "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img",
        "size_estimate": "550 MB",
        "distro": "ubuntu",
        "default_user": "student"
    },
    {
        "id": "ubuntu-24.04",
        "name": "Ubuntu 24.04 LTS (Noble Numbat)",
        "filename": "ubuntu-noble.img",
        "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "size_estimate": "700 MB",
        "distro": "ubuntu",
        "default_user": "student"
    },
    {
        "id": "debian-12",
        "name": "Debian 12 (Bookworm)",
        "filename": "debian-12.img",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
        "size_estimate": "400 MB",
        "distro": "debian",
        "default_user": "debian"
    }
]

def ensure_directories():
    """Ensure required directories exist"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(INSTANCES_DIR, exist_ok=True)
    try:
        os.makedirs(ISOS_DIR, exist_ok=True)
        os.makedirs(VMS_DIR, exist_ok=True)
    except Exception:
        # Might need root on target, safe to pass
        pass
