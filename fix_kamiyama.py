import os, requests
from dotenv import load_dotenv
load_dotenv()

TENANT_ID  = os.getenv("ENTRA_TENANT_ID")
CLIENT_ID  = os.getenv("ENTRA_CLIENT_ID")
CLIENT_SEC = os.getenv("ENTRA_CLIENT_SECRET")

r = requests.post(
    f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
    data={"grant_type":"client_credentials","client_id":CLIENT_ID,
          "client_secret":CLIENT_SEC,"scope":"https://graph.microsoft.com/.default"})
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

r = requests.patch(
    "https://graph.microsoft.com/v1.0/users/wo47779@tseg7421.onmicrosoft.com",
    headers=headers,
    json={"displayName": "神山 潤", "givenName": "潤", "surname": "神山"})
print("[OK] wo47779 → 神山潤 に変更" if r.ok else f"[ERR] {r.json().get('error',{}).get('message')}")
