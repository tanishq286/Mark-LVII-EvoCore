<#
.SYNOPSIS
    Mark-LVII Phone Remote Control Root-Cause Diagnostic & Network/Firewall/TLS Repair.
.DESCRIPTION
    1. Detects the real active Wi-Fi / Ethernet IPv4 address while ignoring virtual
       adapters (WSL, vEthernet, Hyper-V, Docker, VirtualBox, VMware, Bluetooth, Tailscale).
    2. Inspects the active Windows Network Category (Private / Domain / Public) and
       optionally switches trusted home Wi-Fi from Public -> Private (-SetPrivateProfile).
    3. Removes stale inbound Block rules on Python and adds/verifies the Windows Defender
       Firewall inbound rule for TCP port 8000 (and redirector 8001) on Private,Domain profiles.
    4. Ensures self-signed TLS cert + key exist in git-ignored memory/certs/ with SAN entries
       for localhost, 127.0.0.1, and the real active LAN IPv4 (plus Tailscale IP if present).
    5. Detects Tailscale & Cloudflare Quick Tunnel (cloudflared) for 4G/5G anywhere control.
    6. Verifies live HTTPS 200 reachability on /pwa, /api/cert-fingerprint, and /api/android/qr.
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$SetPrivateProfile,
    [switch]$StartQuickTunnel,
    [switch]$SkipLiveProbe
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VirtualPattern = "(?i)(vEthernet|WSL|Hyper-V|Docker|VirtualBox|VMware|Bluetooth|Loopback|Npcap|TAP-|TUN|WireGuard|ZeroTier|CloudflareWARP|ProtonVPN|Teredo|isatap)"

Write-Host "=== Mark-LVII Phone Remote Diagnostic & Fix ===" -ForegroundColor Cyan
Write-Host "Repo Root : $RepoRoot"

# 1. Detect real active Wi-Fi / Ethernet IPv4
$AllConfigs = Get-NetIPConfiguration | Where-Object {
    $_.NetAdapter -ne $null -and $_.NetAdapter.Status -eq "Up" -and $_.IPv4Address -ne $null
}

$PhysicalConfigs = $AllConfigs | Where-Object {
    $_.InterfaceAlias -notmatch $VirtualPattern -and
    $_.InterfaceDescription -notmatch $VirtualPattern -and
    $_.InterfaceAlias -notmatch "(?i)Tailscale"
}

$Preferred = $PhysicalConfigs | Where-Object { $_.IPv4DefaultGateway -ne $null } | Select-Object -First 1
if (-not $Preferred) {
    $Preferred = $PhysicalConfigs | Select-Object -First 1
}

if (-not $Preferred) {
    Write-Warning "No active physical Wi-Fi/Ethernet IPv4 adapter detected! Falling back to 127.0.0.1."
    $LanIp = "127.0.0.1"
    $IfaceAlias = "Loopback"
    $NetCategory = "Unknown"
} else {
    $LanIp = ($Preferred.IPv4Address | Where-Object {
        $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*"
    } | Select-Object -First 1).IPAddress
    $IfaceAlias = $Preferred.InterfaceAlias
    $NetCategory = if ($Preferred.NetProfile) { [string]$Preferred.NetProfile.NetworkCategory } else { "Unknown" }
}

Write-Host ("[1/6] Active LAN Adapter : {0} -> IPv4 {1} (Profile={2})" -f $IfaceAlias, $LanIp, $NetCategory) -ForegroundColor Green

# Check if network profile is Public
if ($NetCategory -eq "Public") {
    if ($SetPrivateProfile) {
        Write-Host "      Switching '$IfaceAlias' from Public -> Private..." -ForegroundColor Yellow
        Set-NetConnectionProfile -InterfaceAlias $IfaceAlias -NetworkCategory Private
        $NetCategory = "Private"
    } else {
        Write-Warning "Active network '$IfaceAlias' is set to 'Public'. Private/Domain firewall rules will NOT match until you switch Wi-Fi to Private in Windows Settings > Network & internet (or re-run with -SetPrivateProfile)."
    }
}

