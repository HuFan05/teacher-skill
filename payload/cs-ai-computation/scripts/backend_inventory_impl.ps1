[CmdletBinding()]
param(
    [ValidateSet('ReadOrCreate', 'Refresh', 'Invalidate', 'RecordMcp')]
    [string]$Mode = 'ReadOrCreate',
    [string]$StateFile = '',
    [ValidateSet('all', 'python', 'accelerators', 'smt_solvers', 'proof_assistants', 'model_checkers', 'profilers')]
    [string[]]$Backend = @('all'),
    [string]$ReasonCode = '',
    [int]$MaxAgeHours = 168,
    [string]$ProbeScript = (Join-Path $PSScriptRoot 'probe_backends_impl.ps1'),
    [string]$ProbeJsonFile = '',
    [string]$PythonCommand = 'python',
    [string]$PythonVendorRoot = $env:CS_AI_COMPUTATION_VENDOR,
    [string[]]$ToolCommand = @(),
    [ValidateSet('torch', 'jax', 'tensorflow', 'none')]
    [string[]]$FrameworkDevices = @('torch'),
    [string]$WslDistro = '',
    [string]$WslCommand = 'python3',
    [string]$McpServerName = '',
    [string]$McpProtocolVersion = '',
    [string]$McpServerVersion = '',
    [string]$McpRuntimeVersion = '',
    [string]$McpObservedAtUtc = '',
    [switch]$NoWrite
)

$ErrorActionPreference = 'Stop'
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$allBackends = @('python', 'accelerators', 'smt_solvers', 'proof_assistants', 'model_checkers', 'profilers')
$toolGroups = [ordered]@{
    smt_solvers = @('z3', 'cvc5')
    proof_assistants = @('lean', 'coq', 'isabelle')
    model_checkers = @('spin', 'cbmc')
    profilers = @('hyperfine', 'py_spy', 'perf', 'nsys', 'ncu')
}
if ($null -eq $PythonVendorRoot) { $PythonVendorRoot = '' }
if ($Mode -eq 'Invalidate' -and $ReasonCode -notmatch '^[a-z][a-z0-9_]{0,31}$') {
    throw "Invalidate mode requires a bounded lowercase reason code."
}
if ($Mode -eq 'RecordMcp' -and $NoWrite) {
    throw "RecordMcp persists an observation and cannot run with -NoWrite."
}

function Get-DefaultStateFile {
    if ($env:CS_AI_BACKEND_INVENTORY) {
        return $env:CS_AI_BACKEND_INVENTORY
    }
    return (Join-Path (Join-Path (Join-Path ([IO.Path]::GetTempPath()) 'Teacher') 'cs-ai-computation') 'backend-inventory.json')
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

function Read-Inventory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        $inventory = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -AsHashtable
        if ($inventory.inventory_schema_version -ne '1.0' -or $inventory.local.schema_version -ne '1.0') {
            return $null
        }
        foreach ($name in $allBackends) {
            if ($inventory.local[$name] -isnot [Collections.IDictionary]) { return $null }
        }
        return $inventory
    }
    catch {
        return $null
    }
}

function Select-Backends {
    param([string[]]$Names)

    if ($Names -contains 'all') { return $allBackends }
    return @($allBackends | Where-Object { $Names -contains $_ })
}

function Invoke-LocalProbe {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    if ($ProbeJsonFile) {
        $data = Get-Content -Raw -LiteralPath $ProbeJsonFile | ConvertFrom-Json -AsHashtable
    }
    else {
        if (-not (Test-Path -LiteralPath $ProbeScript -PathType Leaf)) {
            throw "Backend probe script is unavailable."
        }
        $probeArguments = @{
            PythonCommand = $PythonCommand
            PythonVendorRoot = $PythonVendorRoot
            FrameworkDevices = $FrameworkDevices
            Only = (Select-Backends -Names $Names)
            WslDistro = $WslDistro
            WslCommand = $WslCommand
        }
        if ($ToolCommand.Count -gt 0) { $probeArguments.ToolCommand = $ToolCommand }
        $raw = & $ProbeScript @probeArguments
        $data = (($raw | ForEach-Object { $_.ToString() }) -join "`n") | ConvertFrom-Json -AsHashtable
    }
    if ($data -isnot [Collections.IDictionary] -or $data.schema_version -ne '1.0') {
        throw 'Unsupported backend probe schema.'
    }
    foreach ($transportKey in @('ok', 'response_complete', 'mcp', 'raw_diagnostics_returned')) {
        if ($data.ContainsKey($transportKey)) { [void]$data.Remove($transportKey) }
    }
    return $data
}

