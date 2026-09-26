function Convert-ComputationView {
    param($Value,$Shape)
    if($Shape -is [System.Collections.IDictionary]) {
        $result=@{}
        if($Value -isnot [System.Collections.IDictionary]) { return $result }
        foreach($key in $Shape.Keys) {
            if($Value.Contains($key)) {$result[$key]=Convert-ComputationView $Value[$key] $Shape[$key]}
        }
        return $result
    }
    if($Shape -eq 'bool') {return ($Value -is [bool] -and $Value)}
    if($Shape -eq 'int') {
        if(($Value -is [int] -or $Value -is [long]) -and $Value -ge -2147483648 -and $Value -le 4294967295){return $Value}
        return $null
    }
    if($Shape -eq 'long') {
        if($Value -is [int] -or $Value -is [long]){return [long]$Value}
        return $null
    }
    if($Shape -eq 'strings') {
        $items=@()
        if($Value -is [array]) {foreach($item in @($Value|Select-Object -First 16)) {$safe=Convert-ComputationView $item 'text';if($null -ne $safe){$items+=,$safe}}}
        return ,$items
    }
    if($Shape -eq 'gpus') {
        $items=@()
        if($Value -is [array]) {foreach($item in @($Value|Select-Object -First 16)) {if($item -is [System.Collections.IDictionary]){$items+=,(Convert-ComputationView $item @{name='text';driver_version='text';memory_total_mib='long'})}}}
        return ,$items
    }
    if($Shape -eq 'observations') {
        $items=@()
        if($Value -is [array]) {foreach($item in @($Value|Select-Object -First 16)) {if($item -is [System.Collections.IDictionary]){$items+=,(Convert-ComputationView $item @{server_name='text';protocol_version='text';server_version='text';runtime_version='text';observed_at_utc='text'})}}}
        return ,$items
    }
    if($Shape -eq 'diagnostic') {if($null -eq $Value -or $Value -ceq ''){return ''}; return 'diagnostic_suppressed'}
    if($Shape -eq 'paths') {
        $items=@()
        if($Value -is [array]) {foreach($item in @($Value|Select-Object -First 8)) {$safe=Convert-ComputationView $item 'path';if($null -ne $safe){$items+=,$safe}}}
        return ,$items
    }
    if($Shape -eq 'backends') {
        return ,@($Value|Where-Object {$_ -is [string] -and $_ -in @('python','accelerators','smt_solvers','proof_assistants','model_checkers','profilers')}|Select-Object -Unique -First 6)
    }
    if($Value -isnot [string]) {return $null}
    $bound=if($Shape -eq 'path'){4096}elseif($Shape -eq 'version'){8192}else{256}
    if($Value.Length -gt $bound){return $null}
    try{[void]([Text.UTF8Encoding]::new($false,$true).GetByteCount($Value))}catch{return $null}
    if($Shape -eq 'version') {
        $match=[regex]::Match($Value,'(?<![\w.])\d{1,4}(?:\.\d{1,4}){1,4}(?![\w.])')
        return $match.Value
    }
    if($Shape -eq 'status' -and $Value -notin @('available','unavailable','probe_failed','not_requested','unsupported_platform','wsl_unavailable','requires_agent_probe','session_probe_required','hit','created','refreshed','write_failed','not_persisted','mcp_recorded')){return 'unknown'}
    return $Value
}

