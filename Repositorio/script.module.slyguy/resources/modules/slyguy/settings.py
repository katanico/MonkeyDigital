import json

from kodi_six import xbmcaddon

from .constants import ADDON_ID

def open():
    xbmcaddon.Addon(ADDON_ID).openSettings()

def getDict(key, default=None):
    try:
        return json.loads(get(key))
    except:
        return default

def setDict(key, value):
    set(key, json.dumps(value))

def getInt(key, default=None):
    try:
        return int(get(key))
    except:
        return default

def getFloat(key, default=None):
    try:
        return float(get(key))
    except:
        return default

def setInt(key, value):
    set(key, int(value))

def getBool(key, default=False):
    value = get(key).lower()
    if not value:
        return default
    else:
        return value == 'true'

def getEnum(key, choices=None, default=None):
    index = getInt(key)
    if index == None or not choices:
        return default

    try:
        return choices[index]
    except KeyError:
        return default

def remove(key):
    set(key, '')

def setBool(key, value=True):
    set(key, 'true' if value else 'false')

def get(key, default=''):
    return xbmcaddon.Addon(ADDON_ID).getSetting(key) or default

def set(key, value=''):
    xbmcaddon.Addon(ADDON_ID).setSetting(key, str(value))

class Settings(object):
    _fresh = False

    @property
    def fresh(self):
        return self._fresh

    def __init__(self, addon_id=ADDON_ID):
        self._addon_id = addon_id
        
        self._fresh = self.getBool('_fresh', True)
        if self._fresh:
            self.setBool('_fresh', False)

    def open(self):
        xbmcaddon.Addon(self._addon_id).openSettings()

    def getDict(self, key, default=None):
        try:
            return json.loads(self.get(key))
        except:
            return default

    def setDict(self, key, value):
        self.set(key, json.dumps(value))

    def getInt(self, key, default=None):
        try:
            return int(self.get(key))
        except:
            return default

    def getFloat(self, key, default=None):
        try:
            return float(self.get(key))
        except:
            return default

    def setInt(self, key, value):
        self.set(key, int(value))

    def getBool(self, key, default=False):
        value = self.get(key).lower()
        if not value:
            return default
        else:
            return value == 'true'

    def getEnum(self, key, choices=None, default=None):
        index = self.getInt(key)
        if index == None or not choices:
            return default

        try:
            return choices[index]
        except KeyError:
            return default

    def remove(self, key):
        self.set(key, '')

    def setBool(self, key, value=True):
        self.set(key, 'true' if value else 'false')

    def get(self, key, default=''):
        return xbmcaddon.Addon(self._addon_id).getSetting(key) or default

    def set(self, key, value=''):
        xbmcaddon.Addon(self._addon_id).setSetting(key, str(value))

FRESH = getBool('_fresh', True)
if FRESH:
    setBool('_fresh', False)