$ErrorActionPreference = 'Stop'
Write-Host 'Production synchronization account setup (this Windows user only)'
$syncUser = Read-Host 'External MES user ID'
$securePassword = Read-Host 'External MES password' -AsSecureString
if ([string]::IsNullOrWhiteSpace($syncUser) -or $securePassword.Length -eq 0) {
    throw 'User ID and password are required.'
}
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $syncPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    [Environment]::SetEnvironmentVariable('PRODUCTION_SYNC_USER', $syncUser, 'User')
    [Environment]::SetEnvironmentVariable('PRODUCTION_SYNC_PASSWORD', $syncPassword, 'User')
    $env:PRODUCTION_SYNC_USER = $syncUser
    $env:PRODUCTION_SYNC_PASSWORD = $syncPassword
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    $syncPassword = $null
    $securePassword.Dispose()
}
Write-Host 'Account configured. Stop the MES server, then start it from this PowerShell window.'
Write-Host 'Example: .\run_server.bat'
Write-Host 'Enable synchronization in Production status > External synchronized results.'
