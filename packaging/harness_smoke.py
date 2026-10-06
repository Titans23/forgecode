"""Frozen F01 probe imports the actual Harness without constructing model clients."""
import inspect
import json
import sys

import forge
from forge.runtime.agent_loop import Conversation
from forge.runtime.executor import ToolExecutor
from forge.runtime.factory import create_runtime
from forge.sessions.store import SessionJournal

if __name__ == '__main__':
    print(json.dumps({'version': forge.__version__, 'python': sys.version.split()[0],
                      'frozen': bool(getattr(sys, 'frozen', False)),
                      'conversation_stream': str(inspect.signature(Conversation.stream)),
                      'tool_executor': str(inspect.signature(ToolExecutor.execute)),
                      'journal_append': str(inspect.signature(SessionJournal.append)),
                      'factory': str(inspect.signature(create_runtime))}))
