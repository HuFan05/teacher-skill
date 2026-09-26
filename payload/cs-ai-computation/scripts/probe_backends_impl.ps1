[CmdletBinding()]
param(
    [string]$PythonCommand = 'python',
    [string]$PythonVendorRoot = $env:CS_AI_COMPUTATION_VENDOR,
    [string[]]$ToolCommand = @(),
    [ValidateSet('torch', 'jax', 'tensorflow', 'none')]
    [string[]]$FrameworkDevices = @('torch'),
    [ValidateSet('all', 'python', 'accelerators', 'smt_solvers', 'proof_assistants', 'model_checkers', 'profilers')]
    [string[]]$Only = @('all'),
    [string]$WslDistro = '',
    [string]$WslCommand = 'python3'
)

$ErrorActionPreference = 'Stop'
if ($null -eq $PythonVendorRoot) { $PythonVendorRoot = '' }
$defaultVersionPattern = '(?<![\w.])\d{1,4}(?:\.\d{1,4}){1,4}(?![\w.])'
$allBackends = @('python', 'accelerators', 'smt_solvers', 'proof_assistants', 'model_checkers', 'profilers')
$selected = if ($Only -contains 'all') { $allBackends } else { @($allBackends | Where-Object { $Only -contains $_ }) }

# group -> ordered tool specs: key, candidate commands, version arguments, version pattern
$toolSpecs = [ordered]@{
    smt_solvers = @(
        @{ key = 'z3'; commands = @('z3'); arguments = @('--version'); pattern = $defaultVersionPattern },
        @{ key = 'cvc5'; commands = @('cvc5'); arguments = @('--version'); pattern = $defaultVersionPattern }
    )
    proof_assistants = @(
        @{ key = 'lean'; commands = @('lean'); arguments = @('--version'); pattern = $defaultVersionPattern },
        @{ key = 'coq'; commands = @('coqc', 'rocq'); arguments = @('--version'); pattern = $defaultVersionPattern },
        @{ key = 'isabelle'; commands = @('isabelle'); arguments = @('version'); pattern = 'Isabelle(\d{4}(?:-\d+)?)' }
    )
    model_checkers = @(
        @{ key = 'spin'; commands = @('spin'); arguments = @('-V'); pattern = $defaultVersionPattern },
        @{ key = 'cbmc'; commands = @('cbmc'); arguments = @('--version'); pattern = $defaultVersionPattern }
    )
    profilers = @(
        @{ key = 'hyperfine'; commands = @('hyperfine'); arguments = @('--version'); pattern = $defaultVersionPattern },
        @{ key = 'py_spy'; commands = @('py-spy'); arguments = @('--version'); pattern = $defaultVersionPattern },
        @{ key = 'perf'; commands = @('perf'); arguments = @('--version'); pattern = $defaultVersionPattern },
        @{ key = 'nsys'; commands = @('nsys'); arguments = @('--version'); pattern = $defaultVersionPattern },
        @{ key = 'ncu'; commands = @('ncu'); arguments = @('--version'); pattern = $defaultVersionPattern }
    )
}

$overridable = @('nvidia_smi')
foreach ($group in $toolSpecs.Keys) { foreach ($spec in $toolSpecs[$group]) { $overridable += $spec.key } }
$overrides = @{}
foreach ($entry in @($ToolCommand)) {
    $index = ([string]$entry).IndexOf('=')
    if ($index -lt 1) { throw 'invalid_request' }
    $name = ([string]$entry).Substring(0, $index).Trim().Replace('-', '_')
    $value = ([string]$entry).Substring($index + 1).Trim()
    if (-not $value -or $overridable -notcontains $name -or $overrides.ContainsKey($name)) { throw 'invalid_request' }
    $overrides[$name] = $value
}

function Resolve-CommandPath {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (Test-Path -LiteralPath $Name -PathType Leaf) {
        return (Get-Item -LiteralPath $Name).FullName
    }

    $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }
    return $command.Source
}

function Get-VersionToken {
    param([string]$Text, [string]$Pattern)

    $match = [regex]::Match([string]$Text, $Pattern)
    if (-not $match.Success) { return $null }
    if ($match.Groups.Count -gt 1 -and $match.Groups[1].Success) { return $match.Groups[1].Value }
    return $match.Value
}

