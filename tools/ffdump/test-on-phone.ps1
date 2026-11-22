<#
  test-on-phone.ps1 - run the decoder against Free Fire's own files, on a real
  phone, with no root and no game running.

  Why this exists as a script rather than as a list of commands: the failure
  modes are all silent. Push the x86_64 binary to an arm64 phone and the shell
  answers "exec format error", which says nothing. Push the libil2cpp.so that
  came out of the APK instead of the one sofix.py wrote and every field offset
  comes back as a plausible wrong number. Get the adb path wrong and it looks
  like the phone is not connected.

  Every one of those is checked here and named when it fails.

  Usage:
    .\tools\ffdump\test-on-phone.ps1
    .\tools\ffdump\test-on-phone.ps1 -Serial 1234567890
    .\tools\ffdump\test-on-phone.ps1 -Tag v2 -Class COW.GameFacade
#>
[CmdletBinding()]
param(
    [string]$Serial = "",
    [string]$Tag = "cheat",
    [string]$Class = "COW.GamePlay.Player",
    [string]$FieldFilter = "Transform",
    [string]$Lib = "D:\ffdump\fixed\libil2cpp.so",
    [string]$Meta = "D:\ffdump\meta\global-metadata.dat",
    [string]$WorkDir = "$env:LOCALAPPDATA\Temp\opencode\fftest"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Say($msg) { Write-Host "  $msg" }
function Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red; exit 1 }
function Ok($msg) { Write-Host "  [ok]   $msg" -ForegroundColor Green }

# --- find adb ---------------------------------------------------------------
$adb = $null
foreach ($cand in @(
    "adb",
    "C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
    "C:\Android\Sdk\platform-tools\adb.exe"
)) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $adb = (Get-Command $cand).Source; break }
    if (Test-Path $cand) { $adb = $cand; break }
}
if (-not $adb) { Fail "no adb found. Install platform-tools, or BlueStacks, and try again." }
Ok "adb: $adb"

# --- find the device --------------------------------------------------------
$lines = & $adb devices -l 2>&1 | Where-Object { $_ -match "\sdevice\b" }
$devices = @()
foreach ($l in $lines) {
    $parts = ($l -split "\s+")
    if ($parts.Count -ge 2 -and $parts[1] -eq "device") {
        $devices += [pscustomobject]@{ Serial = $parts[0]; Detail = $l }
    }
}
if ($devices.Count -eq 0) {
    $raw = & $adb devices 2>&1 | ForEach-Object { "    " + $_ }
    Write-Host ($raw -join "`n")
    Fail "no device in 'device' state. Plug the phone in, turn on USB debugging, and accept the RSA prompt on the phone screen."
}
if ($Serial) {
    $dev = $devices | Where-Object { $_.Serial -eq $Serial } | Select-Object -First 1
    if (-not $dev) { Fail "serial $Serial is not attached." }
} elseif ($devices.Count -eq 1) {
    $dev = $devices[0]
} else {
    Say "more than one device attached - pass -Serial"
    $devices | ForEach-Object { Say "  $($_.Serial)   $($_.Detail)" }
    exit 1
}
Ok "device $($dev.Serial)"

function Sh([string]$cmd) { (& $adb -s $dev.Serial shell $cmd 2>&1) -join "`n" }

# --- what the phone says about itself ---------------------------------------
$props = @{}
foreach ($p in @("ro.product.manufacturer", "ro.product.model", "ro.build.version.release",
                 "ro.build.version.sdk", "ro.product.cpu.abi", "ro.product.cpu.abilist",
                 "ro.miui.ui.version.name", "ro.build.version.opporom")) {
    $props[$p] = (Sh "getprop $p").Trim()
}
Say ""
Say "phone: $($props['ro.product.manufacturer']) $($props['ro.product.model'])"
Say "android: $($props['ro.build.version.release']) (api $($props['ro.build.version.sdk']))"
Say "abi: $($props['ro.product.cpu.abi'])   list: $($props['ro.product.cpu.abilist'])"
if ($props['ro.build.version.opporom']) { Say "rom: ColorOS $($props['ro.build.version.opporom'])" }
if ($props['ro.miui.ui.version.name']) { Say "rom: MIUI $($props['ro.miui.ui.version.name'])" }
Say ""

# The test binary has to match the phone, not the machine this runs on.
$abi = $props["ro.product.cpu.abi"]
$need = switch -Wildcard ($abi) {
    "arm64*" { "arm64-v8a" }
    "armeabi*" { "arm64-v8a" }   # a 64-bit phone that reports 32-bit first
    "x86_64" { "x86_64" }
    default { Fail "unsupported abi: $abi" }
}
Ok "will fetch test_real-$need"

