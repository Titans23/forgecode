"""Private filtered copies, never a bind mount of the user's working tree."""
from dataclasses import replace
import os
from pathlib import Path

from forge.sandbox.path_policy import inspect_path
from forge.sandbox.workspace_transfer import TransferLimits, WorkspaceSnapshot, rejected


def capture_inputs(policy, transfer_limits=None):
    project = WorkspaceSnapshot(policy, limits=transfer_limits)
    roots = sorted(set(policy.read_roots), key=lambda path: (len(path.parts), str(path)))
    references = []
    remaining_entries = project.limits.entries - len(project.files) - len(project.directories)
    remaining_bytes = project.limits.bytes - sum(len(data) for data, _ in project.files.values())
    for root in roots:
        if root.is_relative_to(project.root) or any(root.is_relative_to(ref.root) for ref in references):
            continue
        if len(references) >= 64 or remaining_entries < 1 or remaining_bytes < 1:
            raise rejected('Combined sandbox input quota exceeded', 'ARTIFACT_LIMIT')
        reference_policy = replace(policy, workspace=inspect_path(root, absolute=True), write_roots=(),
            protected_paths=(*policy.protected_paths, project.root))
        reference = WorkspaceSnapshot(reference_policy,
            limits=TransferLimits(entries=remaining_entries, bytes=remaining_bytes))
        references.append(reference)
        remaining_entries -= len(reference.files) + len(reference.directories) + 1
        remaining_bytes -= sum(len(data) for data, _ in reference.files.values())
        if remaining_entries < 0:
            raise rejected('Combined sandbox input quota exceeded', 'ARTIFACT_LIMIT')
    return project, references


def writable_directories(snapshot):
    roots = []
    for path in sorted(set(snapshot.policy.write_roots), key=lambda path: (len(path.parts), str(path))):
        snapshot.policy.authorize(path, write=True).assert_current()
        if not path.is_relative_to(snapshot.root):
            raise rejected('Container write roots must be inside the project')
        name = path.relative_to(snapshot.root).as_posix()
        name = '' if name == '.' else name
        if name and name not in snapshot.directories:
            raise rejected('Container write roots must be existing visible directories')
        if not any(not parent or name.startswith(parent + '/') for parent in roots):
            roots.append(name)
    if len(roots) > 64:
        raise rejected('At most 64 writable directories are supported')
    return tuple(roots)


def prepare_inputs(directory, project, references, runtime):
    """Materialize bytes already audited by snapshots in a new private directory."""
    directory = Path(directory)
    (directory / 'references').mkdir(mode=0o700)
    for snapshot, target in [(project, directory / 'project'),
            *((ref, directory / 'references' / str(index)) for index, ref in enumerate(references))]:
        target.mkdir(mode=0o700)
        for name in sorted(snapshot.directories, key=lambda name: (name.count('/'), name)):
            (target / name).mkdir(mode=0o700)
        for name, (data, executable) in snapshot.files.items():
            destination = target / name
            with destination.open('xb') as stream:
                stream.write(data)
            destination.chmod(0o700 if executable else 0o600)
    # Root-owned Linux controllers still run guests as a non-root user. Only
    # this fresh filtered copy changes owner, never the source workspace.
    if os.name != 'nt' and os.getuid() == 0:
        for parent, directories, files in os.walk(directory):
            os.chown(parent, runtime.uid, runtime.gid)
            for name in files:
                os.chown(Path(parent) / name, runtime.uid, runtime.gid)
