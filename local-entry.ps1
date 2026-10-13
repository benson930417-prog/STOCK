# Dot-source from a Windows launcher after its param block.
function Enter-LocalWorkspace {
    param([string]$SourceRoot, [string]$ScriptRelative, [System.Collections.IDictionary]$BoundParameters, [object[]]$ExtraArguments)
    $canonicalRoot = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
    if ($env:TRADING_WORKSPACE_ACTIVE -and $canonicalRoot -eq [IO.Path]::GetFullPath($env:TRADING_WORKSPACE_ACTIVE).TrimEnd('\')) { return }
    $forwarded = @('run', 'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptRelative)
    foreach ($key in $BoundParameters.Keys) {
        $value = $BoundParameters[$key]
        if ($value -is [System.Management.Automation.SwitchParameter]) {
            if ($value.IsPresent) { $forwarded += "-$key" }
        } else { $forwarded += "-$key"; $forwarded += [string]$value }
    }
    $forwarded += $ExtraArguments
    & python -B (Join-Path $canonicalRoot 'local_workspace.py') @forwarded
    exit $LASTEXITCODE
}
