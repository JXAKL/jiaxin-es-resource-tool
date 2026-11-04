param(
    [Parameter(Mandatory)][string]$SourceFolder,
    [Parameter(Mandatory)][string]$InnoCompiler,
    [string]$SevenZip='C:\Program Files\7-Zip\7z.exe',
    [string]$OutputDirectory=(Join-Path $PSScriptRoot '..\dist')
)
$ErrorActionPreference='Stop'
$source=(Resolve-Path -LiteralPath $SourceFolder).Path
$manifest=Get-Content -LiteralPath (Join-Path $source 'build-info.json') -Raw | ConvertFrom-Json
$version=$manifest.version
if($version -notmatch '^\d+\.\d+\.\d+$' -or $manifest.user_data_included -ne $false){throw 'Invalid clean release manifest'}
if(!(Test-Path -LiteralPath (Join-Path $source '夹心的ES资源工具.exe'))){throw 'Application executable missing'}
$forbidden=@('.state','reports','session.json','cookies.txt','cookies.json','settings.json','ui-settings.json','folder-group-settings.json','local-settings.json','collector-settings.json','search-scraper-settings.json','search-scrapes','completion-settings.json','resource-organize-settings.json','resource-organize-scores.sqlite3','completion-categories.json','completion-collections','collections','catalog.sqlite3','.links','download-queue.json','download-jobs','download-profiles','download-browser','pane-layout.json')
foreach($path in Get-ChildItem -LiteralPath $source -Recurse -Force){
    if($path.Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Filesystem link in package: $($path.Name)"}
    if($path.Name -in $forbidden){throw "Private state in package: $($path.Name)"}
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$output=(Resolve-Path -LiteralPath $OutputDirectory).Path
& $InnoCompiler ('/DSourceFolder='+$source) ('/DReleaseVersion='+$version) ('/DReleaseOutput='+$output) (Join-Path $PSScriptRoot 'ESResourceTool.iss')
if($LASTEXITCODE -ne 0){throw 'Installer compilation failed'}
# A fresh archive cannot retain stale files from previous builds.
$temporary=Join-Path $output ('portable-'+[guid]::NewGuid().ToString('N')+'.zip')
& $SevenZip a -tzip -mx=5 -mcu=on $temporary $source
if($LASTEXITCODE -ne 0){throw 'ZIP creation failed'}
& $SevenZip t $temporary
if($LASTEXITCODE -ne 0){throw 'ZIP integrity check failed'}
$archive=Join-Path $output "Jiaxin-ES-Resource-Tool-$version-Portable.zip"
Move-Item -LiteralPath $temporary -Destination $archive -Force
$hashLines=foreach($name in "Jiaxin-ES-Resource-Tool-$version-Setup.exe","Jiaxin-ES-Resource-Tool-$version-Portable.zip"){
    '{0}  {1}' -f (Get-FileHash -LiteralPath (Join-Path $output $name) -Algorithm SHA256).Hash,$name
}
$hashLines | Set-Content -LiteralPath (Join-Path $output 'SHA256.txt') -Encoding utf8NoBOM
