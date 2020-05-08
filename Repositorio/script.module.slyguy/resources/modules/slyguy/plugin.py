import sys
import re
import shutil
import random
import time

from functools import wraps

from kodi_six import xbmc, xbmcplugin

from . import router, gui, settings, userdata, inputstream, signals, quality_player, migrate
from .constants import ROUTE_SETTINGS, ROUTE_RESET, ROUTE_SERVICE, ROUTE_SERVICE_INTERVAL, ROUTE_CLEAR_CACHE, ROUTE_IA_SETTINGS, ROUTE_IA_INSTALL, ADDON_ICON, ADDON_FANART, ADDON_ID, ADDON_NAME, ROUTE_AUTOPLAY_TAG, ADDON_PROFILE, QUALITY_TAG, ROUTE_MIGRATE_DONE
from .log import log
from .language import _
from .exceptions import PluginError, FailedPlayback

## SHORTCUTS
url_for         = router.url_for
dispatch        = router.dispatch
############

def exception(msg=''):
    raise PluginError(msg)

logged_in   = False

class Redirect(object):
    def __init__(self, location):
        self.location = location

# @plugin.login_required()
def login_required():
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not logged_in:
                raise PluginError(_.PLUGIN_LOGIN_REQUIRED)

            return f(*args, **kwargs)
        return decorated_function
    return lambda f: decorator(f)

# @plugin.route()
def route(url=None):
    def decorator(f, url):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            item = f(*args, **kwargs)

            pattern = kwargs.get(ROUTE_AUTOPLAY_TAG, None)

            if pattern is not None and isinstance(item, Folder):
                _autoplay(item, pattern)
            elif isinstance(item, Folder):
                item.display()
            elif isinstance(item, Item):
                item.play(quality=kwargs.get(QUALITY_TAG))
            elif isinstance(item, Redirect):
                if _handle() > 0:
                    xbmcplugin.endOfDirectory(_handle(), succeeded=True, updateListing=True, cacheToDisc=True)
                    
                gui.redirect(item.location)
            else:
                resolve()

        router.add(url, decorated_function)
        return decorated_function
    return lambda f: decorator(f, url)

# @plugin.merge()
def merge():
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            xbmc.executebuiltin('Skin.SetString(merge,started)')

            try:
                result = f(*args, **kwargs)
            except Exception as e:
                xbmc.executebuiltin('Skin.SetString(merge,error)')
                log.exception(e)
            else:
                xbmc.executebuiltin('Skin.SetString(merge,ok)')
                return result
                
        return decorated_function
    return lambda f: decorator(f)

def resolve(error=False):
    handle = _handle()
    if handle > 0:
        if error and '_play=1' in sys.argv[2]:
            _failed_playback()
        else:
            xbmcplugin.endOfDirectory(handle, succeeded=False, updateListing=False, cacheToDisc=False)

@signals.on(signals.ON_ERROR)
def _error(e):
    if not e.message:
        signals.emit(signals.ON_EXCEPTION, e)
        return

    _close()

    log.debug(e, exc_info=True)
    gui.ok(e.message, heading=e.heading)
    resolve(error=True)

@signals.on(signals.ON_EXCEPTION)
def _exception(e):
    _close()

    if type(e) == FailedPlayback:
        _failed_playback()
        return

    log.exception(e)
    gui.exception()
    resolve(error=True)

@route('')
def _home(**kwargs):
    raise PluginError(_.PLUGIN_NO_DEFAULT_ROUTE)

@route(ROUTE_IA_SETTINGS)
def _ia_settings(**kwargs):
    _close()
    inputstream.open_settings()

@route(ROUTE_IA_INSTALL)
def _ia_install(**kwargs):
    _close()
    inputstream.install_widevine(reinstall=True)

@route(ROUTE_MIGRATE_DONE)
def _migrate_done(old_addon_id, **kwargs):
    _close()
    migrate.migrate_done(old_addon_id)

def reboot():
    _close()
    xbmc.executebuiltin('Reboot')

@signals.on(signals.AFTER_DISPATCH)
def _close():
    signals.emit(signals.ON_CLOSE)

@route(ROUTE_SETTINGS)
def _settings(**kwargs):
    _close()
    settings.open()
    gui.refresh()


@route(ROUTE_RESET)
def _reset(**kwargs):
    if not gui.yes_no(_.PLUGIN_RESET_YES_NO):
        return

    _close()

    try:
        xbmc.executeJSONRPC('{{"jsonrpc":"2.0","id":1,"method":"Addons.SetAddonEnabled","params":{{"addonid":"{}","enabled":false}}}}'.format(ADDON_ID))
        shutil.rmtree(ADDON_PROFILE)
    except:
        pass
        
    xbmc.executeJSONRPC('{{"jsonrpc":"2.0","id":1,"method":"Addons.SetAddonEnabled","params":{{"addonid":"{}","enabled":true}}}}'.format(ADDON_ID))

    gui.notification(_.PLUGIN_RESET_OK)
    signals.emit(signals.AFTER_RESET)
    gui.refresh()

@route(ROUTE_SERVICE)
def _service(**kwargs):
    try:
        signals.emit(signals.ON_SERVICE)
    except Exception as e:
        #catch all errors so dispatch doesn't show error
        log.exception(e)

def service(interval=ROUTE_SERVICE_INTERVAL):
    monitor = xbmc.Monitor()

    delay = settings.getInt('service_delay', 0) or random.randint(10, 60)
    monitor.waitForAbort(delay)

    last_run = 0
    while not monitor.abortRequested():
        if time.time() - last_run >= interval:
            
            try:
                signals.emit(signals.ON_SERVICE)
            except Exception as e:
                #catch all errors so dispatch doesn't show error
                log.exception(e)

            last_run = time.time()
            
        monitor.waitForAbort(5)

