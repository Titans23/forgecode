"""Bounded pending user inputs; the Journal records admission and consumption."""
from copy import deepcopy
from uuid import uuid4

from forge.messages import normalize_input


class SessionInbox:
    def __init__(self, journal):
        self._journal = journal
        self._items = []

    def restore(self, items):
        self._items = deepcopy(list(items))

    @property
    def pending(self):
        return deepcopy(self._items)

    def queue(self, content, *, follow_up=False):
        content = normalize_input(content)
        if len(self._items) >= 16:
            raise ValueError('At most 16 pending inputs are allowed')
        item = {'id': uuid4().hex, 'mode': 'follow_up' if follow_up else 'steering', 'content': content}
        journal = self._journal()
        if journal is not None:
            journal.commit()
            journal.append('input_queued', item)
        self._items.append(item)
        return item['id']

    def peek(self, mode=None):
        return next((deepcopy(item) for item in self._items if mode is None or item['mode'] == mode), None)

    def consumed(self, identity):
        # Called only after the corresponding user_message append succeeded.
        self._items[:] = [item for item in self._items if item['id'] != identity]

    def discard(self, identity):
        if any(item['id'] == identity for item in self._items):
            journal = self._journal()
            if journal is not None:
                journal.append('input_discarded', {'id': identity})
            self.consumed(identity)
            return True
        return False

    def defer_steering(self):
        identities = [item['id'] for item in self._items if item['mode'] == 'steering']
        if not identities:
            return
        journal = self._journal()
        if journal is not None:
            journal.append('input_deferred', {'ids': identities})
        for item in self._items:
            if item['id'] in identities:
                item['mode'] = 'follow_up'
