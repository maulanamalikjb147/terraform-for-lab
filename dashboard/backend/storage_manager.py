"""
Storage pool and cloud image repository manager
"""
import os
import time
import threading
import urllib.request
import subprocess
from .config import ISOS_DIR, VMS_DIR, POPULAR_IMAGES

# In-memory download and customization tasks tracker
_DOWNLOAD_TASKS = {}
_CUSTOMIZE_JOBS = {}

def get_customize_job(job_id: str) -> dict:
    """Return customization job details and logs"""
    return _CUSTOMIZE_JOBS.get(job_id)

def customize_image_async(params: dict) -> dict:
    """Bake/customize a cloud image using virt-customize in background"""
    source_filename = params.get("source_filename", "").strip()
    target_filename = params.get("target_filename", "").strip() or source_filename
    install_docker = params.get("install_docker", True)
    run_upgrade = params.get("run_upgrade", False)
    root_password = params.get("root_password", "").strip()
    custom_script = params.get("custom_script", "").strip()

    source_path = os.path.join(ISOS_DIR, source_filename)
    target_path = os.path.join(ISOS_DIR, target_filename)

    if not os.path.exists(source_path):
        return {"success": False, "error": f"Source image '{source_filename}' does not exist in /data/isos."}

    job_id = f"bake_{int(time.time())}_{target_filename}"
    job = {
        "id": job_id,
        "vm_name": target_filename,
        "action": "customize",
        "status": "queued",
        "logs": [f"[{time.strftime('%H:%M:%S')}] Image customization queued for '{target_filename}'..."],
        "started_at": time.time(),
        "finished_at": None,
        "error": None
    }
    _CUSTOMIZE_JOBS[job_id] = job

    def _worker():
        def log(msg):
            timestamp = time.strftime('%H:%M:%S')
            job["logs"].append(f"[{timestamp}] {msg}")

        try:
            # Step 1: Copy to target if creating new image
            if source_path != target_path:
                job["status"] = "copying"
                log(f"Creating copy: {source_filename} -> {target_filename}...")
                import shutil
                shutil.copyfile(source_path, target_path)
                log("Copy completed.")

            # Step 2: Ensure virt-customize is available
            job["status"] = "customizing"
            if not shutil.which("virt-customize"):
                log("virt-customize not found. Checking system tools...")

            # Expand disk by +6GB so apt never runs out of space
            try:
                subprocess.run(["qemu-img", "resize", target_path, "+6G"], capture_output=True, timeout=10)
                log("Expanded virtual disk by +6GB for package installation.")
            except Exception as e:
                pass

            # Build virt-customize arguments
            cmd = [
                "virt-customize", "-a", target_path,
                "--run-command", "growpart /dev/sda 1 2>/dev/null || growpart /dev/vda 1 2>/dev/null || true",
                "--run-command", "resize2fs /dev/sda1 2>/dev/null || resize2fs /dev/vda1 2>/dev/null || true"
            ]

            if run_upgrade:
                log("Option enabled: apt update & upgrade")
                cmd.extend(["--run-command", "apt-get update && apt-get upgrade -y"])

            if install_docker:
                log("Option enabled: Docker CE & Compose installation")
                cmd.extend([
                    "--run-command", "apt-get update",
                    "--install", "ca-certificates,curl",
                    "--run-command", "install -m 0755 -d /etc/apt/keyrings",
                    "--run-command", "curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc",
                    "--run-command", "chmod a+r /etc/apt/keyrings/docker.asc",
                    "--run-command", 'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \\"$VERSION_CODENAME\\") stable" > /etc/apt/sources.list.d/docker.list',
                    "--run-command", "apt-get update",
                    "--run-command", "apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
                    "--run-command", "systemctl enable docker"
                ])

            if root_password:
                log("Option enabled: Set root password")
                cmd.extend(["--root-password", f"password:{root_password}"])

            if custom_script:
                log("Option enabled: Custom bash commands")
                for line in custom_script.splitlines():
                    cmd_line = line.strip()
                    if cmd_line and not cmd_line.startswith("#"):
                        cmd.extend(["--run-command", cmd_line])

            log(f"Executing virt-customize on {target_path}...")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in proc.stdout:
                log(line.strip())
            proc.wait()

            if proc.returncode != 0:
                raise RuntimeError(f"virt-customize failed with exit code {proc.returncode}")

            refresh_pool("isos")
            job["status"] = "completed"
            job["finished_at"] = time.time()
            log(f"✓ Image '{target_filename}' customized and baked successfully! Ready to deploy.")
        except Exception as e:
            job["status"] = "failed"
            job["finished_at"] = time.time()
            job["error"] = str(e)
            log(f"ERROR: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "job_id": job_id}

def list_iso_images() -> list:
    """List all available cloud images / ISOs in /data/isos"""
    if not os.path.exists(ISOS_DIR):
        return []
    
    images = []
    try:
        for fname in sorted(os.listdir(ISOS_DIR)):
            if fname.startswith("."):
                continue
            fpath = os.path.join(ISOS_DIR, fname)
            if os.path.isfile(fpath):
                stat = os.stat(fpath)
                size_mb = round(stat.st_size / (1024 * 1024), 1)
                images.append({
                    "filename": fname,
                    "size_mb": size_mb,
                    "size_display": f"{size_mb} MB" if size_mb < 1024 else f"{round(size_mb/1024, 2)} GB",
                    "modified": int(stat.st_mtime),
                    "status": "ready"
                })
    except Exception as e:
        print(f"Error listing ISO images: {e}")
        
    return images

def refresh_pool(pool_name: str = "isos"):
    """Trigger virsh pool-refresh"""
    try:
        subprocess.run(["virsh", "pool-refresh", pool_name], capture_output=True, text=True, timeout=5)
    except Exception:
        pass

def get_catalog() -> list:
    """Get popular cloud images catalog merged with local availability"""
    local_images = {img["filename"]: img for img in list_iso_images()}
    
    catalog = []
    for item in POPULAR_IMAGES:
        is_downloaded = item["filename"] in local_images
        active_task = None
        for task_id, task in _DOWNLOAD_TASKS.items():
            if task["filename"] == item["filename"] and task["status"] == "downloading":
                active_task = task
                break
                
        catalog.append({
            **item,
            "downloaded": is_downloaded,
            "local_info": local_images.get(item["filename"]),
            "download_task": active_task
        })
    return catalog

def start_image_download(url: str, filename: str) -> dict:
    """Start background download of a cloud image to /data/isos"""
    os.makedirs(ISOS_DIR, exist_ok=True)
    target_path = os.path.join(ISOS_DIR, filename)
    
    task_id = f"dl_{int(time.time())}_{filename}"
    task = {
        "id": task_id,
        "filename": filename,
        "url": url,
        "target_path": target_path,
        "status": "downloading",
        "progress": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "error": None,
        "started_at": time.time()
    }
    _DOWNLOAD_TASKS[task_id] = task
    
    def _worker():
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "CloudLab-VPS-Hypervisor/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                total = int(response.headers.get("content-length", 0))
                task["total_bytes"] = total
                downloaded = 0
                chunk_size = 1024 * 1024  # 1 MB chunk
                
                temp_path = target_path + ".tmp"
                with open(temp_path, "wb") as out_f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded += len(chunk)
                        task["downloaded_bytes"] = downloaded
                        if total > 0:
                            task["progress"] = round((downloaded / total) * 100, 1)
                            
                os.rename(temp_path, target_path)
                task["status"] = "completed"
                task["progress"] = 100.0
                refresh_pool("isos")
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
            if os.path.exists(target_path + ".tmp"):
                try:
                    os.remove(target_path + ".tmp")
                except Exception:
                    pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "task_id": task_id}

def get_download_tasks() -> dict:
    """Return all download tasks"""
    return _DOWNLOAD_TASKS

def delete_image(filename: str) -> dict:
    """Delete an image from /data/isos"""
    fpath = os.path.join(ISOS_DIR, filename)
    if not os.path.exists(fpath):
        return {"success": False, "error": "File does not exist"}
    try:
        os.remove(fpath)
        refresh_pool("isos")
        return {"success": True, "message": f"{filename} deleted"}
    except Exception as e:
        return {"success": False, "error": str(e)}