def _handle():
    try:
        return int(sys.argv[1])
    except:
        return -1

def _autoplay(folder, pattern):
    choose = None

    if '#' in pattern:
        pattern, choose = pattern.lower().split('#')

    try: 
        choose = int(choose)
    except ValueError:
        if choose != 'random':
            choose = 'choose'

    log.debug('Auto Play: "{}" item that label matches "{}"'.format(choose, pattern))

    matches = []
    for item in folder.items:
        if not item or not item.playable:
            continue

        if re.search(pattern, item.label, re.IGNORECASE):
            matches.append(item)
            log.debug('#{} Match: {}'.format(len(matches)-1, item.label))
    
    if not matches:
        selected = None

    elif isinstance(choose, int):
        try:
            selected = matches[choose]
        except IndexError:
            selected = None

    elif len(matches) == 1:
        selected = matches[0]

    elif choose == 'random':
        selected = random.choice(matches)

    else:
        index = gui.select(folder.title, options=matches, autoclose=10000, preselect=0, useDetails=True)
        if index < 0:
            return resolve()

        selected = matches[index]

    if not selected:
        raise PluginError(_(_.NO_AUTOPLAY_FOUND, pattern=pattern, choose=choose))

    log.debug('"{}" item selected "{}"'.format(choose, selected.label))

    return router.redirect(selected.path)

def _failed_playback():
    handle = _handle()
    xbmcplugin.setResolvedUrl(handle, False, Item(path='http://').get_li())
    xbmcplugin.endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=False)
    # xbmc.PlayList(xbmc.PLAYLIST_MUSIC).clear()
    # xbmc.PlayList(xbmc.PLAYLIST_VIDEO).clear()

default_thumb  = ADDON_ICON
default_fanart = ADDON_FANART

#Plugin.Item()
class Item(gui.Item):
    def __init__(self, cache_key=None, playback_error=None, *args, **kwargs):
        super(Item, self).__init__(self, *args, **kwargs)
        self.cache_key = cache_key
        self.playback_error = playback_error

    def get_li(self):
        # if settings.getBool('use_cache', True) and self.cache_key:
        #     url = url_for(ROUTE_CLEAR_CACHE, key=self.cache_key)
        #     self.context.append((_.PLUGIN_CONTEXT_CLEAR_CACHE, 'XBMC.RunPlugin({})'.format(url)))

        if not self.playable:
            self.art['thumb']  = self.art.get('thumb') or default_thumb
            self.art['fanart'] = self.art.get('fanart') or default_fanart

        quality_player.add_context(self)

        return super(Item, self).get_li()

    def play(self, quality=None):
        self.playable = True

        try:
            if sys.argv[3] == 'resume:true':
                self.properties.pop('ResumeTime', None)
                self.properties.pop('TotalTime', None)
        except:
            pass

        # Move quality select etc into proxy server
        # if quality:
        #     self.use_proxy = True
        #     self.headers['_quality'] = quality
        quality_player.parse(self, quality=quality)

        li     = self.get_li()
        handle = _handle()

        if handle > 0:
            xbmcplugin.setResolvedUrl(handle, True, li)
        else:
            xbmc.Player().play(self.path, li)

#Plugin.Folder()
class Folder(object):
    def __init__(self, title=None, items=None, content='episodes', updateListing=False, cacheToDisc=True, sort_methods=None, thumb=None, fanart=None, no_items_label=_.NO_ITEMS, no_items_method='dialog'):
        self.title = title
        self.items = items or []
        self.content = content
        self.updateListing = updateListing
        self.cacheToDisc = cacheToDisc
        self.sort_methods = sort_methods or [xbmcplugin.SORT_METHOD_UNSORTED, xbmcplugin.SORT_METHOD_LABEL, xbmcplugin.SORT_METHOD_DATEADDED]
        self.thumb = thumb
        self.fanart = fanart
        self.no_items_label = no_items_label
        self.no_items_method = no_items_method

    def display(self):
        handle = _handle()
        items  = [i for i in self.items if i]

        if not items and self.no_items_label:
            label = _(self.no_items_label, _label=True)
            
            if self.no_items_method == 'dialog':
                gui.ok(label, heading=self.title)
                return resolve()
            else:
                items.append(Item(
                    label = label, 
                    is_folder = False,
                ))

        for item in items:
            if self.thumb and not item.art.get('thumb'):
                item.art['thumb'] = self.thumb

            if self.fanart and not item.art.get('fanart'):
                item.art['fanart'] = self.fanart

            li = item.get_li()
            xbmcplugin.addDirectoryItem(handle, item.path, li, item.is_folder)

        if self.content: xbmcplugin.setContent(handle, self.content)
        if self.title: xbmcplugin.setPluginCategory(handle, self.title)

        for sort_method in self.sort_methods:
            xbmcplugin.addSortMethod(handle, sort_method)

        xbmcplugin.endOfDirectory(handle, succeeded=True, updateListing=self.updateListing, cacheToDisc=self.cacheToDisc)

    def add_item(self, *args, **kwargs):
        position = kwargs.pop('_position', None)
        
        item = Item(*args, **kwargs)
        
        if position == None:
            self.items.append(item)
        else:
            self.items.insert(int(position), item)

        return item

    def add_items(self, items):
        if items is None:
            return

        if isinstance(items, list):
            self.items.extend(items)
        elif isinstance(items, Item):
            self.items.append(items)
        else:
            raise Exception('add_items only accepts an Item or list of Items')