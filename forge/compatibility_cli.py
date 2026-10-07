"""New compatibility entry points delegate to the existing Harness/Engine commands."""
import importlib
import json
from pathlib import Path
import typer

history=typer.Typer(help='Readonly legacy scan, verified backup, explicit hash confirmation and idempotent import.',add_completion=False)


def run_history(operation,data_dir,profile_id,project=None,source=None,confirmation=None,legacy_id=None,offset=0):
    from forge.application.legacy_import import LegacyImporter
    from forge.application.models import ContractError
    from forge.engine.persistence import Store
    try:
        with Store(data_dir) as store:
            importer=LegacyImporter(store,profile_id=profile_id)
            if operation=='prepare':result=importer.prepare(project,source)
            elif operation=='import':result=importer.import_confirmed(project,source,confirmation_sha256=confirmation)
            elif operation=='list':result=importer.list(offset=offset)
            else:result=importer.inspect(legacy_id)
            typer.echo(json.dumps(result,ensure_ascii=False,sort_keys=True))
    except (ContractError,OSError) as error:
        typer.echo(json.dumps({'status':'blocked','kind':str(getattr(error,'kind','SOURCE_UNAVAILABLE'))}),err=True)
        raise typer.Exit(1) from None


@history.command('prepare')
def prepare(project:Path,source:Path,data_dir:Path=typer.Option(...,'--data-dir'),profile_id:str=typer.Option('cli-profile','--profile-id')):
    run_history('prepare',data_dir,profile_id,project,source)


@history.command('import')
def import_history(project:Path,source:Path,confirm_sha256:str=typer.Option(...,'--confirm-sha256'),
    data_dir:Path=typer.Option(...,'--data-dir'),profile_id:str=typer.Option('cli-profile','--profile-id')):
    run_history('import',data_dir,profile_id,project,source,confirm_sha256)


@history.command('list')
def list_history(data_dir:Path=typer.Option(...,'--data-dir'),profile_id:str=typer.Option('cli-profile','--profile-id'),offset:int=typer.Option(0,'--offset',min=0)):
    run_history('list',data_dir,profile_id,offset=offset)


@history.command('inspect')
def inspect_history(legacy_id:str,data_dir:Path=typer.Option(...,'--data-dir'),profile_id:str=typer.Option('cli-profile','--profile-id')):
    run_history('inspect',data_dir,profile_id,legacy_id=legacy_id)


def delegate(target,ctx):
    try:entry=importlib.import_module(target)
    except ModuleNotFoundError as error:
        if target=='forge.engine.http_adapter' and error.name=='aiohttp':
            typer.echo('Optional Web transport is unavailable; install forge-code[web] using the pinned lock.',err=True)
            raise typer.Exit(2) from None
        raise
    raise typer.Exit(int(entry.main(ctx.args) or 0))


def register(app):
    app.add_typer(history,name='history')
    settings={'allow_extra_args':True,'ignore_unknown_options':True}
    modules={'engine':'forge.engine.__main__','sandbox':'forge.sandbox.doctor','eval':'benchmark.cli','web':'forge.engine.http_adapter'}
    def command_factory(target):
        def command(ctx:typer.Context):return delegate(target,ctx)
        return command
    for name,module in modules.items():
        app.command(name,context_settings=settings,add_help_option=False)(command_factory(module))
