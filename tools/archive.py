"""Deterministic corresponding-source archives with preserved dependency notices."""

from contextlib import contextmanager
import gzip
import tarfile


class SourceTar(tarfile.TarFile):
    epoch = 0

    def addfile(self, tarinfo, fileobj=None):
        tarinfo.uid = tarinfo.gid = 0
        tarinfo.uname = tarinfo.gname = ""
        tarinfo.mtime = self.epoch
        tarinfo.mode = 0o755 if tarinfo.isdir() or tarinfo.mode & 0o111 else 0o644
        tarinfo.pax_headers = {}
        return super().addfile(tarinfo, fileobj)


@contextmanager
def deterministic_tar(path, epoch=0):
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
            with SourceTar.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                archive.epoch = epoch
                yield archive