function Invoke-VersionProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$Pattern = $defaultVersionPattern
    )

    try {
        $output = & $Executable @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        $text = (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
        return [ordered]@{
            status = if ($exitCode -eq 0) { 'available' } else { 'probe_failed' }
            version = Get-VersionToken -Text $text -Pattern $Pattern
            version_output = $text
            exit_code = $exitCode
            error = ''
        }
    }
    catch {
        return [ordered]@{
            status = 'probe_failed'
            version = $null
            version_output = ''
            exit_code = $null
            error = $_.Exception.Message
        }
    }
}

function Get-HostIdentity {
    $system = if ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([Runtime.InteropServices.OSPlatform]::Windows)) {
        'Windows'
    }
    elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([Runtime.InteropServices.OSPlatform]::OSX)) {
        'Darwin'
    }
    else {
        'Linux'
    }
    $rawArchitecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    $architecture = switch ($rawArchitecture) {
        'x64' { 'x86_64' }
        'amd64' { 'x86_64' }
        'arm64' { 'arm64' }
        'x86' { 'x86' }
        default { $rawArchitecture }
    }
    return [ordered]@{ system = $system; architecture = $architecture; powershell_edition = $PSVersionTable.PSEdition }
}

function Get-HardwareFacts {
    param([Parameter(Mandatory = $true)][string]$System)

    $memory = $null
    $model = $null
    try {
        if ($System -eq 'Windows') {
            $memory = [long](Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory
            $model = $env:PROCESSOR_IDENTIFIER
        }
        elseif ($System -eq 'Darwin') {
            $memory = [long]((& sysctl -n hw.memsize 2>$null) | Select-Object -First 1)
            $model = [string]((& sysctl -n machdep.cpu.brand_string 2>$null) | Select-Object -First 1)
        }
        else {
            $line = Get-Content -LiteralPath '/proc/meminfo' -ErrorAction Stop | Where-Object { $_ -match '^MemTotal:\s+(\d+)\s+kB' } | Select-Object -First 1
            if ($line -match '^MemTotal:\s+(\d+)\s+kB') { $memory = [long]$Matches[1] * 1024 }
            $cpu = Get-Content -LiteralPath '/proc/cpuinfo' -ErrorAction Stop | Where-Object { $_ -match '^(model name|Hardware|cpu model)\s*:' } | Select-Object -First 1
            if ($cpu) { $model = ($cpu -split ':', 2)[1].Trim() }
        }
    }
    catch {
    }
    if ($model -and $model.Length -gt 256) { $model = $model.Substring(0, 256) }
    return [ordered]@{
        logical_cpus = [Environment]::ProcessorCount
        memory_bytes = $memory
        cpu_model = if ($model) { $model } else { $null }
    }
}

function Get-ElanHome {
    if ($env:ELAN_HOME) { return $env:ELAN_HOME }
    return (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.elan')
}

function Resolve-ElanProxy {
    param([Parameter(Mandatory = $true)][string]$Path)

    # Map an elan proxy to its default toolchain binary without executing elan,
    # so a probe can never trigger a toolchain download.
    $elanHome = Get-ElanHome
    $proxy = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    $binDirectory = Join-Path $elanHome 'bin'
    if ($null -eq $proxy -or -not (Test-Path -LiteralPath $binDirectory -PathType Container)) { return $null }
    if ([IO.Path]::GetFullPath($proxy.DirectoryName) -ne [IO.Path]::GetFullPath($binDirectory)) { return $null }
    $default = $null
    $settings = Join-Path $elanHome 'settings.toml'
    if (Test-Path -LiteralPath $settings -PathType Leaf) {
        $match = [regex]::Match((Get-Content -Raw -LiteralPath $settings), '(?m)^\s*default_toolchain\s*=\s*"([^"]+)"')
        if ($match.Success) { $default = $match.Groups[1].Value }
    }
    if ($default) {
        $directory = $default.Replace('/', '--').Replace(':', '---')
        $candidate = Join-Path (Join-Path (Join-Path (Join-Path $elanHome 'toolchains') $directory) 'bin') $proxy.Name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [ordered]@{ path = (Get-Item -LiteralPath $candidate).FullName; manager = 'elan'; toolchain = $default }
        }
    }
    return [ordered]@{ path = $null; manager = 'elan'; toolchain = $default; proxy_path = $proxy.FullName }
}

function Get-ToolRecord {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Spec,
        [string]$Explicit = ''
    )

    $path = $null
    if ($Explicit) {
        $requested = $Explicit
        $source = 'explicit'
        $path = Resolve-CommandPath -Name $Explicit
    }
    else {
        $requested = $Spec.commands[0]
        $source = 'path'
        foreach ($command in $Spec.commands) {
            $path = Resolve-CommandPath -Name $command
            if ($path) { $requested = $command; break }
        }
    }
    $record = [ordered]@{ requested_command = $requested; discovery_source = $source; path = $path }
    if ($path -and $Spec.key -eq 'lean' -and $source -eq 'path') {
        $mapped = Resolve-ElanProxy -Path $path
        if ($null -ne $mapped) {
            $record.manager = $mapped.manager
            $record.toolchain = $mapped.toolchain
            if (-not $mapped.path) {
                $record.path = $mapped.proxy_path
                $record.status = 'probe_failed'
                $record.version = $null
                $record.version_output = ''
                $record.exit_code = $null
                $record.error = 'elan_default_toolchain_not_resolved'
                return $record
            }
            $record.path = $mapped.path
            $record.discovery_source = 'elan_default_toolchain'
            $path = $mapped.path
        }
    }
    if (-not $path) {
        $record.status = 'unavailable'
        $record.version = $null
        $record.version_output = ''
        $record.exit_code = $null
        $record.error = ''
        return $record
    }
    $probe = Invoke-VersionProbe -Executable $path -Arguments $Spec.arguments -Pattern $Spec.pattern
    foreach ($key in $probe.Keys) { $record[$key] = $probe[$key] }
    return $record
}