# --- fetch the test binary --------------------------------------------------
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$bin = Join-Path $WorkDir "test_real-$need"
$url = "https://raw.githubusercontent.com/alokgacorcuy11-alt/ff-ios/$Tag/test_real-$need"
if (-not (Test-Path $bin)) {
    Say "fetching $url"
    try { Invoke-WebRequest $url -OutFile $bin -UseBasicParsing }
    catch { Fail "could not fetch the test binary. Is the '$Tag' branch built?" }
}
$size = (Get-Item $bin).Length
if ($size -lt 100000) { Fail "the fetched binary is $size bytes - that is an error page, not a binary." }
Ok "test_real: $size bytes"

# --- the two game files -----------------------------------------------------
foreach ($f in @($Lib, $Meta)) {
    if (-not (Test-Path $f)) { Fail "missing $f" }
}
foreach ($f in @($Lib, $Meta)) {
    $h = (Get-FileHash $f -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
    Ok ("{0,-30} {1,12:N0} B  sha256 {2}..." -f (Split-Path $f -Leaf), (Get-Item $f).Length, $h)
}

# sofix.py applies the relocations the linker would have. Without it the pointer
# tables read as zeros and every field offset comes back as a plausible wrong
# number - the exact failure that cost two rounds of debugging.
#
# The check is the field-offset pointer itself. Its virtual address is
# 0xa5c7f60 + 0x58 = 0xa5c7fb8, and .data.rel.ro sits 0x8000 below its own
# virtual address in the file, so the value lives at file offset 0xa5bffb8.
# Verified against both files: the sofix.py build reads 0xa8bf168 there, the one
# straight out of the APK reads zero.
$head = [byte[]]::new(4)
$fs = [System.IO.File]::OpenRead($Lib)
try {
    $fs.Read($head, 0, 4) | Out-Null
    if (-not ($head[0] -eq 0x7f -and $head[1] -eq 0x45 -and $head[2] -eq 0x4c -and $head[3] -eq 0x46)) {
        Fail "$Lib is not an ELF file."
    }
    $fs.Seek(0xA5BFFB8, [System.IO.SeekOrigin]::Begin) | Out-Null
    $value = [byte[]]::new(8)
    if ($fs.Read($value, 0, 8) -ne 8) { Fail "could not read the registration slot." }
    $ptr = [BitConverter]::ToUInt64($value, 0)
} finally { $fs.Dispose() }

if ($ptr -eq 0) {
    Fail ("$Lib still has its pointer tables zeroed - run tools/ffdump/sofix.py first. " +
          "This is the libil2cpp.so from the APK, not the fixed one.")
}
if ($ptr -ne 0xA8BF168) {
    Write-Host "  [warn] field-offset pointer is $('{0:x}' -f $ptr), expected a8bf168." -ForegroundColor Yellow
    Write-Host "         A different game version is fine; a value near zero is not." -ForegroundColor Yellow
} else {
    Ok "libil2cpp.so is the sofix.py build (field-offset pointer a8bf168)"
}

# --- push -------------------------------------------------------------------
Say ""
Say "pushing 241 MB - about a minute over USB"
foreach ($pair in @(@($Lib, "/data/local/tmp/libil2cpp.so"), @($Meta, "/data/local/tmp/global-metadata.dat"), @($bin, "/data/local/tmp/test_real"))) {
    $r = & $adb -s $dev.Serial push $pair[0] $pair[1] 2>&1 | Select-Object -Last 1
    Ok "$($pair[1])  $($r -replace '^\s+','')"
}
Sh "chmod 755 /data/local/tmp/test_real" | Out-Null

$free = (Sh "df /data 2>/dev/null | tail -1")
Say "disk on phone: $free"

# --- run --------------------------------------------------------------------
Say ""
Say "=============================================================="
$out = Sh "cd /data/local/tmp && ./test_real ./libil2cpp.so ./global-metadata.dat '$Class' '$FieldFilter'"
$out -split "`n" | ForEach-Object { "  $($_.TrimEnd())" }
Say "=============================================================="

if ($out -match "exec format error") { Fail "the binary is the wrong architecture for this phone." }
if ($out -match "No such file") { Fail "a file is missing on the phone - did the push finish?" }
if ($out -match "(\d+) passed, (\d+) failed") {
    if ($Matches[2] -eq "0") { Ok "decoder verified on $($props['ro.product.model'])" }
    else { Fail "$($Matches[2]) assertion(s) failed - the output above says which." }
} else {
    Fail "the test did not report a result - read the output above."
}
