import asyncio
import json
from pathlib import Path
import typer

from forge.delivery import load_definition, export_agent, import_agent

app = typer.Typer(help='Check and transport explicit Agent compositions.')


@app.command('check')
def check(definition: Path, cases: Path = typer.Option(..., '--cases'),
          trust: bool = typer.Option(False, '--trust'), output: Path | None = None):
    from forge.validation import check_agent
    from forge.plugin_cli import emit_result
    agent = load_definition(definition, trust=trust)
    if agent.plugins and not trust:
        raise typer.BadParameter('Checking executes trusted host Python; specify --trust')
    result = asyncio.run(check_agent(agent, json.loads(cases.read_text(encoding='utf-8'))))
    emit_result(result, output)


@app.command('export')
def export(definition: Path, destination: Path, cases: Path = typer.Option(..., '--cases'),
           sdk_wheel: Path = typer.Option(..., '--sdk-wheel', help='Matching SDK wheel built with uv build --wheel.'),
           receipt: Path | None = None, models: Path | None = None):
    result = export_agent(load_definition(definition), json.loads(cases.read_text(encoding='utf-8')), destination,
                          receipt=json.loads(receipt.read_text(encoding='utf-8')) if receipt else None,
                          models=json.loads(models.read_text(encoding='utf-8')) if models else None, sdk_wheel=sdk_wheel)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command('import')
def import_delivery(archive: Path, directory: Path):
    typer.echo(json.dumps(import_agent(archive, directory), ensure_ascii=False, indent=2))
