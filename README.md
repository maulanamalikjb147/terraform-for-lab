# CloudLab VPS Hypervisor & Cloud Control Panel

Sistem Virtualisasi KVM & Terraform otomatis dengan **Modern Web Dashboard** bergaya Cloud/VPS Provider (seperti DigitalOcean, Linode, Hetzner Cloud). Dilengkapi dengan fitur **Admin Login**, monitoring resource host real-time, IPAM network tracker, cloud image downloader, dan paket instalasi `.deb` (`dpkg`) otomatis.

Cukup install file `.deb` di server Ubuntu (x86_64 / amd64), semua komponen KVM, Libvirt, Nested Virtualization, Storage Pool, Network Bridge, IPTables NAT, Terraform binary, dan Terraform Libvirt Provider akan otomatis terkonfigurasi dan dashboard langsung aktif!

---

## 🚀 Cara Install Otomatis via DPKG (Rekomendasi)

Paket installer `.deb` dapat diunduh dari **GitHub Releases** atau di-build sendiri menggunakan `./build.sh`.

### 1. Download & Install di Server Ubuntu (x86_64)
Di server Ubuntu target, unduh dan pasang paket `.deb`:
```bash
# Unduh release terbaru:
wget https://github.com/maulanamalikjb147/terraform-for-lab/releases/latest/download/tf-vps-panel_1.0.0_amd64.deb

# Install menggunakan apt (otomatis install semua dependensi qemu, libvirt, bridge-utils, dll):
sudo apt update
sudo apt install -y ./tf-vps-panel_1.0.0_amd64.deb

# ATAU menggunakan dpkg langsung:
sudo dpkg -i tf-vps-panel_1.0.0_amd64.deb
sudo apt-get install -f -y
```

### 2. Apa yang Otomatis Dilakukan oleh Paket `.deb`?
Ketika `dpkg -i` dijalankan di Ubuntu, script `postinst` otomatis mengeksekusi semua konfigurasi dari awal sampai siap pakai:
1. ✅ Mengaktifkan **Nested Virtualization** di `/etc/modprobe.d/kvm.conf` (`kvm_intel` / `kvm_amd` nested=1).
2. ✅ Mengonfigurasi **QEMU security driver** (`security_driver = "none"` di `/etc/libvirt/qemu.conf`).
3. ✅ Mengaktifkan dan menjalankan service `libvirtd` (`systemctl enable --now libvirtd`).
4. ✅ Memasang binary **Terraform** di `/usr/local/bin/terraform`.
5. ✅ Memasang dan meregistrasi **Terraform Libvirt Provider** (`terraform-provider-libvirt`) di direktori plugin Terraform.
6. ✅ Membuat dan mengaktifkan Storage Pool `/data/vms` dan `/data/isos` di KVM.
7. ✅ Mendefinisikan, mengaktifkan, dan meng-autostart virtual network `net-10.10.10` (bridge `virbr10`, subnet `10.10.10.0/24`, gateway `10.10.10.1`).
8. ✅ Mengaktifkan kernel IPv4 forwarding (`net.ipv4.ip_forward=1`) dan IPTables NAT Masquerade agar VM dapat langsung akses internet.
9. ✅ Memasang service systemd `tf-vps-panel.service` dan langsung menjalankannya di port `8080`.

---

## 🌐 Akses Web Dashboard

Buka browser dan akses IP server Anda:
```
http://<IP-SERVER-UBUNTU>:8080/
```

### 🔑 Kredensial Login Default:
- **Username:** `admin`
- **Password:** `admin123`

*(Password dapat langsung diubah di menu **Admin Settings** di dalam dashboard atau melalui CLI)*.

---

## ✨ Fitur Dashboard

1. **🖥️ Instances (VPS) Management:**
   - Melihat daftar seluruh VM aktif / non-aktif beserta CPU, RAM, Disk, dan IP address.
   - Status badge real-time (Running 🟢, Stopped 🔴).
   - Tombol Power: **Start**, **Stop (ACPI Shutdown)**, **Reboot**, **Force Stop (Kill)**.
   - **VNC & SSH Console:** Langsung menampilkan display port VNC (e.g. 5900) dan perintah SSH one-click.
   - **📸 1-Click Snapshot to Template Image:** Mengambil snapshot disk VM dan menyimpannya sebagai golden image baru di `/data/isos/` sehingga Anda bisa membuat VM-VM baru langsung dari snapshot tersebut!
   - **Terminate / Destroy:** Menghapus VM dan storage virtual disk secara bersih via Terraform & virsh.

2. **⚡ Deploy VPS (Creation Wizard):**
   - **Pilih OS:** Ubuntu 22.04 LTS (Jammy), Ubuntu 20.04 LTS (Focal), Ubuntu 24.04 (Noble), Debian 12.
   - **Pilih Paket Hardware:** Preset *Starter* (1vCPU/1GB), *Standard* (2vCPU/2GB), *Pro* (2vCPU/4GB), *Performance* (4vCPU/8GB), atau gunakan **Custom Slider** bebas (vCPU, RAM, Disk SSD).
   - **IPAM / Network:** Auto-suggest IP kosong berikutnya (contoh `10.10.10.10`, `10.10.10.11`).
   - **Cloud-Init:** Input SSH Public Key dan password root/student dengan generator password acak.
   - **Real-Time Deployment Logs Modal:** Menampilkan output live `terraform init` dan `terraform apply` saat pembuatan instance!

