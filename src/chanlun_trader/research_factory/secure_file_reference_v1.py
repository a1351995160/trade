"""受信部署引用与任务工件的文件边界；错误不披露任意路径的存在性。"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat


def validated_reference_path(value, *, error_code, root=None):
    """外部部署可显式引用绝对路径；任务工件须另传固定归属 root。"""
    try:
        if not isinstance(value, (str, Path)):
            raise ValueError
        text = str(value)
        path, windows = Path(text), PureWindowsPath(text)
        if (not text or '\x00' in text or not path.is_absolute()
                or '..' in path.parts or '..' in windows.parts
                or (windows.drive and not re.fullmatch(r'[A-Za-z]:', windows.drive))):
            raise ValueError
        for part in windows.parts[1:] if windows.anchor else windows.parts:
            if (':' in part or part.endswith((' ', '.'))
                    or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', part.split('.')[0], re.I)):
                raise ValueError
        # resolve 仅用于拒绝别名；绝不把未经核对的路径解析结果作为可信新路径。
        if path.resolve() != path:
            raise ValueError
        if root is not None:
            boundary = validated_reference_path(root, error_code=error_code)
            if not path.is_relative_to(boundary):
                raise ValueError
        return path
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(error_code) from exc


def _checked_path(value, *, error_code, root, directory):
    try:
        path = validated_reference_path(value, error_code=error_code, root=root)
        metadata = path.lstat()
        if (getattr(metadata, 'st_file_attributes', 0) & 0x400
                or not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))):
            raise ValueError
        return path
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(error_code) from exc


def checked_file_path(value, *, error_code, root=None):
    return _checked_path(value, error_code=error_code, root=root, directory=False)


def checked_directory_path(value, *, error_code, root=None):
    return _checked_path(value, error_code=error_code, root=root, directory=True)


@contextmanager
def _regular_reader(value, *, error_code, root):
    descriptor = None
    try:
        path = checked_file_path(value, error_code=error_code, root=root)
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        opened = os.fstat(descriptor)
        # 再验路径及已打开对象，拒绝检查与打开之间发生的文件/链接替换。
        checked_file_path(path, error_code=error_code, root=root)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError
        stream = os.fdopen(descriptor, 'rb')
        descriptor = None
        with stream:
            yield stream
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(error_code) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_file_bytes(value, *, error_code, root=None, maximum_bytes=None):
    if maximum_bytes is not None and (type(maximum_bytes) is not int or maximum_bytes < 1):
        raise ValueError(error_code)
    with _regular_reader(value, error_code=error_code, root=root) as stream:
        raw = stream.read() if maximum_bytes is None else stream.read(maximum_bytes + 1)
        if maximum_bytes is not None and len(raw) > maximum_bytes:
            raise ValueError(error_code)
        return raw


def file_sha256(value, *, error_code, root=None):
    digest = hashlib.sha256()
    with _regular_reader(value, error_code=error_code, root=root) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_pinned_json(reference, *, error_code, root=None, maximum_bytes=20 * 1024 * 1024):
    try:
        if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}
                or not isinstance(reference['sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', reference['sha256'])):
            raise ValueError
        raw = read_file_bytes(reference['path'], error_code=error_code, root=root, maximum_bytes=maximum_bytes)
        if hashlib.sha256(raw).hexdigest() != reference['sha256']:
            raise ValueError
        return json.loads(raw)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        cause = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        raise ValueError(error_code) from cause
