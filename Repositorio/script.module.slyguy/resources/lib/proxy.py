import threading
import os
import shutil
import re
import uuid

from io import BytesIO, SEEK_END
from xml.dom.minidom import parseString
from collections import defaultdict

from six.moves.BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from six.moves.socketserver import ThreadingMixIn
from six.moves.urllib.parse import urlparse, urljoin
from kodi_six import xbmc
from requests import Session

from slyguy.log import log
from slyguy.constants import ADDON_DEV
from slyguy import settings
#from slyguy.session import Session

from .constants import PROXY_CACHE, PROXY_CACHE_AHEAD, PROXY_CACHE_BEHIND

patterns = {}
sessions = {}
PROXY_HEADERS  = ['_proxy_default_language']
REMOVE_HEADERS = ['connection', 'transfer-encoding', 'content-encoding', 'date', 'server', 'content-length', 'location']

HOST = settings.get('proxy_host')
PORT = settings.getInt('proxy_port')
PROXY_PATH = 'http://{}:{}/'.format(HOST, PORT)

#IS_ANDROID = xbmc.getCondVisibility('system.platform.android')

def devlog(msg):
    if ADDON_DEV:
        log.debug(msg)

class ResponseStream(object):
    def __init__(self, response, chunk_size=None):
        self._bytes = BytesIO()
        self._response = response
        self._iterator = response.iter_content(chunk_size)
        self._chunk_size = chunk_size

    def _load_until(self, goal_position=None):
        current_position = self._bytes.seek(0, SEEK_END)

        while goal_position is None or current_position < goal_position:
            try:
                current_position = self._bytes.write(next(self._iterator))
            except StopIteration:
                break

    @property
    def size(self):
        return self._size

    def tell(self):
        return self._bytes.tell()

    def read(self, size=None, start_from=None):
        if size is None:
            left_off_at = start_from or 0
            goal_position = None
        else:
            left_off_at = start_from or self._bytes.tell()
            goal_position = left_off_at + size
        
        self._load_until(goal_position)
        self._bytes.seek(left_off_at)

        return self._bytes.read(size)

    def set(self, _bytes):
        self._bytes = BytesIO(_bytes)

    def iter_content(self):
        self._bytes.seek(0)

        while True:
            chunk = self._bytes.read(self._chunk_size)
          #  chunk = self.read(self._chunk_size)
            if not chunk:
                break

            yield chunk
            
        for chunk in self._iterator:
            yield chunk

