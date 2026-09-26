"""Operation-local location metadata for the native synchronous Store.

Ordinary queries validate the paths of the requested object on every access.
Whole-table interfaces retain whole-table live validation. Content hashing,
container scanning, publication and review authority stay in the native Store.
No persistent format change. Full-table queries retain live validation.
"""
from __future__ import annotations
import contextlib, copy, ctypes, os, threading
from collections.abc import Mapping
from pathlib import Path
from crs_model import CRSError,parse_json
from crs_store import SHA,ordinary


def fail(code,message):
    raise CRSError(code,message)


def _read_lease(path):
    """Return an owned stream for one ordinary file; deny data writes/replaces.

    This freezes table bytes, not all filesystem topology. Actual object paths
    are checked at use. Windows sharing conflicts cause the caller to fall back.
    """
    import msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
        wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
    if handle==wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        class AttributeTag(ctypes.Structure):
            _fields_=[('attributes',wintypes.DWORD),('tag',wintypes.DWORD)]
        kernel.GetFileInformationByHandleEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
        info=AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400|0x10):
            fail('reparse_path','Location metadata must be an ordinary file.')
        descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
        handle=None
        try:return os.fdopen(descriptor,'rb')
        except BaseException:
            os.close(descriptor);raise
    finally:
        if handle is not None:kernel.CloseHandle(handle)


def _structure(data):
    if (not isinstance(data,dict) or set(data)-{'schema','objects','pending_moves'}
        or data.get('schema')!='crs-locations/v1' or not isinstance(data.get('objects'),dict)
        or not isinstance(data.get('pending_moves',{}),dict)):
        fail('locations_invalid','Cold asset location table is invalid.')
    def key(sha):
        if not isinstance(sha,str) or not SHA.fullmatch(sha):
            fail('invalid_digest','Expected a lowercase SHA-256 content identity.')
    def absolute(value):
        if not isinstance(value,str) or not value or not Path(value).is_absolute():
            fail('locations_invalid','Registered locations must be absolute ordinary file paths.')
    for sha,value in data['objects'].items():key(sha);absolute(value)
    for sha,move in data.get('pending_moves',{}).items():
        key(sha)
        if not isinstance(move,dict) or set(move)!={'source','destination'}:
            fail('locations_invalid','A pending cold move must bind its source and destination.')
        absolute(move['source']);absolute(move['destination'])


class _Locations(Mapping):
    def __init__(self, scope, store, data, counts):
        self._scope, self.store, self._data, self.counts = scope, store, data, counts

    def _check(self,sha):
        self._scope.assert_current()
        self.counts['entry_checks']+=1
        store=self.store;data=self._data
        hot=store._object(sha)
        def checked(value,allow_hot=False):
            candidate=ordinary(Path(value)).absolute()
            if allow_hot and candidate==hot:return candidate
            if (candidate.name!=sha or candidate.resolve().is_relative_to(store.root)
                    or store.root.is_relative_to(candidate.parent.resolve())):
                fail('locations_invalid','Cold locations must name their digest outside the project and its ancestors.')
            return candidate
        if sha in data['objects']:checked(data['objects'][sha])
        move=data.get('pending_moves',{}).get(sha)
        if move is not None:
            source=checked(move['source'],True);destination=checked(move['destination'])
            current=data['objects'].get(sha)
            if (source==destination or (current is None and source!=hot)
                    or (current is not None and current not in {str(source),str(destination)})):
                fail('locations_invalid','A pending cold move does not match the registered object location.')

    def _all(self):
        self._scope.assert_current()
        self.counts['full_enumerations']+=1
        for sha in self._data['objects'].keys()|self._data.get('pending_moves',{}).keys():self._check(sha)

    def __getitem__(self,sha):
        self._scope.assert_current()
        # Negative lookups need no historical filesystem traversal.
        if sha in self._data['objects'] or sha in self._data.get('pending_moves',{}):self._check(sha)
        return self._data['objects'][sha]

    def __iter__(self):
        self._all()
        for sha in self._data['objects']:
            self._scope.assert_current()
            yield sha

    def __len__(self):
        self._all();return len(self._data['objects'])


class _Scope:
    def __init__(self, store, resources):
        self.store = store
        self.resources = resources
        self.owner = threading.get_ident()
        self.active = True
        self.attempted = False
        self.failure = None
        self.stream = None
        self.data = None
        self.mapping = None
        self.path = store.root / 'locations.json'
        self.counts = dict(active=False, table_reads=0, table_bytes=0,
                           entry_checks=0, full_enumerations=0, fallback=None)

    def assert_current(self):
        if not self.active or threading.get_ident() != self.owner:
            fail('locations_invalid', 'Location views are confined to their active synchronous operation.')
        if self.stream is not None:
            ordinary(self.path)
            try:
                current = self.path.stat()
                held = os.fstat(self.stream.fileno())
            except OSError:
                fail('locations_invalid', 'Held location metadata is no longer available at its original path.')
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                fail('locations_invalid', 'Location metadata identity changed during the operation.')

    def locations(self, with_pending=False):
        self.assert_current()
        if self.failure is not None:
            raise self.failure
        if not self.attempted:
            self.attempted = True
            try:
                path = self.store._path('locations.json')
                if not path.exists():
                    self.counts['fallback'] = 'no_table'
                else:
                    try:
                        stream = _read_lease(path)
                    except OSError:
                        self.counts['fallback'] = 'lease_unavailable'
                    else:
                        self.stream = self.resources.enter_context(stream)
                        self.assert_current()
                        raw = stream.read()
                        self.counts['table_reads'] = 1
                        self.counts['table_bytes'] = len(raw)
                        self.data = parse_json(raw)
                        _structure(self.data)
                        self.mapping = _Locations(self, self.store, self.data, self.counts)
                        self.counts['active'] = True
            except BaseException as error:
                self.failure = error
                raise
        if self.mapping is None:
            return self.store._locations_full(with_pending)
        if with_pending:
            self.mapping._all()
            return copy.deepcopy(self.data)
        return self.mapping


@contextlib.contextmanager
def operation_scope(store):
    """Reentrant, lazy Windows metadata lease under the existing writer mutex.

    Other platforms retain native locking and full location validation. A scope
    never validates location bytes before native operation/HEAD dispatch needs
    them. Escaped views are invalidated before releasing the held file.
    """
    current = store._location_scope
    if current is not None:
        current.assert_current()
        yield current.counts
        return
    if os.name != 'nt':
        yield dict(active=False, table_reads=0, table_bytes=0, entry_checks=0,
                   full_enumerations=0, fallback='platform')
        return
    with store._lock(), contextlib.ExitStack() as resources:
        scope = _Scope(store, resources)
        store._location_scope = scope
        try:
            yield scope.counts
        finally:
            scope.active = False
            store._location_scope = None
            store._last_location_counts = dict(scope.counts)
