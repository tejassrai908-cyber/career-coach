$home_dir = $env:USERPROFILE
$app = Join-Path $home_dir 'career-coach'
$ws  = New-Object -ComObject WScript.Shell

foreach ($dest in @((Join-Path $home_dir 'Desktop\Career Coach.lnk'),
                    (Join-Path $app 'Career Coach.lnk'))) {
    $s = $ws.CreateShortcut($dest)
    $s.TargetPath       = Join-Path $app 'venv\Scripts\pythonw.exe'
    $s.Arguments        = 'launch.py'
    $s.WorkingDirectory = $app
    $s.IconLocation     = 'shell32.dll,13'
    $s.Description      = 'Career Coach - resume vs Naukri job skill gap'
    $s.Save()
}

# verify
foreach ($dest in @((Join-Path $home_dir 'Desktop\Career Coach.lnk'),
                    (Join-Path $app 'Career Coach.lnk'))) {
    $v = $ws.CreateShortcut($dest)
    Write-Output ("FILE   : " + $dest)
    Write-Output ("TARGET : " + $v.TargetPath)
    Write-Output ("ARGS   : " + $v.Arguments)
    Write-Output ("WORKDIR: " + $v.WorkingDirectory)
    Write-Output ("EXISTS : " + (Test-Path $v.TargetPath))
    Write-Output "---"
}
