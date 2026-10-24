#!/bin/bash
# ==============================================================
# Helper script to build the Debian package (.deb)
# Works on macOS, Linux, and any system with Python 3
# ==============================================================

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "Building CloudLab VPS Hypervisor Debian package..."
python3 build-deb.py

echo ""
echo "Done! The .deb package is ready for deployment on Ubuntu (x86_64)."
