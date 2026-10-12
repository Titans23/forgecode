"""A distributable package must carry its runtime and exclude private configuration."""
import io
import tarfile

import pytest

from scripts.package_cli import check_archive


def archive(tmp_path, additional=()):
    output = tmp_path / 'package.tgz'
    names = ['package/bin/forge.mjs', 'package/lib/install.mjs', 'package/assets/runtime.json',
             'package/assets/constraints.txt', 'package/assets/runtime.whl', *additional]
    with tarfile.open(output, 'w:gz') as package:
        for name in names:
            value = b'archive-layout fixture'
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            package.addfile(entry, io.BytesIO(value))
    return output


@pytest.mark.parametrize('name', ['package/assets/.env', 'package/lib/.env.production',
                                  'package/lib/.npmrc', 'package/lib/credentials.json'])
def test_private_files_refuse_distribution(tmp_path, name):
    with pytest.raises(ValueError, match='Private'):
        check_archive(archive(tmp_path, [name]), 'runtime.whl')


def test_runtime_payload_is_required(tmp_path):
    output = archive(tmp_path)
    assert check_archive(output, 'runtime.whl') == 5
    with pytest.raises(ValueError, match='incomplete'):
        check_archive(output, 'missing.whl')