function New-Inventory {
    param([Parameter(Mandatory = $true)][Collections.IDictionary]$Local)

    $now = [DateTime]::UtcNow.ToString('o')
    return [ordered]@{
        inventory_schema_version = '1.0'
        created_at_utc = $now
        updated_at_utc = $now
        local = $Local
        mcp = [ordered]@{
            authority = 'current_session_tool_discovery_and_call'
            persisted_status = 'historical_only'
            required_action = 'Build a current-session overlay and live-check only the selected MCP backend.'
        }
        invalidations = @()
    }
}

function New-McpObservation {
    $required = [ordered]@{
        server_name = $McpServerName
        protocol_version = $McpProtocolVersion
        server_version = $McpServerVersion
        runtime_version = $McpRuntimeVersion
    }
    $missing = @($required.GetEnumerator() | Where-Object { -not $_.Value.Trim() } | ForEach-Object Key)
    if ($missing.Count -gt 0) {
        throw ('RecordMcp requires: ' + ($missing -join ', '))
    }
    if ($McpProtocolVersion -notmatch '^\d{4}-\d{2}-\d{2}$') {
        throw 'MCP protocol version must use the negotiated YYYY-MM-DD form.'
    }
    if ($McpServerName.Length -gt 128) {
        throw 'MCP server name is too long.'
    }
    $observedAt = if ($McpObservedAtUtc) { $McpObservedAtUtc } else { [DateTime]::UtcNow.ToString('o') }
    try { [void][DateTimeOffset]::Parse($observedAt) }
    catch { throw 'MCP observation time must be an ISO-8601 timestamp.' }
    return [ordered]@{
        server_name = $McpServerName
        protocol_version = $McpProtocolVersion
        server_version = $McpServerVersion
        runtime_version = $McpRuntimeVersion
        observed_at_utc = $observedAt
        evidence = 'initialize_handshake_and_execution_call'
    }
}

function Add-McpObservation {
    param(
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Inventory,
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Observation
    )

    $observations = $Inventory.mcp.observations
    if ($observations -isnot [Collections.IDictionary]) { $observations = [ordered]@{} }
    $observations[$Observation.server_name] = $Observation
    if ($observations.Count -gt 16) {
        $kept = [ordered]@{}
        foreach ($item in @($observations.Values | Sort-Object { [string]$_.observed_at_utc } | Select-Object -Last 16)) {
            $kept[$item.server_name] = $item
        }
        $observations = $kept
    }
    $Inventory.mcp.observations = $observations
}

