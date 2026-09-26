# Public bounded entry; implementation is an internal dependency.
. (Join-Path $PSScriptRoot 'computation_output.ps1')
$result=Invoke-ComputationPublic -Implementation (Join-Path $PSScriptRoot 'backend_inventory_impl.ps1') -Kind inventory -InputArguments $args
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Write-Output $result.Json
exit $result.ExitCode
