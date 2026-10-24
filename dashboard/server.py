#!/usr/bin/env python3
"""
CloudLab VPS Hypervisor Dashboard & API Server
Built with Python 3 Standard Library - Zero external dependencies.
"""
import os
import sys
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# Add current dir to sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.config import ensure_directories
from backend.auth import (
    login, verify_token, logout, change_password,
    reset_password_cli, init_default_admin, load_users
)
from backend.system_stats import get_system_summary
from backend.libvirt_manager import (
    list_domains, start_domain, stop_domain, force_stop_domain,
    reboot_domain, get_domain_details, create_vm_snapshot_async, get_snapshot_job,
    sparsify_vm_disk_async, get_sparsify_job
)
from backend.storage_manager import (
    list_iso_images, get_catalog, start_image_download,
    get_download_tasks, delete_image, customize_image_async, get_customize_job
)
from backend.network_manager import (
    get_network_info, list_all_networks, create_virtual_network, delete_virtual_network
)
from backend.terraform_runner import (
    deploy_instance_async, destroy_instance_async, list_jobs, get_job
)
from backend.vnc_ws import bridge_vnc_websocket

STATIC_DIR = os.path.join(BASE_DIR, "static")

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon"
}

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CloudLab-Hypervisor/1.0"
    
    def log_message(self, format, *args):
        # Format clean log messages
        sys.stderr.write(f"[{self.log_date_time_string()}] {args[0]} {args[1]} -> {args[2]}\n")

    def send_json(self, data: dict, status_code: int = 200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status_code: int = 400):
        self.send_json({"success": False, "error": message}, status_code)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def get_auth_token(self) -> str:
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        # Check query string token fallback
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "token" in params:
            return params["token"][0]
        return ""

    def authenticate(self) -> dict:
        token = self.get_auth_token()
        session = verify_token(token)
        return session

    def read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        try:
            body = self.rfile.read(content_length).decode("utf-8")
            return json.loads(body)
        except Exception as e:
            return {}

    def serve_static(self, filepath: str):
        if filepath == "/" or not filepath:
            filepath = "/index.html"
            
        clean_path = os.path.normpath(filepath.lstrip("/"))
        full_path = os.path.join(STATIC_DIR, clean_path)
        
        # Security check: ensure path is inside STATIC_DIR
        if not full_path.startswith(STATIC_DIR) or not os.path.isfile(full_path):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return
            
        ext = os.path.splitext(full_path)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        
        try:
            with open(full_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error_json(str(e), 500)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # VNC WebSocket Tunneling (RFC 6455 Websockify)
        if path == "/api/vnc/ws":
            params = urllib.parse.parse_qs(parsed.query)
            token = params.get("token", [""])[0] or self.get_auth_token()
            if not verify_token(token):
                self.send_error(401, "Unauthorized VNC session")
                return
            try:
                port_num = int(params.get("port", ["5900"])[0])
            except (ValueError, TypeError):
                port_num = 5900
            return bridge_vnc_websocket(self, vnc_port=port_num)
        
        if not path.startswith("/api/"):
            return self.serve_static(path)
            
        # Public endpoints
        if path == "/api/auth/status":
            return self.send_json({"initialized": True})
            
        # Protected endpoints
        session = self.authenticate()
        if not session:
            return self.send_error_json("Unauthorized. Please log in.", 401)
            
        if path == "/api/auth/me":
            return self.send_json({"user": session})
            
        elif path == "/api/system/stats":
            return self.send_json(get_system_summary())
            
        elif path == "/api/instances":
            return self.send_json({"instances": list_domains()})
            
        elif path.startswith("/api/instances/jobs/"):
            job_id = path[len("/api/instances/jobs/"):]
            job = get_job(job_id)
            if job:
                return self.send_json(job)
            return self.send_error_json("Job not found", 404)

        elif path.startswith("/api/instances/snapshot-jobs/"):
            job_id = path[len("/api/instances/snapshot-jobs/"):]
            job = get_snapshot_job(job_id)
            if job:
                return self.send_json(job)
            return self.send_error_json("Snapshot job not found", 404)

        elif path.startswith("/api/instances/sparsify-jobs/"):
            job_id = path[len("/api/instances/sparsify-jobs/"):]
            job = get_sparsify_job(job_id)
            if job:
                return self.send_json(job)
            return self.send_error_json("Sparsify job not found", 404)
            
        elif path == "/api/instances/jobs":
            return self.send_json({"jobs": list_jobs()})
            
        elif path == "/api/images":
            return self.send_json({
                "images": list_iso_images(),
                "tasks": get_download_tasks()
            })
            
        elif path.startswith("/api/images/jobs/"):
            job_id = path[len("/api/images/jobs/"):]
            job = get_customize_job(job_id)
            if job:
                return self.send_json(job)
            return self.send_error_json("Job not found", 404)
            
        elif path == "/api/images/catalog":
            return self.send_json({
                "catalog": get_catalog(),
                "tasks": get_download_tasks()
            })
            
        elif path == "/api/networks" or path == "/api/networks/list":
            query = urllib.parse.parse_qs(parsed.query)
            selected_name = query.get("name", [None])[0]
            networks = list_all_networks()
            detail = get_network_info(selected_name)
            return self.send_json({
                "networks": networks,
                "current": detail
            })

        elif path.startswith("/api/networks/"):
            net_name = path[len("/api/networks/"):]
            detail = get_network_info(net_name)
            return self.send_json(detail)
            
        else:
            return self.send_error_json("Endpoint not found", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        data = self.read_json_body()
        
        # Login is public
        if path == "/api/auth/login":
            username = data.get("username", "").strip()
            password = data.get("password", "")
            res = login(username, password)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Login failed"), 401)
            
        # All other POSTs require authentication
        session = self.authenticate()
        if not session:
            return self.send_error_json("Unauthorized", 401)
            
        if path == "/api/auth/logout":
            token = self.get_auth_token()
            logout(token)
            return self.send_json({"success": True})
            
        elif path == "/api/auth/change-password":
            old_pass = data.get("old_password", "")
            new_pass = data.get("new_password", "")
            res = change_password(session["username"], old_pass, new_pass)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Password change failed"), 400)
            
        elif path == "/api/instances/deploy":
            res = deploy_instance_async(data)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Deployment failed"), 400)
            
        elif path.startswith("/api/instances/") and path.endswith("/start"):
            name = path.split("/")[3]
            res = start_domain(name)
            return self.send_json(res)
            
        elif path.startswith("/api/instances/") and path.endswith("/stop"):
            name = path.split("/")[3]
            res = stop_domain(name)
            return self.send_json(res)
            
        elif path.startswith("/api/instances/") and path.endswith("/force-stop"):
            name = path.split("/")[3]
            res = force_stop_domain(name)
            return self.send_json(res)
            
        elif path.startswith("/api/instances/") and path.endswith("/reboot"):
            name = path.split("/")[3]
            res = reboot_domain(name)
            return self.send_json(res)

        elif path.startswith("/api/instances/") and path.endswith("/snapshot"):
            name = path.split("/")[3]
            res = create_vm_snapshot_async(name, data)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Snapshot failed"), 400)

        elif path.startswith("/api/instances/") and path.endswith("/sparsify"):
            name = path.split("/")[3]
            res = sparsify_vm_disk_async(name, data)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Sparsify failed"), 400)
            
        elif path == "/api/images/download":
            url = data.get("url", "").strip()
            filename = data.get("filename", "").strip()
            if not url or not filename:
                return self.send_error_json("URL and filename are required")
            res = start_image_download(url, filename)
            return self.send_json(res)

        elif path == "/api/images/customize":
            res = customize_image_async(data)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Customization failed"), 400)

        elif path == "/api/networks" or path == "/api/networks/create":
            res = create_virtual_network(data)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Failed to create network"), 400)
            
        else:
            return self.send_error_json("Endpoint not found", 404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        session = self.authenticate()
        if not session:
            return self.send_error_json("Unauthorized", 401)
            
        if path.startswith("/api/instances/"):
            name = path.split("/")[3]
            res = destroy_instance_async(name)
            return self.send_json(res)
            
        elif path.startswith("/api/images/"):
            filename = path.split("/")[3]
            res = delete_image(filename)
            return self.send_json(res)

        elif path.startswith("/api/networks/"):
            net_name = path.split("/")[3]
            res = delete_virtual_network(net_name)
            if res.get("success"):
                return self.send_json(res)
            return self.send_error_json(res.get("error", "Failed to delete network"), 400)
            
        else:
            return self.send_error_json("Endpoint not found", 404)

def run_server(host="0.0.0.0", port=8080):
    ensure_directories()
    init_default_admin()
    
    server_address = (host, port)
    httpd = ThreadedHTTPServer(server_address, DashboardHandler)
    print(f"==================================================")
    print(f"🚀 CloudLab VPS Hypervisor Dashboard running on:")
    print(f"👉 http://{host}:{port}/")
    print(f"Default admin login: admin / admin123")
    print(f"==================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server gracefully...")
        httpd.server_close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reset-password":
        username = sys.argv[2] if len(sys.argv) > 2 else "admin"
        new_password = sys.argv[3] if len(sys.argv) > 3 else "admin123"
        reset_password_cli(username, new_password)
        print(f"Password for user '{username}' has been reset successfully.")
        sys.exit(0)
        
    port = int(os.environ.get("PORT", 8080))
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
            
    run_server(port=port)
