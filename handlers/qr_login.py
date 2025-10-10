"""QR code login functionality for Discord."""

import json
import base64
import hashlib
import io
from typing import Optional, Dict
import asyncio
import pyqrcode
import websockets
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes


class DiscordQRLogin:
    """
    Handles the modern Discord WebSocket-based Remote Authentication flow (v2).
    """

    def __init__(self):
        self.GATEWAY_URL = "wss://remote-auth-gateway.discord.gg/?v=2"
        self.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.public_key_b64 = base64.b64encode(
            self.private_key.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode("utf-8")
        self.ws = None

    async def generate_qr_code(self) -> Optional[Dict]:
        """Performs the handshake and generates a QR code."""
        try:
            self.ws = await websockets.connect(
                self.GATEWAY_URL,
                origin="https://discord.com",
                user_agent_header=self.USER_AGENT,
            )

            await self.ws.send(
                json.dumps({"op": "init", "encoded_public_key": self.public_key_b64})
            )

            response = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=15))
            if response.get("op") != "nonce_proof":
                print("[QR Login] Handshake Error: Did not receive nonce proof.")
                await self.ws.close()
                return None

            nonce = base64.b64decode(response["encrypted_nonce"])
            decrypted_nonce = self.private_key.decrypt(
                nonce,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )

            m = hashlib.sha256()
            m.update(decrypted_nonce)
            proof = base64.urlsafe_b64encode(m.digest()).decode("utf-8").rstrip("=")

            await self.ws.send(json.dumps({"op": "nonce_proof", "proof": proof}))

            response = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=15))
            if response.get("op") != "pending_remote_init":
                print("[QR Login] Handshake Error: Did not receive fingerprint.")
                await self.ws.close()
                return None

            fingerprint = response["fingerprint"]
            qr_url = f"https://discord.com/ra/{fingerprint}"

            qr = pyqrcode.create(qr_url)
            buffer = io.BytesIO()
            qr.png(buffer, scale=5)
            buffer.seek(0)
            return {"image": buffer}

        except Exception as e:
            print(f"[QR Login] Handshake failed: {e}")
            if self.ws and not self.ws.closed:
                await self.ws.close()
            return None

    async def wait_for_login(self) -> Optional[Dict]:
        """Waits on the established WebSocket for the final token."""
        if not self.ws:
            return None

        try:
            while True:
                message = await asyncio.wait_for(self.ws.recv(), timeout=120)
                data = json.loads(message)
                op = data.get("op")

                if op == "pending_finish":
                    continue

                if op == "finish":
                    encrypted_token = base64.b64decode(data["encrypted_token"])
                    token = self.private_key.decrypt(
                        encrypted_token,
                        padding.OAEP(
                            mgf=padding.MGF1(algorithm=hashes.SHA256()),
                            algorithm=hashes.SHA256(),
                            label=None,
                        ),
                    ).decode("utf-8")

                    import httpx

                    async with httpx.AsyncClient() as client:
                        user_res = await client.get(
                            "https://discord.com/api/v9/users/@me",
                            headers={"Authorization": token},
                        )
                        if user_res.status_code == 200:
                            user_data = user_res.json()
                            username = (
                                f"{user_data['username']}#{user_data['discriminator']}"
                            )
                            return {"token": token, "username": username}
                    return {"token": token, "username": "UnknownUser"}

                if op == "cancel":
                    return None
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            print("[QR Login] Timed out or connection closed while waiting for user.")
            return None
        finally:
            if self.ws and not self.ws.closed:
                await self.ws.close()
