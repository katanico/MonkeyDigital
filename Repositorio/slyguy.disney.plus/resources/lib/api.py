import json
import uuid
from time import time

from slyguy import userdata, settings, mem_cache
from slyguy.session import Session
from slyguy.exceptions import Error
from slyguy.util import get_kodi_setting
from slyguy.log import log

from kodi_six import xbmc

from .constants import HEADERS, CONFIG_URL, API_KEY, LANGUAGE_OPTIONS, PROFILE_LANGUAGE, KODI_LANGUAGE
from .language import _

class APIError(Error):
    pass

ERROR_MAP = {
    'not-entitled': _.NOT_ENTITLED,
    'idp.error.identity.bad-credentials': _.BAD_CREDENTIALS,
}

class API(object):
    def new_session(self):
        self.logged_in = False
        self._session  = Session(HEADERS)
        self._set_authentication(userdata.get('access_token'))
        self._set_language()

    @mem_cache.cached(60*60)
    def get_config(self):
        return self._session.get(CONFIG_URL).json()

    def _set_language(self):
        self._language = settings.getEnum('app_language', LANGUAGE_OPTIONS, default=KODI_LANGUAGE)

        if self._language == PROFILE_LANGUAGE:
            self._language = userdata.get('profile_language')

        if not self._language or self._language == KODI_LANGUAGE:
            value = get_kodi_setting('locale.language', default='en')
            value = value.split('.')[-1]
            
            split = value.split('_')
            if len(split) > 1:
                split[1] = split[1].upper()

            self._language = '-'.join(split)

        log.debug("App Language Set to: {}".format(self._language))

    @mem_cache.cached(60*60, key='transaction_id')
    def _transaction_id(self):
        return str(uuid.uuid4())

    @property
    def session(self):
        return self._session
        
    def _set_authentication(self, access_token):
        if not access_token:
            return

        self._session.headers.update({'Authorization': 'Bearer {}'.format(access_token)})
        self._session.headers.update({'x-bamsdk-transaction-id': self._transaction_id()})
        self.logged_in = True

    def _refresh_token(self):
        if userdata.get('expires', 0) > time():
            return

        payload = {
            'refresh_token': userdata.get('refresh_token'),
            'grant_type': 'refresh_token',
            'platform': 'browser',
        }

        self._oauth_token(payload)

    def _oauth_token(self, payload):
        headers = {
            'Authorization': 'Bearer {}'.format(API_KEY),
        }

        endpoint = self.get_config()['services']['token']['client']['endpoints']['exchange']['href']
        token_data = self._session.post(endpoint, data=payload, headers=headers).json()

        if 'errors' in token_data:
            raise APIError(_(_.LOGIN_ERROR, msg=token_data['errors'][0].get('description')))
        elif 'error' in token_data:
            raise APIError(_(_.LOGIN_ERROR, msg=token_data.get('error_description')))

        self._set_authentication(token_data['access_token'])

        userdata.set('access_token', token_data['access_token'])
        userdata.set('expires', int(time() + token_data['expires_in'] - 15))

        if 'refresh_token' in token_data:
            userdata.set('refresh_token', token_data['refresh_token'])

    def login(self, username, password):
        self.logout()

        try:
            self._do_login(username, password)
        except:
            self.logout()
            raise

    def _check_errors(self, data):
        if data.get('errors'):
            error_msg = ERROR_MAP.get(data['errors'][0].get('code')) or _(_.API_ERROR, msg=data['errors'][0].get('description') or data['errors'][0].get('code'))
            raise APIError(error_msg)

        elif data.get('error'):
            error_msg = ERROR_MAP.get(data.get('error_code')) or _(_.API_ERROR, msg=data.get('error_description') or data.get('error_code'))
            raise APIError(error_msg)

    def _do_login(self, username, password):
        headers = {
            'Authorization': 'Bearer {}'.format(API_KEY),
        }

        payload = {
            'deviceFamily': 'android',
            'applicationRuntime': 'android',
            'deviceProfile': 'tv',
            'attributes': {},
        }
    
        endpoint = self.get_config()['services']['device']['client']['endpoints']['createDeviceGrant']['href']
        device_data = self._session.post(endpoint, json=payload, headers=headers).json()

        payload = {
            'subject_token': device_data['assertion'],
            'subject_token_type': 'urn:bamtech:params:oauth:token-type:device',
            'platform': 'android',
            'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
        }

        self._oauth_token(payload)

        payload = {
            'email':    username,
            'password': password,
        }

        endpoint = self.get_config()['services']['bamIdentity']['client']['endpoints']['identityLogin']['href']
        login_data = self._session.post(endpoint, json=payload).json()

        self._check_errors(login_data)

        endpoint = self.get_config()['services']['account']['client']['endpoints']['createAccountGrant']['href']
        grant_data = self._session.post(endpoint, json={'id_token': login_data['id_token']}).json()

        payload = {
            'subject_token': grant_data['assertion'],
            'subject_token_type': 'urn:bamtech:params:oauth:token-type:account',
            'platform': 'android',
            'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
        }

        self._oauth_token(payload)

    def profiles(self):
        self._refresh_token()

        endpoint = self.get_config()['services']['account']['client']['endpoints']['getUserProfiles']['href']
        return self._session.get(endpoint).json()

    def add_profile(self, name, kids=False, avatar=None):
        payload = {
            'attributes': {
                'kidsModeEnabled': bool(kids),
                'languagePreferences': {
                    'appLanguage': self._language,
                    'playbackLanguage': self._language,
                    'subtitleLanguage': self._language,
                },
                'playbackSettings': {
                    'autoplay': True,
                },
            },
            'metadata': None,
            'profileName': name,
        }

        if avatar:
            payload['attributes']['avatar'] = {
                'id': avatar,
                'userSelected': False,
            }

        endpoint = self.get_config()['services']['account']['client']['endpoints']['createUserProfile']['href']
        return self._session.post(endpoint, json=payload).json()

    def delete_profile(self, profile):
        endpoint = self.get_config()['services']['account']['client']['endpoints']['deleteUserProfile']['href'].format(profileId=profile['profileId'])
        return self._session.delete(endpoint)

    def active_profile(self):
        self._refresh_token()

        endpoint = self.get_config()['services']['account']['client']['endpoints']['getActiveUserProfile']['href']
        return self._session.get(endpoint).json()

    def set_profile(self, profile):
        self._refresh_token()

        endpoint   = self.get_config()['services']['account']['client']['endpoints']['setActiveUserProfile']['href'].format(profileId=profile['profileId'])
        grant_data = self._session.put(endpoint).json()

        payload = {
            'subject_token': grant_data['assertion'],
            'subject_token_type': 'urn:bamtech:params:oauth:token-type:account',
            'platform': 'android',
            'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
        }

        self._oauth_token(payload)

        userdata.set('profile_language', profile['attributes']['languagePreferences']['appLanguage'])

    def search(self, query, page=1, page_size=20):
        variables = {
            'preferredLanguage': [self._language],
            'index': 'disney_global',
            'q': query,
            'page': page,
            'pageSize': page_size,
            'contentTransactionId': self._transaction_id(),
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['searchPersisted']['href'].format(queryId='core/disneysearch')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['disneysearch']

    def avatar_by_id(self, ids):
        variables = {
            'preferredLanguage': [self._language],
            'avatarId': ids,
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['searchPersisted']['href'].format(queryId='core/AvatarByAvatarId')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['AvatarByAvatarId']

    def video_bundle(self, family_id):
        variables = {
            'preferredLanguage': [self._language],
            'familyId': family_id,
            'contentTransactionId': self._transaction_id(),
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/DmcVideoBundle')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['DmcVideoBundle']

    def continue_watching(self, family_id):
        variables = {
            'preferredLanguage': [self._language],
            'familyId': family_id,
            'lastBookmark': None,
            'contentTransactionId': self._transaction_id(),
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/ContinueWatchingVideo')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['ContinueWatchingVideo']

    def continue_watching_series(self, series_id):
        variables = {
            'preferredLanguage': [self._language],
            'seriesId': series_id,
            'lastBookmark': None,
            'contentTransactionId': self._transaction_id(),
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/ContinueWatchingSeries')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['ContinueWatchingSeries']

    def series_bundle(self, series_id, page=1, page_size=12):
        variables = {
            'preferredLanguage': [self._language],
            'seriesId': series_id,
            'episodePage': page,
            'episodePageSize': page_size,
            'contentTransactionId': self._transaction_id(),
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/DmcSeriesBundle')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['DmcSeriesBundle']

    def episodes(self, season_ids, page=1, page_size=12):
        variables = {
            'preferredLanguage': [self._language],
            'seasonId': season_ids,
            'episodePage': page,
            'episodePageSize': page_size,
            'contentTransactionId': self._transaction_id(),
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/DmcEpisodes')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['DmcEpisodes']

    def collection_by_slug(self, slug, content_class):
        variables = {
            'preferredLanguage': [self._language],
            'contentClass': content_class,
            'slug': slug,
            'contentTransactionId': self._transaction_id(),
        }

        #endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='disney/CollectionBySlug')
        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/CompleteCollectionBySlug')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['CompleteCollectionBySlug']
        

    def set_by_id(self, set_id, set_type, page=1, page_size=20):
        variables = {
            'preferredLanguage': [self._language],
            'setId': set_id,
            'setType': set_type,
            'page': page,
            'pageSize': page_size,
            'contentTransactionId': self._transaction_id(),
        }

        #endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='disney/SetBySetId')
        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/SetBySetId')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['SetBySetId']

    def add_watchlist(self, content_id):
        variables = {
            'preferredLanguage': [self._language],
            'contentIds': content_id,
        }
        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/AddToWatchlist')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['AddToWatchlist']

    def delete_watchlist(self, content_id):
        variables = {
            'preferredLanguage': [self._language],
            'contentIds': content_id,
        }
        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/DeleteFromWatchlist')
        data = self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['DeleteFromWatchlist']
        xbmc.sleep(500)
        return data

    def videos(self, content_id):
        variables = {
            'preferredLanguage': [self._language],
            'contentId': content_id,
            'contentTransactionId': self._transaction_id(),
        }

        endpoint = self.get_config()['services']['content']['client']['endpoints']['dmcVideos']['href'].format(queryId='core/DmcVideos')
        return self._session.get(endpoint, params={'variables': json.dumps(variables)}).json()['data']['DmcVideos']

    def media_stream(self, playback_url):
        self._refresh_token()

        scenario = self.get_config()['services']['media']['extras']['restrictedPlaybackScenario']

        if xbmc.getCondVisibility('system.platform.android') and settings.getBool('wv_secure', False) and self.get_config()['services']['media']['extras']['isUhdAllowed']:
            scenario = self.get_config()['services']['media']['extras']['playbackScenarioDefault']
            if settings.getBool('h265', False):
                scenario += '-h265'
                if settings.getBool('dolby_vision', False):
                    scenario += '-dovi'
                elif settings.getBool('hdr10', False):
                    scenario += '-hdr10'

        headers = {'accept': 'application/vnd.media-service+json; version=4', 'authorization': userdata.get('access_token')}

        endpoint = playback_url.format(scenario=scenario)
        playback_data = self._session.get(endpoint, headers=headers).json()
        self._check_errors(playback_data)

        return playback_data['stream']['complete']

    def logout(self):
        userdata.delete('access_token')
        userdata.delete('expires')
        userdata.delete('refresh_token')
        mem_cache.delete('transaction_id')
        
        self.new_session()