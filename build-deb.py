#!/usr/bin/env python3
"""
Universal Debian Package (.deb) Builder
Constructs 100% compliant Debian binary packages using only Python standard library.
Works on macOS, Linux, and BSD without requiring dpkg-deb.
"""
import os
import sys
import tarfile
import io
import shutil
import time

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEBIAN_DIR = os.path.join(REPO_ROOT, "debian")
PACKAGE_NAME = "tf-vps-panel"
VERSION = "1.0.0"
ARCH = "amd64"
OUTPUT_DEB = os.path.join(REPO_ROOT, f"{PACKAGE_NAME}_{VERSION}_{ARCH}.deb")

def create_ar_header(filename: str, size: int, mode: int = 0o100644, mtime: int = None) -> bytes:
    """Create 60-byte GNU/Debian ar file header"""
    if mtime is None:
        mtime = int(time.time())
    # Standard Debian ar archive member name (16 bytes, ASCII space-padded)
    name_field = filename.ljust(16)
    mtime_field = str(mtime).ljust(12)
    uid_field = "0".ljust(6)
    gid_field = "0".ljust(6)
    mode_field = oct(mode)[2:].rjust(6, "0").ljust(8)
    size_field = str(size).ljust(10)
    magic = b"`\n"
    
    header = (
        name_field.encode("ascii") +
        mtime_field.encode("ascii") +
        uid_field.encode("ascii") +
        gid_field.encode("ascii") +
        mode_field.encode("ascii") +
        size_field.encode("ascii") +
        magic
    )
    assert len(header) == 60, f"Header length must be 60 bytes, got {len(header)}"
    return header

def build_control_tar() -> bytes:
    """Pack debian/DEBIAN into control.tar.gz"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        debian_meta_dir = os.path.join(DEBIAN_DIR, "DEBIAN")
        for fname in sorted(os.listdir(debian_meta_dir)):
            fpath = os.path.join(debian_meta_dir, fname)
            if os.path.isfile(fpath):
                stat = os.stat(fpath)
                mode = 0o755 if fname in ["postinst", "prerm", "postrm", "config"] else 0o644
                
                ti = tarfile.TarInfo(name=f"./{fname}")
                ti.size = stat.st_size
                ti.mtime = int(stat.st_mtime)
                ti.mode = mode
                ti.uname = "root"
                ti.gname = "root"
                ti.uid = 0
                ti.gid = 0
                
                with open(fpath, "rb") as f:
                    tar.addfile(ti, f)
                    
    return buf.getvalue()

def build_data_tar() -> bytes:
    """Pack filesystem tree into data.tar.gz"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        
        def add_file(src_path, dest_path, mode=None):
            if not os.path.exists(src_path):
                print(f"Warning: source file {src_path} does not exist, skipping.")
                return
            stat = os.stat(src_path)
            if mode is None:
                mode = 0o755 if (stat.st_mode & 0o111) else 0o644
            ti = tarfile.TarInfo(name=dest_path)
            ti.size = stat.st_size
            ti.mtime = int(stat.st_mtime)
            ti.mode = mode
            ti.uname = "root"
            ti.gname = "root"
            ti.uid = 0
            ti.gid = 0
            with open(src_path, "rb") as f:
                tar.addfile(ti, f)

        def add_dir(dest_path, mode=0o755):
            ti = tarfile.TarInfo(name=dest_path)
            ti.type = tarfile.DIRTYPE
            ti.mode = mode
            ti.mtime = int(time.time())
            ti.uname = "root"
            ti.gname = "root"
            ti.uid = 0
            ti.gid = 0
            tar.addfile(ti)

        def add_tree(src_dir, dest_prefix):
            for root, dirs, files in os.walk(src_dir):
                rel = os.path.relpath(root, src_dir)
                target_dir = dest_prefix if rel == "." else os.path.join(dest_prefix, rel)
                add_dir(target_dir)
                for f in sorted(files):
                    if f.startswith(".git") or f.endswith(".pyc") or f == "__pycache__":
                        continue
                    s = os.path.join(root, f)
                    d = os.path.join(target_dir, f)
                    add_file(s, d)

        # Base directories
        add_dir("./opt")
        add_dir("./opt/tf-vps-panel")
        add_dir("./etc")
        add_dir("./etc/systemd")
        add_dir("./etc/systemd/system")
        add_dir("./usr")
        add_dir("./usr/local")
        add_dir("./usr/local/bin")
        add_dir("./data")
        add_dir("./data/isos")
        add_dir("./data/vms")

        # 1. Dashboard application
        dashboard_dir = os.path.join(REPO_ROOT, "dashboard")
        add_tree(dashboard_dir, "./opt/tf-vps-panel")

        # Ensure server.py is executable
        add_file(
            os.path.join(dashboard_dir, "server.py"),
            "./opt/tf-vps-panel/server.py",
            mode=0o755
        )

        # 2. Existing repo scripts & provider
        add_file(
            os.path.join(REPO_ROOT, "not-tfgen.sh"),
            "./opt/tf-vps-panel/not-tfgen.sh",
            mode=0o755
        )
        add_file(
            os.path.join(REPO_ROOT, "bake-docker-image.sh"),
            "./opt/tf-vps-panel/bake-docker-image.sh",
            mode=0o755
        )
        add_tree(
            os.path.join(REPO_ROOT, "functionlib"),
            "./opt/tf-vps-panel/functionlib"
        )
        add_tree(
            os.path.join(REPO_ROOT, "provider"),
            "./opt/tf-vps-panel/provider"
        )
        # Ensure provider is executable
        add_file(
            os.path.join(REPO_ROOT, "provider", "terraform-provider-libvirt"),
            "./opt/tf-vps-panel/provider/terraform-provider-libvirt",
            mode=0o755
        )

        # 3. Systemd service unit
        service_file = os.path.join(DEBIAN_DIR, "etc/systemd/system/tf-vps-panel.service")
        add_file(service_file, "./etc/systemd/system/tf-vps-panel.service", mode=0o644)
        add_file(service_file, "./opt/tf-vps-panel/tf-vps-panel.service", mode=0o644)

        # 4. CLI helper
        cli_file = os.path.join(DEBIAN_DIR, "usr/local/bin/tf-vps-panel")
        add_file(cli_file, "./usr/local/bin/tf-vps-panel", mode=0o755)

    return buf.getvalue()

