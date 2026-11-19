# Pin a shortcut to the Windows taskbar via the shell "Pin to taskbar" verb.
# Windows 10/11 restricts programmatic pinning, so this may silently do
# nothing on newer builds. If it does not stick, the fallback is one
# right-click on the Aries desktop icon -> "Pin to taskbar".
param(
    [Parameter(Mandatory = $true)]
    [string]$Shortcut
)

try {
    $shell = New-Object -ComObject Shell.Application
    $folderPath = Split-Path $Shortcut
    $fileName = Split-Path $Shortcut -Leaf
    $folder = $shell.Namespace($folderPath)
    if ($null -eq $folder) { exit 0 }
    $item = $folder.ParseName($fileName)
    if ($null -eq $item) { exit 0 }
    $item.InvokeVerb('taskbarpin')
} catch {
    # Pinning is best-effort; never fail the install over it.
    exit 0
}
exit 0
