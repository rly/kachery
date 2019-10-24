import os
import numpy as np
from typing import Union, Tuple, Optional, List
import simplejson
import json
import hashlib
import tempfile
import time
import requests
import urllib.request as request
import shutil
from .localhashcache import LocalHashCache

_global_config=dict(
    url=os.getenv('KACHERY_URL', None),
    channel=os.getenv('KACHERY_CHANNEL', None),
    password=os.getenv('KACHERY_PASSWORD', None),
    algorithm='sha1',
    use_remote=False,
    use_remote_only=False
)

_hash_caches = dict(
    sha1=LocalHashCache(algorithm='sha1'),
    md5=LocalHashCache(algorithm='md5')
)

def set_config(*,
        use_remote: Union[bool, None]=None,
        use_remote_only: Union[bool, None]=None,
        channel: Union[str, None]=None,
        password: Union[str, None]=None,
        algorithm: Union[str, None]=None,
        url: Union[str, None]=None
) -> None:
    if use_remote is not None:
        _global_config['use_remote'] = use_remote
    if use_remote_only is not None:
        _global_config['use_remote_only'] = use_remote_only
    if channel is not None:
        _global_config['channel'] = channel
    if password is not None:
        _global_config['password'] = password
    if algorithm is not None:
        _global_config['algorithm'] = algorithm
    if url is not None:
        _global_config['url'] = url

def get_config() -> dict:
    return _load_config()

def _load_config(**kwargs) -> dict:
    if 'config' in kwargs:
        ret = kwargs['config']
    else:
        ret = dict()
        for key, val in _global_config.items():
            ret[key] = val
    for key, val in kwargs.items():
        ret[key] = val
    return ret

def _is_hash_url(path):
    algs = ['sha1', 'md5']
    for alg in algs:
        if path.startswith(alg + '://') or path.startswith(alg + 'dir://'):
            return True
    return False

def load_file(path: str, dest: str=None, **kwargs)-> Union[str, None]:
    config = _load_config(**kwargs)
    if _is_hash_url(path):
        if not config['use_remote_only']:
            ret, _, _ = _find_file_locally(path, config=config)
            if ret:
                if dest:
                    shutil.copyfile(ret, dest)
                    return dest
                return ret
        if config['use_remote'] or config['use_remote_only']:
            url0, algorithm, hash0, size0 = _check_remote_file(path, config=config)
            if url0:
                assert algorithm is not None
                assert hash0 is not None
                assert size0 is not None
                return _hash_caches[algorithm].downloadFile(url=url0, hash=hash0, size=size0, target_path=dest)
            else:
                return None
        return None
    else:
        if os.path.isfile(path):
            if dest:
                shutil.copyfile(path, dest)
                return dest
            return path
        else:
            return None
    
def load_text(path: str, **kwargs) -> Union[str, None]:
    path2 = load_file(path, **kwargs)
    if not path2:
        return None
    with open(path2, 'r') as f:
        return f.read()

def load_object(path: str, **kwargs) -> Union[dict, None]:
    path2 = load_file(path, **kwargs)
    if not path2:
        return None
    with open(path2, 'r') as f:
        return simplejson.load(f)

def load_npy(path: str, **kwargs) -> Union[np.ndarray, None]:
    path2 = load_file(path, **kwargs)
    if not path2:
        return None
    return np.load(path2)

def get_file_info(path: str, **kwargs) -> Union[dict, None]:
    config = _load_config(**kwargs)
    if _is_hash_url(path):
        if not config['use_remote_only']:
            fname, hash1, algorithm1 = _find_file_locally(path, config=config)
            if fname:
                assert hash1 is not None
                assert algorithm1 is not None
                ret = dict(
                    path=fname,
                    size=os.path.getsize(fname)
                )
                ret[algorithm1] = hash1
                return ret
        if config['use_remote'] or config['use_remote_only']:
            url0, algorithm, hash0, size0 = _check_remote_file(path, config=config)
            if url0:
                assert algorithm is not None
                assert hash0 is not None
                assert size0 is not None
                ret = dict(
                    url=url0,
                    size=size0
                )
                ret[algorithm] = hash0
                return ret
            else:
                return None
        return None
    else:
        if os.path.isfile(path):
            ret = dict(
                path=path,
                size=os.path.getsize(path)
            )
            ret[config['algorithm']] = _compute_local_file_hash(path, algorithm=config['algorithm'], config=config)
            return ret
        else:
            return None

