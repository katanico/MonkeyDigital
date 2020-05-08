import os
import time
import hashlib
import shutil
import platform
import base64
import struct
import json

from kodi_six import xbmc

from .language import _
from .log import log
from .exceptions import Error

def jwt_data(token):
    b64_string = token.split('.')[1]
    b64_string += "=" * ((4 - len(b64_string) % 4) % 4) #fix padding
    return json.loads(base64.b64decode(b64_string))

def get_kodi_setting(key, default=None):
    data = kodi_rpc('Settings.GetSettingValue', {'setting': key})
    return data.get('value', default)

def set_kodi_setting(key, value):
    return kodi_rpc('Settings.SetSettingValue', {'setting': key, 'value': value})

def kodi_rpc(method, params=None, raise_on_error=False):
    try:
        payload = {'jsonrpc':'2.0', 'id':1}
        payload.update({'method': method})
        if params:
            payload['params'] = params

        data = json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
        if 'error' in data:
            raise Exception('Kodi RPC "{} {}" returned Error: "{}"'.format(method, params or '', data['error'].get('message')))

        return data['result']
    except Exception as e:
        log.exception(e)
        if raise_on_error:
            raise
        else:
            return {}

def remove_file(file_path):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except:
        return False
    else:
        return True

def hash_6(value, default=None):
    if not value:
        return default

    h = hashlib.md5(str(value).encode('utf8'))
    return base64.b64encode(h.digest()).decode('utf8')[:6]

def md5sum(filepath):
    if not os.path.exists(filepath):
        return None

    return hashlib.md5(open(filepath,'rb').read()).hexdigest()

def process_brightcove(data):
    if type(data) != dict:
        try:
            if data[0]['error_code'].upper() == 'CLIENT_GEO' or data[0].get('client_geo','').lower() == 'anonymizing_proxy':
                raise Error(_.GEO_ERROR)
            else:
                msg = data[0].get('message', data[0]['error_code'])
        except KeyError:
            msg = _.NO_ERROR_MSG

        raise Error(_(_.NO_BRIGHTCOVE_SRC, error=msg))

    sources = []

    for source in data.get('sources', []):
        if not source.get('src'):
            continue

        # HLS
        if source.get('type') == 'application/x-mpegURL' and 'key_systems' not in source:
            sources.append({'source': source, 'type': 'hls', 'order_1': 1, 'order_2': int(source.get('ext_x_version', 0))})

        # MP4
        elif source.get('container') == 'MP4' and 'key_systems' not in source:
            sources.append({'source': source, 'type': 'mp4', 'order_1': 2, 'order_2': int(source.get('avg_bitrate', 0))})

        # Widevine
        elif source.get('type') == 'application/dash+xml' and 'com.widevine.alpha' in source.get('key_systems', ''):
            sources.append({'source': source, 'type': 'widevine', 'order_1': 3, 'order_2': 0})

        elif source.get('type') == 'application/vnd.apple.mpegurl' and 'key_systems' not in source:
            sources.append({'source': source, 'type': 'hls', 'order_1': 1, 'order_2': 0})

    if not sources:
        raise Error(_.NO_BRIGHTCOVE_SRC)

    sources = sorted(sources, key = lambda x: (x['order_1'], -x['order_2']))
    source = sources[0]

    from . import plugin, inputstream

    if source['type'] == 'mp4':
        return plugin.Item(
            path = source['source']['src'],
            art = False,
        )
    elif source['type'] == 'hls':
        return plugin.Item(
            path = source['source']['src'],
            inputstream = inputstream.HLS(),
            art = False,
        )
    elif source['type'] == 'widevine':
        return plugin.Item(
            path = source['source']['src'],
            inputstream = inputstream.Widevine(license_key=source['source']['key_systems']['com.widevine.alpha']['license_url']),
            art = False,
        )
    else:
        raise Error(_.NO_BRIGHTCOVE_SRC)

def get_system_arch():
    if xbmc.getCondVisibility('System.Platform.UWP') or '4n2hpmxwrvr6p' in xbmc.translatePath('special://xbmc/'):
        system = 'UWP'
    elif xbmc.getCondVisibility('System.Platform.Android'):
        system = 'Android'
    elif xbmc.getCondVisibility('System.Platform.IOS'):
        system = 'IOS'
    else:
        system = platform.system()
    
    if system == 'Windows':
        arch = platform.architecture()[0]
    else:
        try:
            arch = platform.machine()
        except:
            arch = ''

    #64bit kernel with 32bit userland
    if ('aarch64' in arch or 'arm64' in arch) and (struct.calcsize("P") * 8) == 32:
        arch = 'armv7'

    elif 'arm' in arch:
        if 'v6' in arch:
            arch = 'armv6'
        else:
            arch = 'armv7'
            
    elif arch == 'i686':
        arch = 'i386'

    return system, arch

def strip_namespaces(tree):
    for el in tree.iter():
        tag = el.tag
        if tag and isinstance(tag, str) and tag[0] == '{':
            el.tag = tag.partition('}')[2]
        attrib = el.attrib
        if attrib:
            for name, value in list(attrib.items()):
                if name and isinstance(name, str) and name[0] == '{':
                    del attrib[name]
                    attrib[name.partition('}')[2]] = value