3. **💾 Storage & Cloud Images (Customizer / Baker):**
   - Monitoring kapasitas storage pool `/data/vms` dan `/data/isos`.
   - **1-Click Download Official Cloud Image:** Download Ubuntu Jammy/Focal/Noble langsung dari web dengan progress bar real-time.
   - **🛠️ Customize / Mod Image Langsung dari Dashboard:**
     - Klik tombol **Customize** di sebelah image (misal `ubuntu-jammy.img`).
     - Centang preset **Docker CE & Docker Compose**, opsi upgrade apt, root password, atau script bash kustom.
     - Tentukan nama file baru (contoh `ubuntu-jammy-docker.img`), lalu klik **Bake Image**.
     - Dashboard akan menjalankan `virt-customize` secara offline dan menampilkan log prosesnya secara real-time!
     - Image hasil modifikasi langsung tersimpan di `/data/isos` dan siap dipilih saat Deploy VM.

4. **🌐 Multi-Network & IPAM Tracker:**
   - **Multi-Network Management:** Mendukung banyak subnet sekaligus (misal `net-10.10.10`, `net-10.20.10`, dll).
   - **+ 1-Click Add Virtual Network:** Buat network baru langsung dari Dashboard! Cukup masukkan nama network, interface bridge (misal `virbr20`), dan gateway IP. Dashboard otomatis membuat file `.xml`, menjalankan `virsh net-define`, `virsh net-autostart`, `virsh net-start`, serta konfigurasi IPTables NAT masquerade.
   - **Auto-Discovery:** Jika Anda membuat network manual via file `.xml` dan `virsh` di CLI seperti dulu, dashboard secara otomatis mendeteksinya tanpa perlu restart.
   - **Dynamic Network Deploy:** Pilih network subnet saat deploy instance di Wizard, IP address otomatis teralokasi di subnet yang dipilih.

5. **📊 Host Health Monitoring:**
   - Real-time CPU Usage %, RAM Usage, KVM Driver, dan status Libvirt daemon.

---

## 🛠️ CLI Management Tool (`tf-vps-panel`)

Setelah instalasi `.deb`, command `tf-vps-panel` tersedia di terminal server:

```bash
# Cek status hypervisor, service panel, storage pool, dan VM
tf-vps-panel status

# Restart service dashboard
tf-vps-panel restart

# Lihat logs real-time dashboard
tf-vps-panel logs

# Reset password admin secara instan dari terminal
tf-vps-panel reset-password <password_baru>
```

---

## 🔨 Cara Build Ulang Paket `.deb`

Jika Anda memodifikasi kode dashboard atau script:
```bash
./build.sh
# atau:
python3 build-deb.py
```
File `tf-vps-panel_1.0.0_amd64.deb` akan otomatis di-generate ulang.

---

## 📖 Manual Setup Reference (Tanpa DPKG)

Jika ingin melakukan setup manual tanpa paket `.deb`, ikuti langkah berikut:

### 1. Install "Virtualization Host" KVM
```bash
sudo apt -y install bridge-utils cpu-checker libvirt-clients libvirt-daemon qemu qemu-kvm libvirt-daemon-system mkisofs gawk genisoimage iptables-persistent
```

### 2. Enable nested virtualization
```bash
cat <<EOF > /etc/modprobe.d/kvm.conf
options kvm_intel nested=1
options kvm_amd nested=1
EOF
sudo modprobe -r kvm_intel && sudo modprobe kvm_intel nested=1
sudo modprobe -r kvm_amd && sudo modprobe kvm_amd nested=1
systemctl enable --now libvirtd
```

### 3. Setup Terraform & Provider
```bash
wget https://releases.hashicorp.com/terraform/0.13.7/terraform_0.13.7_linux_amd64.zip
apt install unzip -y
unzip terraform_0.13.7_linux_amd64.zip
mv terraform /usr/local/bin/

mkdir -p ~/.local/share/terraform/plugins/registry.terraform.io/dmacvicar/libvirt/0.6.2/linux_amd64
cp provider/terraform-provider-libvirt ~/.local/share/terraform/plugins/registry.terraform.io/dmacvicar/libvirt/0.6.2/linux_amd64/
```

### 4. Setup Virtual Network & Storage Pools
```bash
virsh net-define dashboard/config/net-10-10-10.xml
virsh net-start net-10.10.10
virsh net-autostart net-10.10.10

mkdir -p /data/isos /data/vms
virsh pool-define-as vms dir - - - - "/data/vms"
virsh pool-define-as isos dir - - - - "/data/isos"
virsh pool-autostart vms && virsh pool-autostart isos
virsh pool-start vms && virsh pool-start isos
```

### 5. Download Cloud Images
```bash
cd /data/isos
wget https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img -O ubuntu-jammy.img
wget https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img -O ubuntu-focal.img
virsh pool-refresh isos
```

### 6. Jalankan Dashboard Secara Manual
```bash
python3 dashboard/server.py --port 8080
```
Buka browser: `http://<IP_HOST>:8080/` (User: `admin`, Pass: `admin123`).