def store_file(path: str, basename: Union[str, None]=None, git_annex_mode: bool=False, **kwargs) -> Union[str, None]:
    if basename is None:
        basename = os.path.basename(path)
    config = _load_config(**kwargs)
    algorithm = config['algorithm']
    hash0 = _compute_local_file_hash(path, algorithm=algorithm, config=config)
    if not hash0:
        raise Exception('Unable to compute {} hash of file: {}'.format(algorithm, path))
    if not config['use_remote_only']:
        _store_local_file_in_cache(path, algorithm=algorithm, hash=hash0, config=config)
    if (config['use_remote'] or config['use_remote_only']) and (not git_annex_mode):
        _upload_local_file(path, algorithm=algorithm, hash=hash0, config=config)
    return '{}://{}/{}'.format(algorithm, hash0, basename)
    
    
def store_text(text: str, basename: Union[str, None]=None, **kwargs) -> Union[str, None]:
    if basename is None:
        basename = 'file.txt'
    with tempfile.NamedTemporaryFile() as tmpfile:
        with open(tmpfile.name, 'w') as f:
            f.write(text)
        return store_file(tmpfile.name, basename=basename, **kwargs)

def store_object(object: dict, basename: Union[str, None]=None, indent: Union[int, None]=None, **kwargs) -> Union[str, None]:
    if basename is None:
        basename = 'file.json'
    txt = simplejson.dumps(object, indent=indent)
    return store_text(text=txt, basename=basename, **kwargs)

def store_npy(array: np.ndarray, basename: Union[str, None]=None, **kwargs) -> Union[str, None]:
    if basename is None:
        basename = 'file.npy'
    with tempfile.NamedTemporaryFile(suffix='.npy') as tmpfile:
        np.save(tmpfile.name, array)
        return store_file(tmpfile.name, basename=basename, **kwargs)

def store_dir(dirpath: str, label: Union[str, None]=None, git_annex_mode: bool=False, **kwargs):
    config = _load_config(**kwargs)
    if label is None:
        label = os.path.basename(dirpath)
    X = _read_file_system_dir(dirpath, recursive=True, include_hashes=True, store_files=True, git_annex_mode=git_annex_mode, config=config)
    if not X:
        return None
    path1 = store_object(X, config=config)
    assert path1 is not None
    hash0, algorithm = _determine_file_hash_from_url(url=path1, config=config)
    return '{}dir://{}.{}'.format(algorithm, hash0, label)

def read_dir(path: str, *, recursive: bool=True, git_annex_mode: bool=False, **kwargs):
    config = _load_config(**kwargs)
    if _is_hash_url(path):
        protocol, algorithm, hash0, additional_path = _parse_hash_url(path)
        if not protocol.endswith('dir'):
            raise Exception('Not a directory: {}'.format(path))
        dd = load_object('{}://{}'.format(algorithm, hash0), config=config)
        if dd is None:
            return None
        if additional_path:
            list0 = additional_path.split('/')
        else:
            list0 = []
        ii = 0
        while ii < len(list0):
            assert dd is not None
            name0 = list0[ii]
            if name0 in dd['dirs']:
                dd = dd['dirs'][name0]
            elif name0 in dd['files']:
                raise Exception('Not a directory: {}'.format(path))
            else:
                return None
            ii = ii + 1
        if dd:
            if not recursive:
                for dname in dd['dirs']:
                    dd['dirs'][dname] = {}
        return dd
    else:
        return _read_file_system_dir(path, recursive=recursive, include_hashes=True, store_files=False, git_annex_mode=git_annex_mode, config=config)

def _compute_local_file_hash(path: str, *, algorithm: str, config: dict) -> Union[str, None]:
    return _hash_caches[algorithm].computeFileHash(path)

def _store_local_file_in_cache(path: str, *, hash: str, algorithm: str, config: dict) -> None:
    local_path, hash2 = _hash_caches[algorithm].copyFileToCache(path)
    if hash2 is None:
        raise Exception('Unable to store local file in cache: {}'.format(path))

def _find_file_locally(path: str, *, config: dict) -> Tuple[Union[str, None], Union[str, None], Union[str, None]]:
    if _is_hash_url(path):
        hash0, algorithm = _determine_file_hash_from_url(path, config=config)
        if not hash0:
            return None, None, None
        assert algorithm is not None
        ret = _hash_caches[algorithm].findFile(hash=hash0)
        if ret is not None:
            return ret, hash0, algorithm
        else:
            return None, None, None
    elif os.path.isfile(path):
        hash1 = _compute_local_file_hash(path, algorithm=config['algorithm'], config=config)
        return path, hash1, config['algorithm']
    else:
        return None, None, None

