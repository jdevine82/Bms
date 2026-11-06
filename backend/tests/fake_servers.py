"""Small fake servers for alarm delivery tests (milestone 6B): an HTTP server standing in for
ClickSend and ntfy, and an SMTP server. They record what they receive."""

from __future__ import annotations

import asyncio
import json


class FakeHttp:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status = 200
        self.reply: dict = {"data": {"messages": [{"status": "SUCCESS"}]}}
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readline()).decode()
            method, path, _ = line.split(" ", 2)
            headers = {}
            while True:
                h = (await reader.readline()).decode().strip()
                if not h:
                    break
                k, _, v = h.partition(":")
                headers[k.strip().lower()] = v.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            self.requests.append({"method": method, "path": path, "headers": headers,
                                  "json": json.loads(body) if body else None})
            payload = json.dumps(self.reply).encode()
            writer.write(f"HTTP/1.1 {self.status} X\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        finally:
            writer.close()


class FakeSmtp:
    """Plain SMTP (security "none"), enough for smtplib.send_message."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def say(text: str) -> None:
            writer.write((text + "\r\n").encode())

        say("220 fake ESMTP")
        mail: dict = {"to": []}
        try:
            while True:
                line = (await reader.readline()).decode().rstrip("\r\n")
                if not line:
                    break
                cmd = line[:4].upper()
                if cmd in ("EHLO", "HELO"):
                    say("250 fake")
                elif cmd == "MAIL":
                    mail = {"from": line, "to": []}
                    say("250 OK")
                elif cmd == "RCPT":
                    mail["to"].append(line.split(":", 1)[1].strip("<> "))
                    say("250 OK")
                elif cmd == "DATA":
                    say("354 go ahead")
                    await writer.drain()
                    data = []
                    while True:
                        d = (await reader.readline()).decode()
                        if d in (".\r\n", ".\n"):
                            break
                        data.append(d)
                    mail["data"] = "".join(data)
                    self.messages.append(mail)
                    say("250 OK")
                elif cmd == "QUIT":
                    say("221 bye")
                    await writer.drain()
                    break
                else:
                    say("250 OK")
                await writer.drain()
        finally:
            writer.close()
