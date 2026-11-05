"""Local, explicit plugin management. Checks/development are added by the validator."""
import json
from pathlib import Path
import typer

from forge.agents import PluginSpec
from forge.compositions import read_settings, update_settings
from forge.plugins import describe_plugin

app = typer.Typer(help='Develop and explicitly enable trusted Python plugins.')


@app.command('list')
def list_plugins():
    plugins = read_settings().get('plugins', {})
    typer.echo(json.dumps({name: {key: value.get(key) for key in ('source', 'enabled', 'trusted')}
                          for name, value in plugins.items()}, ensure_ascii=False, indent=2))


@app.command('enable')
def enable(source: str, trust: bool = typer.Option(False, '--trust', help='Trust arbitrary host Python code.')):
    if not trust:
        raise typer.BadParameter('Enabling a plugin requires --trust; plugin code is not sandboxed.')
    source = source if source.startswith('entrypoint:') else str(Path(source).resolve(strict=True))
    manifest, _ = describe_plugin(PluginSpec(source, trusted=True))
    def apply(settings):
        settings.setdefault('plugins', {})[manifest['id']] = {'source': source, 'trusted': True, 'enabled': True}
    update_settings(apply)
    typer.echo(f"Enabled {manifest['id']}. Select it in an agent composition; existing sessions are unchanged.")


@app.command('disable')
def disable(identifier: str):
    def apply(settings):
        plugins = settings.get('plugins', {})
        if identifier not in plugins:
            raise typer.BadParameter('Unknown plugin')
        plugins[identifier]['enabled'] = False
    update_settings(apply)
    typer.echo(f'Disabled {identifier} for new sessions.')


@app.command('init')
def initialize(directory: Path, name: str = 'example'):
    from forge.agents import AgentDefinition
    AgentDefinition(name=name)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'forge-plugin.json').write_text(json.dumps({'id': name, 'api_version': 1,
        'entry_point': 'plugin.py:activate', 'requires': []}, indent=2) + '\n', encoding='utf-8')
    (directory / 'plugin.py').write_text('''def activate(ctx, config):
    ctx.register_tool("greet", "Greet a person", {
        "type": "object", "properties": {"name": {"type": "string"}},
        "required": ["name"], "additionalProperties": False,
    }, lambda args: "Hello, " + args["name"] + "!")
''', encoding='utf-8')
    (directory / 'pyproject.toml').write_text('[project]\nname = "forge-plugin-' + name + '"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndependencies = []\n', encoding='utf-8')
    typer.echo(str(directory.resolve()))
