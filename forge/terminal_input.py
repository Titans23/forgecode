"""Terminal input while a turn runs; pause before another prompt owns stdin."""
import asyncio


class TurnInput:
    def __init__(self, terminal, conversation, expand):
        self.terminal = terminal
        self.conversation = conversation
        self.expand = expand
        self.turn = None
        self.reader = None

    def resume(self):
        if self.turn is not None and not self.turn.done() and self.reader is None:
            self.reader = asyncio.create_task(self._read())

    async def pause(self):
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None

    async def _read(self):
        while True:
            try:
                text = await self.terminal.read_prompt_async()
            except (EOFError, KeyboardInterrupt):
                self.turn.cancel()
                return
            if not text.strip():
                continue
            if text.strip() == '/cancel':
                self.turn.cancel()
                return
            follow_up = text.startswith('/follow ')
            if text.startswith('/') and not follow_up:
                self.terminal.show_notice('Input', 'Use /cancel, /follow <task>, or type an adjustment while running.')
                continue
            try:
                content = self.expand(text[len('/follow '):] if follow_up else text)
                identity = self.conversation.queue_input(content, follow_up=follow_up)
                self.terminal.show_notice('Queued',
                    f'{identity[:8]}: ' + ('after this turn' if follow_up else 'before the next model request'))
            except ValueError as error:
                self.terminal.show_error(error)
