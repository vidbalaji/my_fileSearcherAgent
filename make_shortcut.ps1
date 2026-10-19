# Creates a "Folder Assistant" shortcut on your desktop that opens the sticky-note chat window.
# Run once from this folder:  powershell -ExecutionPolicy Bypass -File make_shortcut.ps1

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# pythonw.exe runs the window without a console; prefer this project's virtual environment.
$pythonw = Join-Path $here ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($found) { $pythonw = $found.Source } else { $pythonw = $null }
}
if (-not $pythonw) {
    Write-Error "pythonw.exe not found. Create the .venv in this folder (python -m venv .venv) or install Python from python.org."
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Folder Assistant.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + (Join-Path $here "sticky.py") + '"'
$shortcut.WorkingDirectory = $here
$shortcut.Description = "Folder assistant sticky-note chat"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,23"
$shortcut.Save()

Write-Host "Created: $shortcutPath"
Write-Host "Using:   $pythonw"
