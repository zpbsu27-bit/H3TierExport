$src = $PSScriptRoot
$parentDir = Split-Path -Path $src -Parent

if (-not (Test-Path $src)) {
    Write-Host "[错误] 未找到项目根目录: $src" -ForegroundColor Red
    exit 1
}

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$tempDir = Join-Path $parentDir ("_temp_H3TierExport_backup_" + $ts)
$zipFile = Join-Path $parentDir ("H3TierExport_backup_" + $ts + ".zip")

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "       H3TierExport 项目源码一键精简备份" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] 正在提取项目核心文件 (已排除 build/dist/venv-win/runtime/缓存)..." -ForegroundColor Yellow

# 创建临时工作目录
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

# 排除目录列表 (build/experiments 约 40 GB；venv-win、dist、runtime 均为本机生成物)
$excludeDirs = @(
    'build',
    'dist',
    '.runtime-*',
    'reports',
    'retired-test-files',
    'venv',
    'venv-win',
    '.venv',
    'node_modules',
    '.cache',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.git',
    '.vs',
    '.vscode',
    '.idea',
    'logs',
    'tmp',
    '.tmp',
    'backups',
    '_temp_*',
    '待删除'
)

# 排除文件列表
$excludeFiles = @(
    '*.pyc',
    '*.pyo',
    '*.pyd',
    '*.tmp',
    '*.log',
    '*.bak',
    '*.zip',
    '*.7z',
    '*.rar',
    '*.tar',
    '*.tar.gz',
    '*.whl',
    '*.pdb'
)

# 使用 robocopy 快速高效复制并过滤
robocopy $src $tempDir /E /XD $excludeDirs /XF $excludeFiles /R:1 /W:1 /NP /NFL /NDL | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy 复制失败，退出码: $LASTEXITCODE"
}

Write-Host "[2/3] 正在打包压缩为 ZIP (保存至同级目录: tools/)..." -ForegroundColor Yellow
$items = @(Get-ChildItem -LiteralPath $tempDir -Force)
if ($items.Count -eq 0) {
    throw "备份筛选结果为空，已中止。"
}
Compress-Archive -LiteralPath $items.FullName -DestinationPath $zipFile -Force

Write-Host "[3/3] 正在清理临时工作目录..." -ForegroundColor Yellow
if (Test-Path $tempDir) {
    Remove-Item -Recurse -Force $tempDir
}

$sizeMB = [math]::Round(((Get-Item $zipFile).Length / 1MB), 2)

Write-Host ""
Write-Host "===================================================" -ForegroundColor Green
Write-Host " [完成] H3TierExport 项目备份成功！" -ForegroundColor Green
Write-Host " 备份文件位置 : $zipFile" -ForegroundColor White
Write-Host " 压缩包大小   : $sizeMB MB" -ForegroundColor White
Write-Host "===================================================" -ForegroundColor Green
