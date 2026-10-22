"""Session-owned state and history queries, separate from an executing turn."""
from forge.sessions.inbox import SessionInbox


class SessionController:
    def __init__(self, *, messages=(), journal=None, store=None, checkpoints=None):
        self.messages = [dict(message) for message in messages]
        self.journal = journal
        self.store = store
        self.checkpoints = checkpoints
        self.verification = []
        self.inbox = SessionInbox(lambda: self.journal)

    def commit_messages(self, messages):
        # Preserve the list identity shared with ContextManager.
        self.messages[:] = messages

    def history(self):
        if self.store is None or self.journal is None:
            return 'Session persistence is unavailable.'
        if self.journal.deferred:
            return 'No conversation history yet.'
        return '\n'.join('- {}: {}{}'.format(item['sequence'], item['type'],
            ' — ' + item['summary'] if item['summary'] else '')
            for item in self.store.history(self.journal.session_id))

    def candidates(self):
        if self.store is None:
            return 'Session persistence is unavailable.'
        sessions = self.store.list()
        if not sessions:
            return 'No saved ForgeCode sessions for this project.'
        return '\n'.join('- {}{} [{}]'.format(item.session_id,
            ' (' + item.name + ')' if item.name else '', item.status) for item in sessions)

    def rename(self, name):
        if self.journal is None:
            raise ValueError('Session persistence is unavailable.')
        return f'Renamed session to {self.journal.rename(name)}.'

    def resume_target(self, identifier, provider):
        if self.store is None:
            raise ValueError('Session persistence is unavailable.')
        state, journal = self.store.open(identifier)
        if journal.read_only:
            raise ValueError('Legacy sessions are read-only; fork one before continuing.')
        if state.info.provider and state.info.provider != provider:
            raise ValueError('This session uses a different provider; open it with its profile or fork it.')
        if not state.messages and state.active_task is None and not state.pending_inputs:
            raise ValueError(f'Session {state.info.session_id} has no resumable conversation history.')
        return state, journal
