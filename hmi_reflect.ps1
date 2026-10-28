# hmi_reflect.ps1 - list the public API of the Aurora Vision Studio HMI controls (read-only .NET reflection over the
# installed HMI.Runtime.dll / HMI.Controls.dll; no code is decompiled) into <cache>\hmi_controls.json, which
# avs_hmi_controls shows next to what the examples use.
#
#   powershell -ExecutionPolicy Bypass -File hmi_reflect.ps1 [-StudioDir <dir>] [-Out <json>]
param(
    [string]$StudioDir = "",
    [string]$Out = ""
)
if (-not $StudioDir) {
    $StudioDir = (Get-ChildItem "C:\Program Files\Aurora Vision" -Directory -Filter "Aurora Vision Studio * Professional" |
                  Sort-Object Name | Select-Object -Last 1).FullName
}
if (-not $Out) {
    $cache = if ($env:AVS_DOCS_CACHE) { $env:AVS_DOCS_CACHE } else { Join-Path (Split-Path $PSScriptRoot) "avs_docs_cache" }
    $Out = Join-Path $cache "hmi_controls.json"
}
Add-Type -AssemblyName System.Windows.Forms
$wf = [System.Windows.Forms.Control].Assembly
[System.AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($s, $e)
    $p = Join-Path $StudioDir (($e.Name -split ',')[0] + '.dll')
    if (Test-Path $p) { [System.Reflection.Assembly]::LoadFrom($p) } else { $null }
})

function Attr($member, $name) {
    $member.GetCustomAttributes($true) | Where-Object { $_.GetType().Name -eq $name } | Select-Object -First 1
}

$result = [ordered]@{ studio = $StudioDir; controls = [ordered]@{} }
foreach ($file in 'HMI.Runtime.dll', 'HMI.Controls.dll', 'HMI.dll') {
    $asm = [System.Reflection.Assembly]::LoadFrom((Join-Path $StudioDir $file))
    # internal types too: ProgramControlBox is not public but is used in .avhmi files
    try { $types = $asm.GetTypes() } catch { $types = $_.Exception.InnerException.Types | Where-Object { $_ } }
    foreach ($t in $types | Where-Object { $_ -and -not $_.IsNested -and $_.IsSubclassOf([System.Windows.Forms.Control]) -and -not $_.IsAbstract }) {
        $desc = Attr $t 'DescriptionAttribute'
        $props = @()
        foreach ($p in $t.GetProperties() | Sort-Object Name) {
            if (-not $p.CanRead -or $p.GetIndexParameters().Count) { continue }
            $b = Attr $p 'BrowsableAttribute'
            if ($b -and -not $b.Browsable) { continue }
            # AVS describes its own properties with LocalizedDescription / HMITranslatedDescription attributes
            $d = $p.GetCustomAttributes($true) | Where-Object { $_.GetType().Name -match 'Description' -and $_.Description } | Select-Object -First 1
            $c = Attr $p 'CategoryAttribute'; $v = Attr $p 'DefaultValueAttribute'
            $attrs = @($p.GetCustomAttributes($true) | ForEach-Object { $_.GetType().Name -replace 'Attribute$', '' })
            $props += [ordered]@{
                name = $p.Name; type = $p.PropertyType.Name; own = ($p.DeclaringType.Assembly -ne $wf); writable = $p.CanWrite
                hmi = ($attrs -contains 'HMIBrowsable'); port = ($attrs -contains 'HMIPortProperty')
                category = if ($c) { $c.Category } else { '' }
                default = if ($v -and $null -ne $v.Value) { "$($v.Value)" } else { '' }
                description = if ($d) { $d.Description } else { '' }
                values = if ($p.PropertyType.IsEnum) { [System.Enum]::GetNames($p.PropertyType) -join ', ' } else { '' }
                attributes = (($attrs | Where-Object { $_ -notin 'Description', 'Category', 'DefaultValue', 'Browsable' }) -join ',')
            }
        }
        $result.controls[$t.Name] = [ordered]@{
            assembly = $file; full = $t.FullName; base = $t.BaseType.Name; public = $t.IsPublic
            description = if ($desc) { $desc.Description } else { '' }; properties = $props
        }
    }
}
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$result | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 $Out
"{0} controls -> {1}" -f $result.controls.Count, $Out
