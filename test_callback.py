import hmac, hashlib, base64, json
from urllib.request import urlopen, Request

body = json.dumps({
    'type': 'message',
    'source': {'userId': 'test', 'channelId': 'test'},
    'content': {'type': 'text', 'text': 'hello'}
}).encode()

secret = 'C0iuQeI89rH4oQaxFRsqYug8wn0Xoo'
sig = base64.b64encode(
    hmac.new(secret.encode(), body, hashlib.sha256).digest()
).decode()

req = Request(
    'https://tseg-lw-receiver-bqanh0c7aufgffdt.japanwest-01.azurewebsites.net/lineworks/callback',
    data=body,
    headers={'Content-Type': 'application/json', 'X-WORKS-Signature': sig},
    method='POST'
)
print(urlopen(req, timeout=60).read())
