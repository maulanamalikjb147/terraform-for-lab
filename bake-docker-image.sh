#!/bin/bash
# ==============================================================
# Bake Docker CE into Ubuntu Cloud Image (Offline Customization)
# Produces ubuntu-jammy-docker.img ready in /data/isos
# ==============================================================

set -e

SOURCE_IMG="${1:-/data/isos/ubuntu-jammy.img}"
OUTPUT_IMG="${2:-/data/isos/ubuntu-jammy-docker.img}"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo)."
    exit 1
fi

if [ ! -f "$SOURCE_IMG" ]; then
    echo "Error: Source image $SOURCE_IMG not found!"
    echo "Download it first via Dashboard or:"
    echo "curl -L -o /data/isos/ubuntu-jammy.img https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"
    exit 1
fi

# Ensure libguestfs-tools is installed
if ! command -v virt-customize >/dev/null 2>&1; then
    echo "Installing libguestfs-tools for offline image customization..."
    apt-get update -y
    apt-get install -y libguestfs-tools
fi

echo "Creating copy of base image: $OUTPUT_IMG ..."
cp -v "$SOURCE_IMG" "$OUTPUT_IMG"

echo "Expanding virtual disk by +6GB to ensure adequate space for packages..."
qemu-img resize "$OUTPUT_IMG" +6G

echo "Injecting Docker CE repository and packages into $OUTPUT_IMG ..."

# Run virt-customize
virt-customize -a "$OUTPUT_IMG" \
    --run-command 'growpart /dev/sda 1 2>/dev/null || growpart /dev/vda 1 2>/dev/null || true' \
    --run-command 'resize2fs /dev/sda1 2>/dev/null || resize2fs /dev/vda1 2>/dev/null || true' \
    --run-command 'apt-get update' \
    --install ca-certificates,curl \
    --run-command 'install -m 0755 -d /etc/apt/keyrings' \
    --run-command 'curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc' \
    --run-command 'chmod a+r /etc/apt/keyrings/docker.asc' \
    --run-command 'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" > /etc/apt/sources.list.d/docker.list' \
    --run-command 'apt-get update' \
    --run-command 'apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin' \
    --run-command 'systemctl enable docker'

echo "Refreshing Libvirt isos storage pool..."
virsh pool-refresh isos 2>/dev/null || true

echo ""
echo "=========================================================="
echo "✓ Docker Golden Image created successfully: $OUTPUT_IMG"
echo "You can now select '$OUTPUT_IMG' when deploying VMs!"
echo "=========================================================="