def _check_remote_file(hash_url: str, *, config: dict) -> Tuple[Union[str, None], Union[str, None], Union[str, None], Union[int, None]]:
    if _is_hash_url(hash_url):
        hash0, algorithm = _determine_file_hash_from_url(hash_url, config=config)
        if hash0 is None:
            return None, None, None, None
        assert algorithm is not None
        url_check: str = _form_check_url(hash=hash0, algorithm=algorithm, config=config)
        check_resp: dict = _http_get_json(url_check)
        if not check_resp['success']:
            print('Warning: Problem checking for file: ' + check_resp['error'])
            return None, None, None, None
        if check_resp['found']:
            url_download = _form_download_url(hash=hash0, algorithm=algorithm, config=config)
            size = check_resp['size']
            return url_download, algorithm, hash0, size
        else:
            return None, None, None, None
    else:
        raise Exception('Unexpected')

def _form_download_url(*, algorithm: str, hash: str, config: dict) -> str:
    url = config['url']
    channel = config['channel']
    signature = _sha1_of_object(dict(
        algorithm=algorithm,
        hash=hash,
        name='download',
        password=config['password'],
    ))
    return '{}/get/{}/{}?channel={}&signature={}'.format(url, algorithm, hash, channel, signature)

def _form_check_url(*, algorithm: str, hash: str, config: dict) -> str:
    url = config['url']
    channel = config['channel']
    signature = _sha1_of_object(dict(
        algorithm=algorithm,
        hash=hash,
        name='check',
        password=config['password']
    ))
    return '{}/check/{}/{}?channel={}&signature={}'.format(url, algorithm, hash, channel, signature)

def _form_upload_url(*, algorithm: str, hash: str, config: dict) -> str:
    url = config['url']
    channel = config['channel']
    signature = _sha1_of_object(dict(
        algorithm=algorithm,
        hash=hash,
        name='upload',
        password=config['password'],
    ))
    return '{}/set/{}/{}?channel={}&signature={}'.format(url, algorithm, hash, channel, signature)

def _upload_local_file(path: str, *, hash: str, algorithm: str, config: dict) -> None:
    if not config['url']:
        raise Exception('Missing url config parameter for uploading to remote server')
    if not config['channel']:
        raise Exception('Missing channel config parameter for uploading to remote server')
    if not config['password']:
        raise Exception('Missing password config parameter for uploading to remote server')
    size0 = os.path.getsize(path)

    url_ch, algorithm_ch, hash_ch, size_ch = _check_remote_file('{}://{}'.format(algorithm, hash), config=config)
    if url_ch is not None:
        # already on the remote server
        if size_ch != size0:
            raise Exception('Unexpected: size of file on remote server does not match local file {} - {} <> {}'.format(path, size_ch, size0))
        return

    url0 = _form_upload_url(algorithm=algorithm, hash=hash, config=config)
    if size0 > 10000:
        print('Uploading to kachery --- ({}): {} -> {}'.format(_format_file_size(size0), path, url0))

    timer = time.time()
    resp_obj = _http_post_file_data(url0, path)
    elapsed = time.time() - timer

    if size0 > 10000:
        print('File uploaded ({}) in {} sec'.format(
            _format_file_size(size0), elapsed))

    if not resp_obj.get('success', False):
        raise Exception('Problem posting file data: ' + resp_obj.get('error', ''))

def _determine_file_hash_from_url(url: str, *, config: dict) -> Tuple[Union[str, None], Union[str, None]]:
    protocol, algorithm, hash0, additional_path = _parse_hash_url(url)
    if not protocol.endswith('dir'):
        return hash0, algorithm
    dd = load_object('{}://{}'.format(algorithm, hash0))
    if dd is None:
        return None, None
    if additional_path:
        list0 = additional_path.split('/')
    else:
        list0 = []
    ii = 0
    while ii < len(list0):
        assert dd is not None
        name0 = list0[ii]
        if name0 in dd['dirs']:
            dd = dd['dirs'][name0]
        elif name0 in dd['files']:
            if ii + 1 == len(list0):
                hash1 = None
                algorithm1 = None
                for alg in ['sha1', 'md5']:
                    if alg in dd['files'][name0]:
                        hash1 = dd['files'][name0][alg]
                        algorithm1 = alg
                return hash1, algorithm1
            else:
                return None, None
        else:
            return None, None
        ii = ii + 1
    return None, None

def _parse_hash_url(url: str) -> Tuple[str, str, str, str]:
    list0 = url.split('/')
    protocol = list0[0].replace(':', '')
    hash0 = list0[2]
    if '.' in hash0:
        hash0 = hash0.split('.')[0]
    additional_path = '/'.join(list0[3:])
    algorithm = None
    for alg in ['sha1', 'md5']:
        if protocol.startswith(alg):
            algorithm = alg
    if algorithm is None:
        raise Exception('Unexpected protocol: {}'.format(protocol))
    return protocol, algorithm, hash0, additional_path