function Get-NvidiaSmiRecord {
    param([Parameter(Mandatory = $true)][string]$System, [string]$Explicit = '')

    if ($Explicit) {
        $requested = $Explicit; $source = 'explicit'; $path = Resolve-CommandPath -Name $Explicit
    }
    else {
        $requested = 'nvidia-smi'; $source = 'path'; $path = Resolve-CommandPath -Name 'nvidia-smi'
        if (-not $path -and $System -eq 'Windows' -and $env:ProgramFiles) {
            $known = Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe'
            if (Test-Path -LiteralPath $known -PathType Leaf) { $path = (Get-Item -LiteralPath $known).FullName }
        }
    }
    $record = [ordered]@{ requested_command = $requested; discovery_source = $source; path = $path; gpus = @() }
    if (-not $path) {
        $record.status = 'unavailable'; $record.version = $null; $record.version_output = ''; $record.exit_code = $null; $record.error = ''
        return $record
    }
    try {
        $output = & $path '--query-gpu=name,driver_version,memory.total' '--format=csv,noheader,nounits' 2>$null
        $exitCode = $LASTEXITCODE
        $gpus = @()
        foreach ($line in @($output)) {
            $parts = @(([string]$line).Split(',') | ForEach-Object { $_.Trim() })
            if ($parts.Count -ne 3 -or -not $parts[0]) { continue }
            $memory = $null
            $parsed = 0.0
            if ([double]::TryParse($parts[2], [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) { $memory = [long]$parsed }
            $gpus += , ([ordered]@{ name = $parts[0]; driver_version = $parts[1]; memory_total_mib = $memory })
        }
        $driver = if ($gpus.Count -gt 0) { $gpus[0].driver_version } else { $null }
        $record.gpus = $gpus
        $record.status = if ($exitCode -eq 0) { 'available' } else { 'probe_failed' }
        $record.version = $driver
        $record.version_output = if ($driver) { $driver } else { '' }
        $record.exit_code = $exitCode
        $record.error = ''
    }
    catch {
        $record.status = 'probe_failed'; $record.version = $null; $record.version_output = ''; $record.exit_code = $null; $record.error = $_.Exception.Message
    }
    return $record
}

function Invoke-PythonCode {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Code,
        [hashtable]$Environment = @{}
    )

    # The program is sent on standard input, so no argument quoting can alter it.
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key)
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key])
    }
    try {
        $output = $Code | & $Python - $PythonVendorRoot 2>$null
        return [ordered]@{ exit_code = $LASTEXITCODE; lines = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() }) }
    }
    finally {
        foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key]) }
    }
}

$pythonProbeCode = @'
import importlib.metadata, importlib.util, json, pathlib, sys
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".")]
vendor_root = sys.argv[1] if len(sys.argv) > 1 else ""
if vendor_root:
    vendor = pathlib.Path(vendor_root).expanduser().resolve()
    if vendor.is_dir(): sys.path.insert(0, str(vendor))
