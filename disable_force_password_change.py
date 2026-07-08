"""端末アカウントの初回パスワード変更強制を解除する
※ パスワードは変更しない（forceChangePasswordNextSignIn: false のみ）
"""
import os, sys, requests
from dotenv import load_dotenv

load_dotenv()

TENANT_ID  = os.getenv("ENTRA_TENANT_ID")
CLIENT_ID  = os.getenv("ENTRA_CLIENT_ID")
CLIENT_SEC = os.getenv("ENTRA_CLIENT_SECRET")

# トークン取得
r = requests.post(
    f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
    data={"grant_type":"client_credentials","client_id":CLIENT_ID,
          "client_secret":CLIENT_SEC,"scope":"https://graph.microsoft.com/.default"},
    timeout=15)
r.raise_for_status()
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

device_upns = [
    "yorii-sp01@tseg7421.onmicrosoft.com",
    "yorii-sp02@tseg7421.onmicrosoft.com",
    "ayase-sp01@tseg7421.onmicrosoft.com",
    "yorii-tab01@tseg7421.onmicrosoft.com",
    "ayase-tab01@tseg7421.onmicrosoft.com",
]

# まず現在の状態を確認
print("=== 現在の forceChangePasswordNextSignIn 状態 ===")
for upn in device_upns:
    r = requests.get(
        f"https://graph.microsoft.com/v1.0/users/{upn}?$select=userPrincipalName,passwordProfile",
        headers=headers, timeout=15)
    if r.ok:
        pp = r.json().get("passwordProfile", {})
        force = pp.get("forceChangePasswordNextSignIn", "N/A")
        print(f"  {upn}: forceChange={force}")
    else:
        print(f"  [GET ERR] {upn}: {r.text}")

print()
print("=== forceChangePasswordNextSignIn を false に設定 ===")

# パスワードフィールドなし・forceChangeのみ更新
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
        print(f"        code   : {err.get('code')}")
        print(f"        message: {err.get('message')}")
