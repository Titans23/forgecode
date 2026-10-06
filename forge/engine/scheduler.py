"""One persisted CAS work owner. Shutdown never dispatches a new cancelled turn."""
import asyncio
import json
import sys


class TurnScheduler:
    def __init__(self, methods, ready):
        self.methods, self.ready = methods, ready
        self.active = None

    async def run(self):
        while True:
            self.ready.clear()
            if self.methods.stopping and self.methods.shutdown_mode == 'cancel':
                return
            if not self.methods.initialized:
                if self.methods.stopping:
                    return
                await self.ready.wait()
                continue
            store = self.methods.store
            row = store.connection.execute("SELECT kind,business_id FROM work_items WHERE kind IN ('turn','attempt') AND state='queued' ORDER BY rowid LIMIT 1").fetchone()
            reconciling = store.connection.execute("SELECT 1 FROM work_items WHERE state='reconciling' LIMIT 1").fetchone()
            running = store.connection.execute("SELECT 1 FROM work_items WHERE state='running' LIMIT 1").fetchone()
            if row and not reconciling and not running:
                execute = self.methods.service.execute_turn if row['kind'] == 'turn' else self.methods.evaluations.scheduler.execute
                self.active = asyncio.create_task(execute(row['business_id']))
                try:
                    await self.active
                except Exception as error:
                    print(json.dumps({'component': 'scheduler', 'exception_type': type(error).__name__}), file=sys.stderr)
                finally:
                    self.active = None
                continue
            if self.methods.stopping:
                return
            await self.ready.wait()
