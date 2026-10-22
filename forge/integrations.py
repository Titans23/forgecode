"""Optional integration composition; never imported by the model/tool loop."""
import os
import sys
from pathlib import Path
from typing import Any
from dotenv import dotenv_values

from forge.channels import load_channel_settings
from forge.mcp import load_mcp_servers
from forge.mcp.config import InternalStdioServerConfig


def load_runtime_mcp_servers(root: Path) -> dict[str, Any]:
    '''Merge project MCP servers with enabled Feishu office sidecars.'''
    environment = {key: value for key, value in dotenv_values(root / '.env').items() if value is not None}
    environment.update(os.environ)
    servers: dict[str, Any] = dict(load_mcp_servers(root, environ=environment))
    settings = load_channel_settings(root, environ=environment)
    policies = {
        'feishu_document_read': 'read',
        'feishu_document_create': 'write',
        'feishu_document_update': 'write',
        'feishu_message_send': 'write',
    }
    for name, config in settings.channels.items():
        if not config.enabled or config.platform != 'feishu':
            continue
        ready, _ = config.credential_status(environment)
        if not ready:
            continue
        servers.setdefault(
            f'office-{name}',
            InternalStdioServerConfig(
                command=sys.executable,
                args=('office-mcp',) if getattr(sys,'frozen',False) else ('-m', 'forge.office.mcp_server'),
                env={
                    'APP_ID': environment[config.app_id_env],
                    'APP_SECRET': environment[config.app_secret_env],
                },
                toolPolicies=policies,
            ),
        )
    return servers
