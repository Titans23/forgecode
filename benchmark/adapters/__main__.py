"""Read-only diagnostics/export and explicit public task materialization."""
import argparse
import asyncio
import json
from pathlib import Path

from benchmark.adapters.diagnostics import doctor
from benchmark.adapters.harbor import HarborAdapter, export_runspec
from benchmark.adapters.materialize import materialize_registry
from benchmark.adapters.protocol import file_bytes
from forge.application.models import ContractError, strict_loads, validate


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor')
    describe=sub.add_parser('describe')
    describe.add_argument('benchmark')
    materialize=sub.add_parser('materialize')
    materialize.add_argument('benchmark')
    materialize.add_argument('--registry',required=True,type=Path)
    materialize.add_argument('--version',required=True)
    materialize.add_argument('--task',action='append',required=True)
    materialize.add_argument('--output',required=True,type=Path)
    export=sub.add_parser('export')
    export.add_argument('--plan',required=True,type=Path)
    export.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=='doctor':
            result=doctor()
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 2 if result['issues'] else 0
        if args.command=='describe':
            result=HarborAdapter(args.benchmark).describe()
        elif args.command=='materialize':
            result=asyncio.run(materialize_registry(args.registry,benchmark=args.benchmark,
                version=args.version,task_ids=args.task,destination=args.output))
        else:
            plan=strict_loads(file_bytes(args.plan,limit=1048576))
            validate('run-spec',plan['spec'])
            result=export_runspec(plan['spec'],plan['resolved_snapshots'])
            # Never replace an existing exported plan or mutate local model settings.
            with args.output.open('x',encoding='utf-8') as stream:
                stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ContractError,OSError,ValueError,KeyError) as error:
        print(json.dumps({'status':'blocked','exception_type':type(error).__name__,
            'reason':str(error) if isinstance(error,ContractError) else 'Input or environment unavailable'},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