function Write-InventoryAtomic {
    param(
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Inventory,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $parent = Split-Path -Parent $Path
    if (-not $parent) {
        $parent = (Get-Location).Path
        $Path = Join-Path $parent $Path
    }
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $temporary = Join-Path $parent ('.backend-inventory-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $json = $Inventory | ConvertTo-Json -Depth 12
        [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($temporary, $Path, $true)
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-MissingBackendPaths {
    param([Parameter(Mandatory = $true)][Collections.IDictionary]$Inventory)

    $missing = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $checks = @(
        @('python', $Inventory.local.python.path),
        @('accelerators', $Inventory.local.accelerators.nvidia_smi.path)
    )
    foreach ($group in $toolGroups.Keys) {
        foreach ($name in $toolGroups[$group]) {
            $record = $Inventory.local[$group][$name]
            if ($record -is [Collections.IDictionary]) { $checks += , @($group, $record.path) }
        }
    }
    foreach ($check in $checks) {
        if ($check[1] -and -not (Test-Path -LiteralPath $check[1] -PathType Leaf)) {
            [void]$missing.Add([string]$check[0])
        }
    }
    return @($allBackends | Where-Object { $missing.Contains($_) })
}

function Test-InventoryExpired {
    param([Parameter(Mandatory = $true)][Collections.IDictionary]$Inventory)

    if ($MaxAgeHours -le 0) {
        return $false
    }
    try {
        $updated = [DateTimeOffset]::Parse([string]$Inventory.updated_at_utc)
        return ([DateTimeOffset]::UtcNow - $updated).TotalHours -ge $MaxAgeHours
    }
    catch {
        return $true
    }
}

function Test-InventoryHostMismatch {
    param([Parameter(Mandatory = $true)][Collections.IDictionary]$Inventory)

    $current = Get-HostIdentity
    $stored = $Inventory.local.host
    if (-not $stored) {
        return $true
    }
    return ($stored.system -ne $current.system -or $stored.architecture -ne $current.architecture)
}

function Merge-Backends {
    param(
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Inventory,
        [Parameter(Mandatory = $true)][Collections.IDictionary]$FreshLocal,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    foreach ($name in (Select-Backends -Names $Names)) {
        if ($FreshLocal.Contains($name)) {
            $Inventory.local[$name] = $FreshLocal[$name]
        }
    }
    $Inventory.local.probed_at_utc = $FreshLocal.probed_at_utc
    $Inventory.local.host = $FreshLocal.host
    $Inventory.local.hardware = $FreshLocal.hardware
    $Inventory.updated_at_utc = [DateTime]::UtcNow.ToString('o')
    return $Inventory
}

function Add-PythonLibraryGuidance {
    param([Parameter(Mandatory = $true)][Collections.IDictionary]$Local)

    if (-not $Local.Contains('python')) {
        $Local.python = [ordered]@{}
    }
    if (-not $Local.python.Contains('libraries') -or $Local.python.libraries -isnot [Collections.IDictionary]) {
        $Local.python.libraries = [ordered]@{}
    }
    $frameworkBoundary = 'a_completed_run_or_metric_is_numerical_evidence_or_bounded_empirical_not_a_correctness_proof'
    $guidance = [ordered]@{
        torch = [ordered]@{
            purpose = 'tensor_computation_autodiff_training_and_evaluation_on_cpu_cuda_rocm_or_mps'
            evidence_boundary = $frameworkBoundary
            live_check_requirement = 'use_run_python_capability_torch_cpu_torch_cuda_or_torch_mps_before_material_runs'
        }
        jax = [ordered]@{
            purpose = 'tensor_computation_autodiff_and_jit_compiled_training_on_cpu_gpu_or_tpu'
            evidence_boundary = $frameworkBoundary
            live_check_requirement = 'use_run_python_capability_jax_default_before_material_runs'
        }
        tensorflow = [ordered]@{
            purpose = 'tensor_computation_autodiff_training_and_evaluation'
            evidence_boundary = $frameworkBoundary
            live_check_requirement = 'use_run_python_capability_tensorflow_default_before_material_runs'
        }
        scipy = [ordered]@{
            purpose = 'numerical_optimization_linear_algebra_and_statistics'
            evidence_boundary = 'numerical_evidence_by_default_not_a_certificate'
            live_check_requirement = 'use_run_python_capability_scipy_stats_or_smoke_test_the_selected_operation_before_material_use'
        }
        z3 = [ordered]@{
            purpose = 'smt_solving_models_and_unsat_answers_through_the_python_binding'
            evidence_boundary = 'a_sat_model_is_checkable_by_substitution_an_unsat_answer_is_trusted_solver_output_unless_a_proof_is_checked'
            live_check_requirement = 'use_run_python_capability_z3_smt_before_material_queries'
        }
        hypothesis = [ordered]@{
            purpose = 'property_based_testing_with_counterexample_shrinking'
            evidence_boundary = 'passing_properties_verify_only_the_generated_inputs_not_correctness'
            live_check_requirement = 'use_run_python_capability_hypothesis_pbt_before_material_test_runs'
        }
    }
    foreach ($name in $guidance.Keys) {
        if (-not $Local.python.libraries.Contains($name)) {
            $Local.python.libraries[$name] = [ordered]@{ available = $false; version = $null }
        }
        foreach ($field in $guidance[$name].Keys) {
            $Local.python.libraries[$name][$field] = $guidance[$name][$field]
        }
    }
}

function Write-Result {
    param(
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Inventory,
        [Parameter(Mandatory = $true)][string]$CacheStatus,
        [string[]]$RefreshedBackends = @(),
        [string[]]$InvalidPaths = @(),
        [bool]$BackendStarted = $false,
        [string]$WriteError = ''
    )

    Add-PythonLibraryGuidance -Local $Inventory.local
    $stopwatch.Stop()
    $observations = @()
    if ($Inventory.mcp.observations -is [Collections.IDictionary]) {
        $observations = @($Inventory.mcp.observations.Values | Select-Object -First 16)
    }
    $output = [ordered]@{
        inventory_schema_version = $Inventory.inventory_schema_version
        snapshot_updated_at_utc = $Inventory.updated_at_utc
        cache = [ordered]@{
            status = $CacheStatus
            state_file = $StateFile
            elapsed_ms = $stopwatch.ElapsedMilliseconds
            backend_started = $BackendStarted
            refreshed_backends = @($RefreshedBackends)
            invalid_path_backends = @($InvalidPaths)
            write_error = $WriteError
        }
        local = $Inventory.local
        mcp = [ordered]@{
            status = 'session_probe_required'
            authority = 'current_session_tool_discovery_and_call'
            note = 'The persisted snapshot is not evidence that an MCP tool is callable in this session.'
            recorded_mcp_observations = $observations
        }
    }
    $output | ConvertTo-Json -Depth 12 -Compress
}

if (-not $StateFile) {
    $StateFile = Get-DefaultStateFile
}
$StateFile = [IO.Path]::GetFullPath($StateFile)
$inventory = Read-Inventory -Path $StateFile
$missingBackends = @()

if ($Mode -eq 'RecordMcp') {
    $observation = New-McpObservation
    $backendStarted = $false
    if (-not $inventory) {
        $inventory = New-Inventory -Local (Invoke-LocalProbe -Names @('all'))
        $backendStarted = $true
    }
    Add-McpObservation -Inventory $inventory -Observation $observation
    $inventory.updated_at_utc = [DateTime]::UtcNow.ToString('o')
    Write-InventoryAtomic -Inventory $inventory -Path $StateFile
    Write-Result -Inventory $inventory -CacheStatus 'mcp_recorded' -BackendStarted $backendStarted
    return
}

if ($Mode -eq 'ReadOrCreate' -and $inventory) {
    $missingBackends = @(Get-MissingBackendPaths -Inventory $inventory)
    if ($missingBackends.Count -eq 0 -and -not (Test-InventoryExpired -Inventory $inventory) -and -not (Test-InventoryHostMismatch -Inventory $inventory)) {
        Write-Result -Inventory $inventory -CacheStatus 'hit' -BackendStarted $false
        return
    }
}

$refreshed = $allBackends
if ($inventory -and -not (Test-InventoryHostMismatch -Inventory $inventory)) {
    if ($Mode -in @('Refresh', 'Invalidate')) {
        $refreshed = @(Select-Backends -Names $Backend)
    }
    elseif ($missingBackends.Count -gt 0 -and -not (Test-InventoryExpired -Inventory $inventory)) {
        $refreshed = $missingBackends
    }
}
$freshLocal = Invoke-LocalProbe -Names $refreshed

$cacheStatus = 'created'
if (-not $inventory) {
    foreach ($name in $allBackends) {
        if (-not $freshLocal.Contains($name)) { throw 'A new inventory requires a probe of every backend.' }
    }
    $inventory = New-Inventory -Local $freshLocal
    $refreshed = $allBackends
}
else {
    $cacheStatus = 'refreshed'
    if ($Mode -eq 'Invalidate') {
        $inventory.invalidations = @($inventory.invalidations) + @($refreshed | ForEach-Object {
            [ordered]@{ backend = $_; reason = $ReasonCode; recorded_at_utc = [DateTime]::UtcNow.ToString('o') }
        })
        if ($inventory.invalidations.Count -gt 20) {
            $inventory.invalidations = @($inventory.invalidations | Select-Object -Last 20)
        }
    }
    $inventory = Merge-Backends -Inventory $inventory -FreshLocal $freshLocal -Names $refreshed
}

$writeError = ''
if ($NoWrite) {
    $cacheStatus = 'not_persisted'
}
else {
    try {
        Write-InventoryAtomic -Inventory $inventory -Path $StateFile
    }
    catch {
        $writeError = 'cache_write_failed'
        $cacheStatus = 'write_failed'
    }
}
Write-Result -Inventory $inventory -CacheStatus $cacheStatus -RefreshedBackends $refreshed -InvalidPaths $missingBackends -BackendStarted $true -WriteError $writeError
