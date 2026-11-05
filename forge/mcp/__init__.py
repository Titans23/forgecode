'''Model Context Protocol client integration for ForgeCode.'''

from forge.mcp.config import (
    MCPConfigurationError,
    MCPServerConfig,
    load_mcp_servers,
)

def __getattr__(name):
    if name == 'MCPClientManager':
        from forge.mcp.manager import MCPClientManager
        return MCPClientManager
    raise AttributeError(name)

__all__ = [
    'MCPClientManager',
    'MCPConfigurationError',
    'MCPServerConfig',
    'load_mcp_servers',
]