def build_deb():
    print(f"📦 Packaging {PACKAGE_NAME} version {VERSION} ({ARCH})...")
    
    # 1. debian-binary content
    deb_binary = b"2.0\n"
    
    # 2. control.tar.gz
    print(" -> Compressing control.tar.gz (metadata & postinst)...")
    control_tar = build_control_tar()
    
    # 3. data.tar.gz
    print(" -> Compressing data.tar.gz (application, provider, services)...")
    data_tar = build_data_tar()
    
    # 4. Assemble ar archive
    print(f" -> Assembling Debian archive: {os.path.basename(OUTPUT_DEB)}")
    with open(OUTPUT_DEB, "wb") as deb:
        # AR Magic Header
        deb.write(b"!<arch>\n")
        
        # 1. debian-binary
        deb.write(create_ar_header("debian-binary", len(deb_binary), 0o100644))
        deb.write(deb_binary)
        if len(deb_binary) % 2 != 0:
            deb.write(b"\n")
            
        # 2. control.tar.gz
        deb.write(create_ar_header("control.tar.gz", len(control_tar), 0o100644))
        deb.write(control_tar)
        if len(control_tar) % 2 != 0:
            deb.write(b"\n")
            
        # 3. data.tar.gz
        deb.write(create_ar_header("data.tar.gz", len(data_tar), 0o100644))
        deb.write(data_tar)
        if len(data_tar) % 2 != 0:
            deb.write(b"\n")
            
    size_mb = os.path.getsize(OUTPUT_DEB) / (1024 * 1024)
    print(f"✨ Successfully generated Debian package:")
    print(f"   📁 File: {OUTPUT_DEB}")
    print(f"   📏 Size: {size_mb:.2f} MB")
    print(f"")
    print(f"🚀 To install on target Ubuntu server:")
    print(f"   sudo dpkg -i {os.path.basename(OUTPUT_DEB)}")
    print(f"   # or with dependency auto-resolution:")
    print(f"   sudo apt install -f -y ./{os.path.basename(OUTPUT_DEB)}")

if __name__ == "__main__":
    build_deb()
