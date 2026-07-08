"""
管理者アカウントで委任認証し forceChangePasswordNextSignIn を解除する。
デバイスコードフローで管理者がブラウザでログインする。
"""
import msal, requests

TENANT_ID = "062bcf13-99bd-40f8-b070-eb3f60caecc1"
CLIENT_ID = "294c1043-4f5a-44a3-b459-065f1d232e59"
SCOPES    = ["User.ReadWrite.All"]

device_upns = [
    "yorii-sp01@tseg7421.onmicrosoft.com",
    "yorii-sp02@tseg7421.onmicrosoft.com",
    "ayase-sp01@tseg7421.onmicrosoft.com",
    "yorii-tab01@tseg7421.onmicrosoft.com",
    "ayase-tab01@tseg7421.onmicrosoft.com",
]

app = msal.PublicClientApplication(
    CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}"
)

# デバイスコードフロー
flow = app.initiate_device_flow(scopes=SCOPES)
print(flow["message"])  # ブラウザで表示されたコードを入力してください
input("ログイン完了後 Enter を押してください...")

result = app.acquire_token_by_device_flow(flow)
if "access_token" not in result:
    print(f"[ERR] 認証失敗: {result.get('error_description')}")
    exit(1)

token = result["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
print("認証OK\n")

for upn in device_upns:
    r = requests.patch(
        f"https://graph.microsoft.com/v1.0/users/{upn}",
        headers=headers,
        json={"passwordProfile": {"forceChangePasswordNextSignIn": False}},
        timeout=15)
    if r.ok:
        print(f"  [OK]  {upn}")
    else:
        err = r.json().get("error", {})
        print(f"  [ERR] {upn}")
        print(f"        {err.get('code')}: {err.get('message')}")
