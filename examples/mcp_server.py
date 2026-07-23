'''Small local MCP server used by ForgeCode documentation and integration tests.'''

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP


def build_server(*, port: int = 8000) -> FastMCP:
    server = FastMCP(
        'ForgeCode Example',
        host='127.0.0.1',
        port=port,
        stateless_http=True,
        json_response=True,
        log_level='ERROR',
    )

    @server.tool()
    def echo(message: str) -> str:
        '''Return the supplied message.'''
        return message

    @server.tool()
    def add(left: int, right: int) -> int:
        '''Add two integers.'''
        return left + right

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--transport',
        choices=('stdio', 'streamable-http'),
        default='stdio',
    )
    parser.add_argument('--port', type=int, default=8000)
    arguments = parser.parse_args()
    build_server(port=arguments.port).run(transport=arguments.transport)


if __name__ == '__main__':
    main()
