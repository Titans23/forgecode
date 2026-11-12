"""Own model clients until session construction transfers their ownership."""
import asyncio


class ModelStartup:
    def __init__(self, factory):
        self.factory = factory
        self.clients = []
        self.active = True

    def create(self, *args, **kwargs):
        client = self.factory(*args, **kwargs)
        if self.active:
            self.clients.append(client)
        return client

    def release(self):
        self.active = False
        self.clients.clear()

    async def close(self):
        failures = []
        clients = tuple({id(client): client for client in self.clients}.values())
        self.release()
        for client in clients:
            close = getattr(client, 'aclose', None)
            if close:
                try:
                    await close()
                except (Exception, asyncio.CancelledError) as error:
                    failures.append(type(error).__name__)
        return failures
