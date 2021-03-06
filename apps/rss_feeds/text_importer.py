import requests
import zlib
from requests.packages.urllib3.exceptions import LocationParseError
from socket import error as SocketError
from mongoengine.queryset import NotUniqueError
from vendor.readability import readability
from lxml.etree import ParserError
from utils import log as logging
from utils.feed_functions import timelimit, TimeoutError
from OpenSSL.SSL import Error as OpenSSLError
from pyasn1.error import PyAsn1Error
from django.utils.encoding import smart_str
from BeautifulSoup import BeautifulSoup

BROKEN_URLS = [
    "gamespot.com",
]


class TextImporter:

    def __init__(self, story=None, feed=None, story_url=None, request=None, debug=False):
        self.story = story
        self.story_url = story_url
        self.feed = feed
        self.request = request
        self.debug = debug

    @property
    def headers(self):
        num_subscribers = getattr(self.feed, 'num_subscribers', 0)
        return {
            'User-Agent': 'NewsBlur Content Fetcher - %s subscriber%s - %s '
                          '(Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_1) '
                          'AppleWebKit/534.48.3 (KHTML, like Gecko) Version/5.1 '
                          'Safari/534.48.3)' % (
                              num_subscribers,
                              's' if num_subscribers != 1 else '',
                              getattr(self.feed, 'permalink', '')
                          ),
        }

    def fetch(self, skip_save=False, return_document=False):
        if self.story_url and any(broken_url in self.story_url for broken_url in BROKEN_URLS):
            logging.user(self.request, "~SN~FRFailed~FY to fetch ~FGoriginal text~FY: banned")
            return

        try:
            resp = self.fetch_request()
        except TimeoutError:
            logging.user(self.request, "~SN~FRFailed~FY to fetch ~FGoriginal text~FY: timed out")
            resp = None
        except requests.exceptions.TooManyRedirects:
            logging.user(self.request, "~SN~FRFailed~FY to fetch ~FGoriginal text~FY: too many redirects")
            resp = None

        if not resp:
            return

        try:
            text = resp.text
        except (LookupError, TypeError):
            text = resp.content
        
        # if self.debug:
        #     logging.user(self.request, "~FBOriginal text's website: %s" % text)
        
        if resp.encoding and resp.encoding != 'utf-8':
            try:
                text = text.encode(resp.encoding)
            except (LookupError, UnicodeEncodeError):
                pass

        if text:
            text = text.replace("\xc2\xa0", " ") # Non-breaking space, is mangled when encoding is not utf-8
            text = text.replace("\u00a0", " ") # Non-breaking space, is mangled when encoding is not utf-8

        original_text_doc = readability.Document(text, url=resp.url,
                                                 positive_keywords="postContent, postField")
        try:
            content = original_text_doc.summary(html_partial=True)
        except (readability.Unparseable, ParserError), e:
            logging.user(self.request, "~SN~FRFailed~FY to fetch ~FGoriginal text~FY: %s" % e)
            return

        try:
            title = original_text_doc.title()
        except TypeError:
            title = ""
        url = resp.url
        
        if content:
            content = self.rewrite_content(content)
        
        if content:
            if self.story and not skip_save:
                self.story.original_text_z = zlib.compress(smart_str(content))
                try:
                    self.story.save()
                except NotUniqueError, e:
                    logging.user(self.request, ("~SN~FYFetched ~FGoriginal text~FY: %s" % (e)), warn_color=False)
                    pass
            logging.user(self.request, ("~SN~FYFetched ~FGoriginal text~FY: now ~SB%s bytes~SN vs. was ~SB%s bytes" % (
                len(content),
                self.story and self.story.story_content_z and len(zlib.decompress(self.story.story_content_z))
            )), warn_color=False)
        else:
            logging.user(self.request, ("~SN~FRFailed~FY to fetch ~FGoriginal text~FY: was ~SB%s bytes" % (
                self.story and self.story.story_content_z and len(zlib.decompress(self.story.story_content_z))
            )), warn_color=False)

        if return_document:
            return dict(content=content, title=title, url=url, doc=original_text_doc)

        return content

    def rewrite_content(self, content):
        soup = BeautifulSoup(content)
        
        for noscript in soup.findAll('noscript'):
            if len(noscript.contents) > 0:
                noscript.replaceWith(noscript.contents[0])
        
        return unicode(soup)
    
    @timelimit(10)
    def fetch_request(self):
        url = self.story_url
        if self.story and not url:
            url = self.story.story_permalink
        try:
            r = requests.get(url, headers=self.headers, verify=False)
            r.connection.close()
        except (AttributeError, SocketError, requests.ConnectionError,
                requests.models.MissingSchema, requests.sessions.InvalidSchema,
                requests.sessions.TooManyRedirects,
                requests.models.InvalidURL,
                requests.models.ChunkedEncodingError,
                requests.models.ContentDecodingError,
                LocationParseError, OpenSSLError, PyAsn1Error), e:
            logging.user(self.request, "~SN~FRFailed~FY to fetch ~FGoriginal text~FY: %s" % e)
            return
        return r