function Invoke-ComputationPublic {
    param([string]$Implementation,[string]$Kind,[object[]]$InputArguments)
    $ErrorActionPreference='Stop'
    $limit=65536; $reason='';$forward=@();$exitCode=0
    try {
        for($i=0;$i -lt $InputArguments.Count;$i++) {
            if($InputArguments[$i] -in @('-MaxResponseBytes','--max-response-bytes')) {
                $i++;$parsed=0
                if($i -ge $InputArguments.Count -or -not [int]::TryParse([string]$InputArguments[$i],[ref]$parsed) -or $parsed -lt 4096 -or $parsed -gt 1048576){throw 'invalid_request'}
                $limit=$parsed
            } elseif($InputArguments[$i] -in @('-ResponseReason','--response-reason')) {
                $i++;if($i -ge $InputArguments.Count){throw 'invalid_request'};$reason=[string]$InputArguments[$i]
            } else {$forward+=,$InputArguments[$i]}
        }
        if($limit -gt 65536 -and -not $reason.Trim()){throw 'invalid_request'}
        # Capture all PowerShell streams. Only the projected single JSON object
        # may leave this boundary; parse/binding/provider diagnostics stay local.
        $parameters=(Get-Command -Name $Implementation -CommandType ExternalScript -ErrorAction Stop).Parameters
        if($forward -contains '--help' -or $forward -contains '-?') {
            return @{Json='{"ok":true,"help":"Use the documented backend parameters. -MaxResponseBytes accepts 4096..1048576; larger than 65536 requires -ResponseReason. Results and errors are bounded JSON."}';ExitCode=0}
        }
        $named=@{}
        for($i=0;$i -lt $forward.Count;$i++) {
            $token=[string]$forward[$i]
            if(-not $token.StartsWith('-')){throw 'invalid_request'}
            $key=$token.TrimStart('-')
            if(-not $parameters.ContainsKey($key) -or $named.ContainsKey($key)){throw 'invalid_request'}
            if($parameters[$key].ParameterType -eq [Management.Automation.SwitchParameter]){$named[$key]=$true;continue}
            $i++;if($i -ge $forward.Count){throw 'invalid_request'}
            if($parameters[$key].ParameterType -eq [string[]]) {
                $values=@($forward[$i])
                while($i+1 -lt $forward.Count -and -not ([string]$forward[$i+1]).StartsWith('-')){$i++;$values+=,$forward[$i]}
                $named[$key]=$values
            } else {$named[$key]=$forward[$i]}
        }
        $captured=@(& $Implementation @named *>&1)
        if(@($captured|Where-Object {$_ -isnot [string]}).Count){throw 'invalid_structured_output'}
        $payload=($captured -join "`n")|ConvertFrom-Json -AsHashtable
        if($payload -isnot [System.Collections.IDictionary]){throw 'invalid_structured_output'}
        $backend=@{status='status';path='path';requested_command='path';version='text';version_output='version';exit_code='int';error='diagnostic';discovery_source='text';distro='text';manager='text';toolchain='text'}
        $library=@{available='bool';version='text';purpose='text';evidence_boundary='text';live_check_requirement='text'}
        $framework=@{status='status';version='text';cuda_built='text';hip_built='text';cudnn_version='text';default_backend='text';cuda_available='bool';mps_built='bool';mps_available='bool';device_count='int';platforms='strings';device_types='strings';error='diagnostic'}
        $python=$backend.Clone()
        $python.libraries=@{}
        foreach($name in @('numpy','scipy','sympy','pandas','sklearn','statsmodels','hypothesis','torch','jax','jaxlib','tensorflow','z3','cvc5','mpmath')){$python.libraries[$name]=$library}
        $python.wsl=$backend
        $smi=$backend.Clone()
        $smi.gpus='gpus'
        $groups=@{smt_solvers=@('z3','cvc5');proof_assistants=@('lean','coq','isabelle');model_checkers=@('spin','cbmc');profilers=@('hyperfine','py_spy','perf','nsys','ncu')}
        $local=@{schema_version='text';probed_at_utc='text';host=@{system='text';architecture='text';powershell_edition='text';python_implementation='text'};hardware=@{logical_cpus='int';memory_bytes='long';cpu_model='text'};python=$python;accelerators=@{nvidia_smi=$smi;frameworks=@{torch=$framework;jax=$framework;tensorflow=$framework};apple_silicon='bool'}}
        foreach($group in $groups.Keys){$members=@{};foreach($name in $groups[$group]){$members[$name]=$backend};$local[$group]=$members}
        if($Kind -eq 'probe'){$local.mcp=@{status='status';evidence='text'}}
        $shape=if($Kind -eq 'probe'){$local}else{@{inventory_schema_version='text';snapshot_updated_at_utc='text';cache=@{status='status';state_file='path';elapsed_ms='int';backend_started='bool';refreshed_backends='backends';invalid_path_backends='backends';write_error='diagnostic'};local=$local;mcp=@{status='status';authority='text';note='text';recorded_mcp_observations='observations'}}}
        $view=Convert-ComputationView $payload $shape
        $sourceLocal=if($Kind -eq 'probe'){$payload}else{$payload.local}
        $viewLocal=if($Kind -eq 'probe'){$view}else{$view.local}
        if($viewLocal.Contains('accelerators') -and $sourceLocal.accelerators.nvidia_smi.gpus -is [array]) {
            $viewLocal.accelerators.nvidia_smi.gpu_count=$sourceLocal.accelerators.nvidia_smi.gpus.Count
            $viewLocal.accelerators.nvidia_smi.gpus_complete=($viewLocal.accelerators.nvidia_smi.gpus.Count -eq $sourceLocal.accelerators.nvidia_smi.gpus.Count)
        }
        $view.ok=$true;$view.response_complete=$true;$view.raw_diagnostics_returned=$false
        $wire=($view|ConvertTo-Json -Depth 20 -Compress)+[Environment]::NewLine
        if([Text.UTF8Encoding]::new($false,$true).GetByteCount($wire) -gt $limit) {
            $view=@{ok=$true;response_complete=$false;response_code='response_limit_exceeded';response_limit_bytes=$limit;limit_scope='this_response_only';business_outcome='success';do_not_retry_automatically=$true;hint='Narrow the selected view or justify a larger return. Inspect existing state before retrying any mutation.'}
            $wire=($view|ConvertTo-Json -Compress)+[Environment]::NewLine
        }
    } catch {
        $exitCode=2
        $wire=(@{ok=$false;response_complete=$false;code='input_or_environment_error';raw_diagnostics_returned=$false;business_outcome='unknown';do_not_retry_automatically=$true}|ConvertTo-Json -Compress)+[Environment]::NewLine
    }
    return @{Json=$wire.TrimEnd([char[]]"`r`n");ExitCode=$exitCode}
}
