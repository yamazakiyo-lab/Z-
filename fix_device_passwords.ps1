Connect-MgGraph -Scopes "User.ReadWrite.All" -UseDeviceAuthentication

$upns = @(
    "yorii-sp01@tseg7421.onmicrosoft.com",
    "yorii-sp02@tseg7421.onmicrosoft.com",
    "ayase-sp01@tseg7421.onmicrosoft.com",
    "yorii-tab01@tseg7421.onmicrosoft.com",
    "ayase-tab01@tseg7421.onmicrosoft.com"
)

$params = @{
    PasswordProfile = @{
        Password = "Tseg@2026!"
        ForceChangePasswordNextSignIn = $false
    }
}

foreach ($upn in $upns) {
    try {
        Update-MgUser -UserId $upn -BodyParameter $params
        Write-Host "[OK] $upn"
    } catch {
        Write-Host "[ERR] $upn : $_"
    }
}
