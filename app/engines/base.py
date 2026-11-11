from __future__ import annotations
import socket
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..models import Profile, Proxy


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class RunningSession:
    profile_id: int
    engine: str
    context: Any
    ws_endpoint: str = ""
    debug_port: int = 0
    cleanups: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def close(self):
        try:
            await self.context.close()
        except Exception:  # noqa: BLE001
            pass
        for fn in self.cleanups:
            try:
                await fn()
            except Exception:  # noqa: BLE001
                pass


class Engine:
    name = "base"

    async def start(self, profile: Profile, proxy: Optional[Proxy], user_data_dir: str,
                    headless: bool = False) -> RunningSession:
        raise NotImplementedError
