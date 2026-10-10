"""Минимальный HTTP-клиент smoke: cookies, HTML inputs, no-store."""
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request


class Inputs(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.values = {}
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'input' and values.get('name') and values.get('value') is not None:
            self.values[values['name']] = values['value']


class Browser:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, path, data=None):
        request = urllib.request.Request(self.base + path, data=urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None)
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read().decode()
            assert response.headers['Cache-Control'] == 'no-store'
            return response.status, body, response.geturl(), response.headers

    def post_form(self, path, data):
        status, body, _, _ = self.request(path)
        assert status == 200
        defaults = {key:value for key,value in Inputs(body).values.items() if key in {'csrfmiddlewaretoken','version','token','next','request_key'}}
        defaults.update(data)
        return self.request(urllib.parse.urlsplit(path).path, defaults)