class RequestHandler(BaseHTTPRequestHandler):
    cached = {}

    def __init__(self, request, client_address, server):
        self._chunk_size = 4096
        BaseHTTPRequestHandler.__init__(self, request, client_address, server)

    def log_message(self, format, *args):
        return

    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        self.request.settimeout(5)

    def do_GET(self):
        url = self.path.strip('/').strip('\\')

        # if self._check_cache(self.cached, url):
        #     return
        #     print("cached")
        #     self._output_response(self.cached[url])
        #     return
        
        if 'http' not in url:
            self.send_error(404)
            return

        devlog('GET IN: {}'.format(url))
        # if self._search_patterns(url):
        #     return

        response = self._proxy_request('GET', url)
        if not response.ok:
            self._output_response(response)
            return

        first_chunk = response.stream.read(self._chunk_size, start_from=0)

        if b'urn:mpeg:dash:schema' in first_chunk.lower():
            self._parse_dash(response)
        elif b'#extm3u' in first_chunk.lower():
            self._parse_m3u8(response)

        self._output_response(response)
       # filepath = self._cache(response, output=True)
       # self.cached[url] = {'headers': response.headers, 'file_path': filepath}

    def _search_patterns(self, url):
        for key in patterns:
            pattern = patterns[key]
            for key2 in pattern:
                match = pattern[key2]['pattern'].match(url)
                if match:
                    return self._download_segment(pattern[key2], match.groupdict())

        return False

    def _check_cache(self, cache, key):
        cached = cache.get(key)

        try:
            if not cached or not os.path.exists(cached['file_path']):
                return False
        except:
            return False

        devlog('Cache Hit')

        self.send_response(200)

        for key in cached['headers']:
            self.send_header(key, cached['headers'][key])

        self.end_headers()

        with open(cached['file_path'], 'rb') as f:
            shutil.copyfileobj(f, self.wfile, length=self._chunk_size)

        return True

    def _download_segment(self, pattern, params):
        params['Number'] = int(params.get('Number', -1))
        if self._check_cache(pattern['cached'], params['Number']):
            return True

        seg_url = pattern['template'] % params

        if seg_url.startswith('http'):
            response = self._proxy_request('GET', seg_url)
        else:
            good_base = pattern['base_urls'][0]

            for base_url in pattern['base_urls']:
                response = self._proxy_request('GET', urljoin(base_url, seg_url))
                if response.ok:
                    good_base = base_url
                    break

            pattern['base_urls'].remove(good_base)
            pattern['base_urls'].insert(0, good_base)
            
        if params['Number'] > -1 and response.ok and PROXY_CACHE_BEHIND > 0:
            file_path = self._cache(response, output=True)  #if want to cache what we have watched (maybe cache either side of current segment for nice rewind)
            pattern['cached'][params['Number']] = {'file_path': file_path, 'headers': response.headers}
        else:
            self._output_response(response)
        
        return True
                
    def _parse_dash(self, response):
        try:
            root = parseString(response.stream.read())
        except:
            log.debug("Failed to parse MPD")
            return self._output_response(response)

        mpd = root.getElementsByTagName("MPD")[0]

        if response.url not in patterns:
            patterns[response.url] = {}

        base_urls      = []
        base_url_nodes = []

        for node in mpd.childNodes:
            if node.nodeType == node.ELEMENT_NODE:
                if node.localName == 'BaseURL':
                    url = node.firstChild.nodeValue

                    if not url.startswith('http'):
                        url = urljoin(response.url, url)

                    base_urls.append(url)
                    base_url_nodes.append(node)
                    node.firstChild.nodeValue = PROXY_PATH + url

        if not base_urls:
            base_urls = [response.url]

        ### SKY GO FIX
        if 'availabilityStartTime' in mpd.attributes.keys():
            mpd.removeAttribute('availabilityStartTime')
        ##############

        # Keep first base_url node
        if base_url_nodes:
            base_url_nodes.pop(0)
            for e in base_url_nodes:
                mpd.removeChild(e)
        ####################

        ## SORT MULTI VIDEO ADAPTION SETS BY BITRATE ##
        video_sets = []

        for elem in root.getElementsByTagName('AdaptationSet'):
            if elem.getAttribute('contentType').lower() != 'video':
                continue

            parent = elem.parentNode
            parent.removeChild(elem)
            highest_bitrate = 0

            for repr_elem in elem.getElementsByTagName('Representation'):
                bitrate = int(repr_elem.getAttribute('bandwidth') or 0)
                if bitrate > highest_bitrate:
                    highest_bitrate = bitrate

            video_sets.append([highest_bitrate, elem, parent])

        for elem in sorted(video_sets, key=lambda  x: x[0], reverse=True):
            elem[2].appendChild(elem[1])

        if len(video_sets) > 1:
            devlog("Video Adaption Sets Sorted")
        ##################

        elems = root.getElementsByTagName('SegmentTemplate')
        elems.extend(root.getElementsByTagName('SegmentURL'))

        for e in elems:
            def process_attrib(attrib):
                if attrib not in e.attributes.keys():
                    return

                url = e.getAttribute(attrib)

                if url.startswith('http'):
                    e.setAttribute(attrib, PROXY_PATH + url)
                    pattern = '^' + re.escape(url)
                else:
                    pattern = '.*' + re.escape(urljoin('.', url))

                pattern = pattern.replace('\$RepresentationID\$', '(?P<RepresentationID>.+?)')
                pattern = re.sub(r'\\\$Number.*?\\\$', '(?P<Number>[0-9]+?)', pattern)
                pattern += '$'
                pattern = re.compile(pattern)

                template = url.replace('$RepresentationID$', '%(RepresentationID)s')
                match    = re.search('(\$Number(.*?)\$)', template)

                if match:
                    if match.group(2):
                        template = template.replace(match.group(0), match.group(2).replace('%', '%(Number)'))
                    else:
                        template = template.replace(match.group(0), '%(Number)d')

                patterns[response.url][url] = {'pattern': pattern, 'template': template, 'cached': {}, 'base_urls': base_urls}

            process_attrib('initialization')
            process_attrib('media')

        response.stream.set(root.toxml(encoding='utf-8'))

    def _default_audio_fix(self, m3u8):
        if '#EXT-X-MEDIA' not in m3u8:
            return m3u8

        def _process_media(line):
            attribs = {}

            for key, value in re.findall('([\w-]+)="?([^",]*)[",$]?', line):
                attribs[key.upper()] = value.strip()

            return attribs

        default_groups = []
        audio_groups = defaultdict(list)
        for line in m3u8.splitlines():
            if line.startswith('#EXT-X-MEDIA'):
                attribs = _process_media(line)

                # FIX es-ES fr-FR languages #
                language = attribs.get('LANGUAGE')
                if language:
                    split = language.split('-')
                    if len(split) > 1 and split[1].lower() == split[0].lower():
                        attribs['LANGUAGE'] = split[0]
                #############################

                if attribs.get('TYPE') == 'AUDIO':
                    audio_groups[attribs['GROUP-ID']].append([attribs, line])
                    if attribs.get('DEFAULT') == 'YES' and attribs['GROUP-ID'] not in default_groups:
                        default_groups.append(attribs['GROUP-ID'])

        default_language = self.headers.get('_proxy_default_language')

        if default_language:
            for group_id in audio_groups:
                if group_id in default_groups:
                    continue

                languages = []
                for group in audio_groups[group_id]:
                    attribs, line = group

                    attribs['AUTOSELECT'] = 'NO'
                    attribs['DEFAULT']    = 'NO'

                    if attribs['LANGUAGE'] not in languages:
                        attribs['AUTOSELECT'] = 'YES'

                        if attribs['LANGUAGE'] == default_language:
                            attribs['DEFAULT'] = 'YES'

                        languages.append(attribs['LANGUAGE'])

        for group_id in audio_groups:
            for group in audio_groups[group_id]:
                attribs, line = group

                new_line = '#EXT-X-MEDIA:'
                for key in attribs:
                    new_line += u'{}="{}",'.format(key, attribs[key])

                new_line = new_line.rstrip(',')
                m3u8 = m3u8.replace(line, new_line)

        return m3u8

    def _parse_m3u8(self, response):
        m3u8 = response.stream.read().decode('utf8')

        try:
            m3u8 = self._default_audio_fix(m3u8)
        except Exception as e:
            log.exception(e)
        else:
            log.debug('Proxy: Default audio fixed')

        m3u8 = re.sub(r'(https?)://', r'{}\1://'.format(PROXY_PATH), m3u8, flags=re.I)

        response.stream.set(m3u8.encode('utf8'))

    def _cache(self, response, output=False):
        if output:
            self._output_headers(response)

        filepath = os.path.join(PROXY_CACHE, str(uuid.uuid4()))

        try:
            f = open(filepath, 'wb')
        except:
            f = None

        for chunk in response.stream.iter_content():
            if output:
                try: 
                    self.wfile.write(chunk)
                except ConnectionResetError:
                    f = None
                    break
            
            if f:
                try: f.write(chunk)
                except: f = None

            elif not output:
                break

        if not f:
            devlog('Cache failed')
            try: os.remove(filepath)
            except: pass
            return False

        devlog('Cached!')
        return filepath

    def _proxy_request(self, method, url):
        parsed = urlparse(url)

        headers = {}
        for header in self.headers:
            headers[header.lower()] = self.headers[header]

        headers['host'] = parsed.hostname

        for key in PROXY_HEADERS:
            headers.pop(key, None)

        length    = int(headers.get('content-length', 0))
        post_data = self.rfile.read(length) if length else None

        session = sessions.get(headers['host'], Session())
        sessions[headers['host']] = session

        devlog('{} OUT: {}'.format(method.upper(), url))
            
        response = session.request(method=method, url=url, headers=headers, data=post_data, allow_redirects=False, stream=True)
        response.stream = ResponseStream(response, self._chunk_size)

        headers = {}
        for header in response.headers:
            headers[header.lower()] = response.headers[header]

        for header in REMOVE_HEADERS:
            if header in headers:
                headers['_'+header] = headers.pop(header)

        if '_location' in headers:
            headers['location'] = PROXY_PATH + headers['_location']

        response.headers = headers

        return response

    def _output_headers(self, response):
        self.send_response(response.status_code)

        for header in REMOVE_HEADERS:
            response.headers.pop('_'+header, None)

        for d in list(response.headers.items()):
            self.send_header(d[0], d[1])

        self.end_headers()

    def _output_response(self, response):
        self._output_headers(response)

        for chunk in response.stream.iter_content():
            try: self.wfile.write(chunk)
            except ConnectionResetError: break

    def do_HEAD(self):
        url = self.path.strip('/').strip('\\')
        devlog('HEAD IN: {}'.format(url))
        response = self._proxy_request('HEAD', url)
        self._output_response(response)

    def do_POST(self):
        url = self.path.strip('/').strip('\\')
        devlog('POST IN: {}'.format(url))
        response = self._proxy_request('POST', url)
        self._output_response(response)

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class Proxy(object):
    def __init__(self):
        if not os.path.exists(PROXY_CACHE):
            os.makedirs(PROXY_CACHE)

    def start(self):
        self._server = ThreadedHTTPServer((HOST, PORT), RequestHandler)
        self._httpd_thread = threading.Thread(target=self._server.serve_forever)
        self._httpd_thread.start()

    def stop(self):
        log.debug("Stopping Proxy Server")
        self._server.shutdown()
        self._server.server_close()
        self._server.socket.close()
        self._httpd_thread.join()