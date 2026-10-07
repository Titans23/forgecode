"""Trusted local manual upgrade CLI, no automatic installer or data deletion."""
import argparse
import json
from pathlib import Path
from forge.release.runtime import verify_manifest
from forge.release.upgrade import backup_for_upgrade, restore_backup, uninstall_policy

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='operation',required=True)
    info=commands.add_parser('inspect');info.add_argument('--resources',required=True,type=Path)
    prepare=commands.add_parser('prepare')
    for name in ('data-dir','current-resources','target-resources','backup-root'):prepare.add_argument('--'+name,required=True,type=Path)
    restore=commands.add_parser('restore')
    for name in ('backup','destination','resources'):restore.add_argument('--'+name,required=True,type=Path)
    commands.add_parser('uninstall-policy')
    args=parser.parse_args(argv)
    try:
        if args.operation=='inspect':result=verify_manifest(args.resources)
        elif args.operation=='prepare':result={'backup':str(backup_for_upgrade(args.data_dir,args.current_resources,args.backup_root,target_resources=args.target_resources))}
        elif args.operation=='restore':result={'restored':str(restore_backup(args.backup,args.destination,args.resources))}
        else:result=uninstall_policy()
        print(json.dumps({'status':'pass','result':result}));return 0
    except Exception as error:
        print(json.dumps({'status':'blocked','reason':str(error)}));return 2

if __name__=='__main__':raise SystemExit(main())