# 2. Check / Add Windows Defender Firewall inbound rule for TCP 8000 on Private,Domain
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
$RuleName = "Mark-LVII Phone Remote (TCP $Port)"

$ExistingPort8000Allow = $false
$NetshOut = & netsh.exe advfirewall firewall show rule name=all dir=in 2>$null
if ($NetshOut -match "LocalPort:\s+$Port\b" -and $NetshOut -match "JARVIS Dashboard Port $Port|$RuleName") {
    $ExistingPort8000Allow = $true
}

if ($IsAdmin) {
    # Remove any accidental Block rules targeting python.exe / pythonw.exe
    $PyCandidates = @("python.exe", "pythonw.exe")
    $BlockRules = Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True -ErrorAction SilentlyContinue | Where-Object {
        $af = $_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
        $af -and ($PyCandidates -contains [System.IO.Path]::GetFileName($af.Program).ToLower())
    }
    foreach ($br in $BlockRules) {
        Write-Host ("      Removing blocking firewall rule: {0}" -f $br.DisplayName) -ForegroundColor Yellow
        Remove-NetFirewallRule -Name $br.Name -ErrorAction SilentlyContinue
    }

    $ExistingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    if ($ExistingRule) {
        Set-NetFirewallRule -DisplayName $RuleName -Enabled True -Direction Inbound -Action Allow -Profile Private,Domain | Out-Null
    } else {
        New-NetFirewallRule -DisplayName $RuleName `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port,8001 `
            -Action Allow `
            -Profile Private,Domain `
            -Description "Allow Mark-LVII HTTPS Phone Remote & Companion WebSocket on Private/Domain LAN" | Out-Null
    }
    $ExistingPort8000Allow = $true
    Write-Host ("[2/6] Firewall Rule      : VERIFIED/UPDATED '{0}' (TCP {1}, Private+Domain, Allow)" -f $RuleName, $Port) -ForegroundColor Green
} else {
    if ($ExistingPort8000Allow) {
        Write-Host ("[2/6] Firewall Rule      : VERIFIED inbound TCP {0} Allow rule already active (non-elevated check)" -f $Port) -ForegroundColor Green
    } else {
        Write-Warning ("[2/6] Not running as Administrator and no TCP {0} inbound Allow rule found." -f $Port)
        Write-Host ("      Run elevated once: Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File `"{0}`"'" -f $MyInvocation.MyCommand.Path) -ForegroundColor Yellow
    }
}

# 3. Resolve Python interpreter & verify/generate SAN TLS certificate in memory/certs/
$PyExe = "C:\Users\tanis\AppData\Local\Python\pythoncore-3.13-64\python.exe"
if (-not (Test-Path $PyExe)) {
    $PyCmd = Get-Command python -ErrorAction SilentlyContinue
    $PyExe = if ($PyCmd) { $PyCmd.Source } else { throw "Python interpreter not found." }
}

$CertJson = & $PyExe -B -c "
import json, sys
sys.path.insert(0, r'$RepoRoot')
from dashboard import server as s
ok = s._ensure_certs(extra_ips=['$LanIp'] if '$LanIp' != '127.0.0.1' else [])
print(json.dumps({
    'certs_ok': ok,
    'cert_dir': str(s.CERTS_DIR),
    'detected_lan_ip': s._local_ip(),
    'sha256': s.cert_fingerprint_sha256(),
    'san': s.cert_san_entries(),
    'tailscale': s.detect_tailscale(),
    'cloudflare': s.get_tunnel_status(),
}))
"
$CertInfo = $CertJson | ConvertFrom-Json
Write-Host ("[3/6] TLS Certificate    : ok={0} | dir={1}" -f $CertInfo.certs_ok, $CertInfo.cert_dir) -ForegroundColor Green
Write-Host ("      SAN Entries        : {0}" -f ($CertInfo.san -join ", "))
Write-Host ("      TLS SHA-256 Pin    : {0}" -f $CertInfo.sha256)

# 4. Detect 4G/5G Remote Tunnel options (Tailscale / Cloudflare Quick Tunnel)
if ($CertInfo.tailscale.available) {
    Write-Host ("[4/6] Tailscale 4G/5G    : ACTIVE -> {0}" -f $CertInfo.tailscale.url) -ForegroundColor Green
} else {
    Write-Host "[4/6] Tailscale 4G/5G    : Not connected (install/login Tailscale for mesh 4G/5G access)" -ForegroundColor DarkGray
}

$CloudflaredCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($CloudflaredCmd) {
    Write-Host ("[5/6] Cloudflared Tunnel : Installed ({0})" -f $CloudflaredCmd.Source) -ForegroundColor Green
    if ($StartQuickTunnel) {
        Write-Host ("      Starting Quick Tunnel -> https://127.0.0.1:{0} ..." -f $Port) -ForegroundColor Cyan
        Start-Process -FilePath $CloudflaredCmd.Source -ArgumentList "tunnel","--url","https://127.0.0.1:$Port","--no-tls-verify" -NoNewWindow
    }
} else {
    Write-Host "[5/6] Cloudflared Tunnel : Not installed (optional: winget install Cloudflare.cloudflared)" -ForegroundColor DarkGray
}

# 5. Inspect 0.0.0.0:8000 listener & run live HTTPS reachability check
$Listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Listeners) {
    $Addrs = ($Listeners | Select-Object -ExpandProperty LocalAddress -Unique) -join ", "
    Write-Host ("[6/6] TCP :{0} Listener  : LISTENING on [{1}] (PID {2})" -f $Port, $Addrs, $Listeners[0].OwningProcess) -ForegroundColor Green
    if (-not $SkipLiveProbe) {
        $ProbeJson = & $PyExe -B -c "
import httpx, json
base = 'https://$LanIp`:$Port'
out = {'base': base}
with httpx.Client(verify=False, timeout=5.0) as c:
    r_pwa = c.get(base + '/pwa')
    r_fp  = c.get(base + '/api/cert-fingerprint')
    r_qr  = c.get(base + '/api/android/qr')
    out['pwa_status'] = r_pwa.status_code
    out['fp_status']  = r_fp.status_code
    out['qr_status']  = r_qr.status_code
    out['qr_body']    = r_qr.json() if r_qr.status_code == 200 else {}
print(json.dumps(out))
"
        $Probe = $ProbeJson | ConvertFrom-Json
        Write-Host ("      HTTPS /pwa         : HTTP {0}" -f $Probe.pwa_status) -ForegroundColor Green
        Write-Host ("      HTTPS /api/cert-fp : HTTP {0}" -f $Probe.fp_status) -ForegroundColor Green
        Write-Host ("      HTTPS /api/qr      : HTTP {0}" -f $Probe.qr_status) -ForegroundColor Green
        if ($Probe.qr_body.pairing_url) {
            Write-Host ""
            Write-Host "=== READY FOR 1-TAP PHONE PAIRING ===" -ForegroundColor Cyan
            Write-Host ("  HTTPS PWA URL : {0}/pwa" -f $Probe.base) -ForegroundColor White
            Write-Host ("  1-Tap QR URL  : {0}" -f $Probe.qr_body.pairing_url) -ForegroundColor Green
            Write-Host ("  8-Char PIN    : {0}" -f $Probe.qr_body.pin) -ForegroundColor Yellow
            Write-Host ("  TLS SHA-256   : {0}" -f $Probe.qr_body.cert_sha256) -ForegroundColor Gray
        }
    }
} else {
    Write-Host ("[6/6] TCP :{0} Listener  : Mark-LVII is not currently running (will bind https://0.0.0.0:{0} on launch)." -f $Port) -ForegroundColor Yellow
    Write-Host ("      Target Phone URL   : https://{0}:{1}/pwa" -f $LanIp, $Port) -ForegroundColor Cyan
}