def _sha1_of_string(txt: str) -> str:
    hh = hashlib.sha1(txt.encode('utf-8'))
    ret = hh.hexdigest()
    return ret


def _sha1_of_object(obj: object) -> str:
    txt = json.dumps(obj, sort_keys=True, separators=(',', ':'))
    return _sha1_of_string(txt)

def _http_get_json(url: str, verbose: Optional[bool]=False, retry_delays: Optional[List[float]]=None) -> dict:
    timer = time.time()
    if retry_delays is None:
        retry_delays = [0.2, 0.5]
    if verbose is None:
        verbose = (os.environ.get('HTTP_VERBOSE', '') == 'TRUE')
    if verbose:
        print('_http_get_json::: ' + url)
    try:
        req = request.urlopen(url)
    except:
        if len(retry_delays) > 0:
            print('Retrying http request in {} sec: {}'.format(
                retry_delays[0], url))
            time.sleep(retry_delays[0])
            return _http_get_json(url, verbose=verbose, retry_delays=retry_delays[1:])
        else:
            return dict(success=False, error='Unable to open url: ' + url)
    try:
        ret = json.load(req)
    except:
        return dict(success=False, error='Unable to load json from url: ' + url)
    if verbose:
        print('Elapsed time for _http_get_json: {} {}'.format(time.time() - timer, url))
    return ret

# thanks: https://stackoverflow.com/questions/1094841/reusable-library-to-get-human-readable-version-of-file-size
def _format_file_size(size: Optional[int]) -> str:
    if not size:
        return 'Unknown'
    if size <= 1024:
        return '{} B'.format(size)
    return _sizeof_fmt(size)


def _sizeof_fmt(num: float, suffix: str='B') -> str:
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f %s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f %s%s" % (num, 'Yi', suffix)

def _http_post_file_data(url: str, fname: str, verbose: Optional[bool]=None) -> dict:
    timer = time.time()
    if verbose is None:
        verbose = (os.environ.get('HTTP_VERBOSE', '') == 'TRUE')
    if verbose:
        print('_http_post_file_data::: ' + fname)
    with open(fname, 'rb') as f:
        try:
            obj = requests.post(url, data=f)
        except:
            raise Exception('Error posting file data.')
    if obj.status_code != 200:
        return dict(
            success=False,
            error='Error posting file data: {} {}'.format(obj.status_code, obj.content.decode('utf-8'))
        )
    if verbose:
        print('Elapsed time for _http_post_file_Data: {}'.format(time.time() - timer))
    return json.loads(obj.content)

def _read_file_system_dir(path: str, *, recursive: bool, include_hashes: bool, store_files: bool, git_annex_mode: bool, config: dict) -> Union[dict, None]:
    ret: dict = dict(
        files={},
        dirs={}
    )
    algorithm = config['algorithm']
    list0 = os.listdir(path)
    for name0 in list0:
        path0 = path + '/' + name0
        if git_annex_mode and os.path.islink(path0) and ('.git/annex/objects' in os.path.realpath(path0)):
            hash1, algorithm1, size1 = _get_info_from_git_annex_link(path0)
            ret['files'][name0] = dict(
                size=size1
            )
            ret['files'][name0][algorithm1] = hash1
        elif os.path.isfile(path0):
            ret['files'][name0] = dict(
                size=os.path.getsize(path0)
            )
            if include_hashes:
                hash1b = _compute_local_file_hash(path0, algorithm=algorithm, config=config)
                ret['files'][name0][algorithm] = hash1b
            if store_files:
                store_file(path0, git_annex_mode=git_annex_mode, config=config)
        elif os.path.isdir(path0):
            ret['dirs'][name0] = {}
            if recursive:
                ret['dirs'][name0] = _read_file_system_dir(
                    path=path0, recursive=recursive, include_hashes=include_hashes, store_files=store_files, git_annex_mode=git_annex_mode, config=config)
    return ret

def _get_info_from_git_annex_link(path) -> Tuple[str, str, int]:
    path1 = os.path.realpath(path)
    # Example: /home/magland/data/najafi-2018-nwb/.git/annex/objects/Gx/pw/MD5E-s167484154--c8bc43bb1868301737797b09266c01a1.mat/MD5E-s167484154--c8bc43bb1868301737797b09266c01a1.mat
    str1 = path1.split('/')[-1]
    # Example: MD5E-s167484154--c8bc43bb1868301737797b09266c01a1.mat
    size0 = int(str1.split('-')[1][1:])
    if str1.split('-')[0] == 'MD5E':
        algorithm0='md5'
    else:
        raise Exception('Unexpected string in _get_info_from_git_annex_link:' + path1)
    hash0 = str1.split('-')[3].split('.')[0]
    return hash0, algorithm0, size0

