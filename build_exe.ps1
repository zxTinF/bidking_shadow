$ErrorActionPreference = "Stop"

uv run pyinstaller --clean --noconfirm .\BidKingGrid.spec

Write-Host ""
Write-Host "Build complete: dist\艾莎鉴影.exe"