modules = {"numpy":["numpy"],"scipy":["scipy"],"sympy":["sympy"],"pandas":["pandas"],"sklearn":["scikit-learn"],"statsmodels":["statsmodels"],"hypothesis":["hypothesis"],"torch":["torch"],"jax":["jax"],"jaxlib":["jaxlib"],"tensorflow":["tensorflow","tensorflow-cpu","tensorflow-macos","tf-nightly"],"z3":["z3-solver"],"cvc5":["cvc5"],"mpmath":["mpmath"]}
libraries = {}
for module_name, distributions in modules.items():
    try: available = importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError): available = False
    version = None
    if available:
        for distribution in distributions:
            try:
                version = importlib.metadata.version(distribution)
                break
            except importlib.metadata.PackageNotFoundError:
                continue
    libraries[module_name] = {"available": available, "version": version}
print(json.dumps({"python_version": sys.version.split()[0], "executable": sys.executable, "libraries": libraries}, ensure_ascii=False))
'@

$frameworkPrelude = @'
import json, pathlib, sys
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".")]
vendor_root = sys.argv[1] if len(sys.argv) > 1 else ""
if vendor_root and pathlib.Path(vendor_root).expanduser().is_dir(): sys.path.insert(0, str(pathlib.Path(vendor_root).expanduser().resolve()))
'@

$frameworkProbeCode = @{
    torch = $frameworkPrelude + @'
try:
    import torch
    cuda_available = bool(torch.cuda.is_available())
    info = {"status": "available", "version": getattr(torch, "__version__", None), "cuda_built": getattr(torch.version, "cuda", None), "hip_built": getattr(torch.version, "hip", None), "cuda_available": cuda_available, "device_count": int(torch.cuda.device_count()) if cuda_available else 0}
    try: cudnn = torch.backends.cudnn.version() if cuda_available else None
    except Exception: cudnn = None
    info["cudnn_version"] = None if cudnn is None else str(cudnn)
    mps = getattr(torch.backends, "mps", None)
    info["mps_built"] = bool(mps is not None and mps.is_built())
    info["mps_available"] = bool(mps is not None and mps.is_available())
except Exception as error:
    info = {"status": "probe_failed", "error": f"{type(error).__name__}: {error}"[:500]}
print(json.dumps(info))
'@
    jax = $frameworkPrelude + @'
try:
    import jax
    devices = jax.devices()
    info = {"status": "available", "version": getattr(jax, "__version__", None), "default_backend": jax.default_backend(), "platforms": sorted({device.platform for device in devices}), "device_count": len(devices)}
except Exception as error:
    info = {"status": "probe_failed", "error": f"{type(error).__name__}: {error}"[:500]}
print(json.dumps(info))
'@
    tensorflow = $frameworkPrelude + @'
try:
    import tensorflow as tf
    devices = tf.config.list_physical_devices()
    info = {"status": "available", "version": getattr(tf, "__version__", None), "device_types": sorted({device.device_type for device in devices}), "device_count": len(devices)}
except Exception as error:
    info = {"status": "probe_failed", "error": f"{type(error).__name__}: {error}"[:500]}
print(json.dumps(info))
'@
}

function Get-PythonRecord {
    $pythonPath = Resolve-CommandPath -Name $PythonCommand
    if (-not $pythonPath) {
        return [ordered]@{ status = 'unavailable'; requested_command = $PythonCommand; path = $null; version = ''; libraries = [ordered]@{}; exit_code = $null; error = '' }
    }
    try {
        $run = Invoke-PythonCode -Python $pythonPath -Code $pythonProbeCode
        if ($run.exit_code -ne 0 -or $run.lines.Count -eq 0) {
            return [ordered]@{ status = 'probe_failed'; requested_command = $PythonCommand; path = $pythonPath; version = ''; libraries = [ordered]@{}; exit_code = $run.exit_code; error = 'python_probe_failed' }
        }
        $data = $run.lines[-1] | ConvertFrom-Json -AsHashtable
        return [ordered]@{ status = 'available'; requested_command = $PythonCommand; path = $data.executable; version = $data.python_version; libraries = $data.libraries; exit_code = 0; error = '' }
    }
    catch {
        return [ordered]@{ status = 'probe_failed'; requested_command = $PythonCommand; path = $pythonPath; version = ''; libraries = [ordered]@{}; exit_code = $null; error = $_.Exception.Message }
    }
}

