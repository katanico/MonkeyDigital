import os
import json
from time import time
from threading import Thread

from kodi_six import xbmc, xbmcaddon

from slyguy import userdata, gui, router, inputstream
from slyguy.session import Session
from slyguy.util import hash_6, kodi_rpc, set_kodi_setting
from slyguy.log import log
from slyguy.constants import ROUTE_SERVICE, ROUTE_SERVICE_INTERVAL

from .proxy import Proxy
from .language import _
from .constants import NEWS_URL, NEWS_CHECK_TIME, NEWS_MAX_TIME, SERVICE_BUILD_TIME

monitor = xbmc.Monitor()

def _check_news():
    _time = int(time())

    if _time < userdata.get('last_news_check', 0) + NEWS_CHECK_TIME:
        return

    userdata.set('last_news_check', _time)

    news = Session().get(NEWS_URL).json()
    if not news:
        return

    if 'id' not in news or news['id'] == userdata.get('last_news_id'):
        return

    userdata.set('last_news_id', news['id'])

    if _time > news.get('timestamp', _time) + NEWS_MAX_TIME:
        log.debug("news is too old to show")
        return

    if news['type'] == 'addon_release':
        try: addon = xbmcaddon.Addon(news['addon_id'])
        except: addon = None

        if addon:
            log.debug('addon_release {} already installed'.format(news['addon_id']))
            return

        def _interact_thread():
            if gui.yes_no(news['message'], news.get('heading', _.NEWS_HEADING)):
                xbmc.executebuiltin('InstallAddon({})'.format(news['addon_id']), True)
                kodi_rpc('Addons.SetAddonEnabled', {'addonid': news['addon_id'], 'enabled': True})

                try: addon = xbmcaddon.Addon(news['addon_id'])
                except: return

                url = router.url_for('', _addon_id=news['addon_id'])
                xbmc.executebuiltin('ActivateWindow(Videos,{})'.format(url))

        thread = Thread(target=_interact_thread)
        thread.daemon = True
        thread.start()

services = {}
last_build = 0
def _build_services():
    global last_build

    _time = int(time())

    if _time < last_build + SERVICE_BUILD_TIME:
        return

    last_build = _time

    data = kodi_rpc('Addons.GetAddons', {'installed': True, 'enabled': True, 'type': 'xbmc.python.pluginsource'})

    for row in data['addons']:
        addon        = xbmcaddon.Addon(row['addonid'])
        addon_path   = xbmc.translatePath(addon.getAddonInfo('path'))
        service_path = os.path.join(addon_path, '.slyguy_service')

        if not os.path.exists(service_path):
            continue
        
        try:
            with open(service_path) as f:
                service_data = json.load(f)
        except:
            service_data = {}

        default_every = ROUTE_SERVICE_INTERVAL
        default_path  = router.url_for(ROUTE_SERVICE, _addon_id=row['addonid'])
        
        data = {
            'every': int(service_data.get('every', default_every)),
            'path': service_data.get('path', default_path).replace('$ID', row['addonid'])
        }

        if row['addonid'] in services:
            services[row['addonid']].update(data)
        else:
            services[row['addonid']] = data

    log.debug('Loaded Services: {}'.format(services))

def _check_services():
    _time = int(time())

    for addon_id in services:
        if monitor.abortRequested():
            break
        
        data = services[addon_id]
        
        if _time < data.get('last_run', 0) + data['every']:
            continue

        # make sure enabled / installed
        try: addon = xbmcaddon.Addon(addon_id)
        except: continue

        data['last_run'] = _time
        xbmc.executebuiltin('XBMC.RunPlugin({})'.format(data['path']))
        log.debug('Service Started: {}'.format(data['path']))

        #delay 1 seconds between each service start
        monitor.waitForAbort(1)

def start():
    log.debug('Shared Service: Started')

    proxy = Proxy()
    proxy.start()

    ## If kodi crashed, we have not reverted the IA settings - so lets do that
    reset_settings = userdata.get('reset_settings')

    if reset_settings:
        if reset_settings[1]:
            inputstream.set_settings(reset_settings[2])
        else:
            for key in reset_settings[2]:
                set_kodi_setting(key, reset_settings[2][key])

        userdata.delete('reset_settings')
        log.debug('Reset Settings after Crash: DONE')

    ## Inital wait on boot
    monitor.waitForAbort(5)

    try:
        while not monitor.abortRequested():
            try: _check_news()
            except Exception as e: log.exception(e)

            try: _build_services()
            except Exception as e: log.exception(e)

            try: _check_services()
            except Exception as e: log.exception(e)

            if monitor.waitForAbort(5):
                break
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception(e)

    proxy.stop()
    log.debug('Shared Service: Stopped')