function Get-FrameworkRecord {
    param([string]$Name, $PythonRecord)

    if ($FrameworkDevices -notcontains $Name) { return [ordered]@{ status = 'not_requested' } }
    if ($PythonRecord.status -ne 'available' -or -not $PythonRecord.libraries -or -not $PythonRecord.libraries.ContainsKey($Name) -or $PythonRecord.libraries[$Name].available -ne $true) {
        return [ordered]@{ status = 'unavailable' }
    }
    $environment = @{ PYTORCH_NVML_BASED_CUDA_CHECK = '1'; TF_CPP_MIN_LOG_LEVEL = '3' }
    if ($PythonVendorRoot) {
        $resolved = [IO.Path]::GetFullPath($PythonVendorRoot)
        $environment.PYTHONPATH = if ($env:PYTHONPATH) { $resolved + [IO.Path]::PathSeparator + $env:PYTHONPATH } else { $resolved }
    }
    try {
        $run = Invoke-PythonCode -Python $PythonRecord.path -Code $frameworkProbeCode[$Name] -Environment $environment
        if ($run.exit_code -ne 0 -or $run.lines.Count -eq 0) { return [ordered]@{ status = 'probe_failed'; error = 'framework_probe_failed' } }
        $data = $run.lines[-1] | ConvertFrom-Json -AsHashtable
        if ($data -isnot [Collections.IDictionary] -or -not $data.ContainsKey('status')) { return [ordered]@{ status = 'probe_failed'; error = 'framework_probe_invalid_response' } }
        return $data
    }
    catch {
        return [ordered]@{ status = 'probe_failed'; error = $_.Exception.Message }
    }
}

function Get-WslRecord {
    param([Parameter(Mandatory = $true)][string]$System)

    $record = [ordered]@{ status = 'not_requested'; distro = $WslDistro; requested_command = $WslCommand; version_output = ''; exit_code = $null; error = '' }
    if (-not $WslDistro) { return $record }
    if ($System -ne 'Windows') { $record.status = 'unsupported_platform'; return $record }
    $wslPath = Resolve-CommandPath -Name 'wsl.exe'
    if (-not $wslPath) { $record.status = 'wsl_unavailable'; return $record }
    $probe = Invoke-VersionProbe -Executable $wslPath -Arguments @('-d', $WslDistro, '--', $WslCommand, '--version')
    foreach ($key in $probe.Keys) { $record[$key] = $probe[$key] }
    return $record
}

# Version probes run outside any project so toolchain files there cannot
# redirect a tool manager to a different, possibly uninstalled, toolchain.
Push-Location -LiteralPath ([IO.Path]::GetTempPath())
try {
    $hostIdentity = Get-HostIdentity
    $result = [ordered]@{
        schema_version = '1.0'
        probed_at_utc = [DateTime]::UtcNow.ToString('o')
        host = $hostIdentity
        hardware = Get-HardwareFacts -System $hostIdentity.system
    }
    $pythonRecord = $null
    if ($selected -contains 'python' -or $selected -contains 'accelerators') {
        $pythonRecord = Get-PythonRecord
    }
    if ($selected -contains 'python') {
        $pythonRecord.wsl = Get-WslRecord -System $hostIdentity.system
        $result.python = $pythonRecord
    }
    if ($selected -contains 'accelerators') {
        $frameworks = [ordered]@{}
        foreach ($name in @('torch', 'jax', 'tensorflow')) { $frameworks[$name] = Get-FrameworkRecord -Name $name -PythonRecord $pythonRecord }
        $result.accelerators = [ordered]@{
            nvidia_smi = Get-NvidiaSmiRecord -System $hostIdentity.system -Explicit ([string]$overrides['nvidia_smi'])
            frameworks = $frameworks
            apple_silicon = ($hostIdentity.system -eq 'Darwin' -and $hostIdentity.architecture -eq 'arm64')
        }
    }
    foreach ($group in $toolSpecs.Keys) {
        if ($selected -notcontains $group) { continue }
        $records = [ordered]@{}
        foreach ($spec in $toolSpecs[$group]) {
            $records[$spec.key] = Get-ToolRecord -Spec $spec -Explicit ([string]$overrides[$spec.key])
        }
        $result[$group] = $records
    }
    $result.mcp = [ordered]@{
        status = 'requires_agent_probe'
        evidence = 'Current session discovery and a live check of the selected MCP tool are required.'
    }
}
finally {
    Pop-Location
}

$result | ConvertTo-Json -Depth 10 -Compress
