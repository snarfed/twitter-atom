#!/usr/bin/python
"""An App Engine app that provides "private" Atom feeds for your Twitter news
feed, ie posts from your friends.

Based on both plusstreamfeed and salmon-unofficial.
"""

__author__ = 'Ryan Barrett <twitter-atom@ryanb.org>'

import json
import logging
import os
import urllib
import urlparse

import appengine_config
from activitystreams import twitter
from activitystreams.webutil import util
from activitystreams.webutil import webapp2

from google.appengine.ext.webapp import template


GENERATED_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                       'templates', 'generated.html')
ATOM_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                  'activitystreams', 'templates', 'user_feed.atom')

API_HOME_URL = (
  'https://graph.twitter.com/me?access_token=%s&fields='
  'home,id,username,name,link,updated_time,bio,location')
API_HOME_COUNT = 25  # default number of posts returned

# based on salmon-unofficial/twitter.py.
# twitter api url templates. can't (easily) use urllib.urlencode() because i
# want to keep the %(...)s placeholders as is and fill them in later in code.
# TODO: use appengine_config.py for local mocktwitter vs prod twitter
GET_AUTH_CODE_URL = '&'.join((
    'https://www.twitter.com/dialog/oauth/?scope=read_stream,offline_access',
    'client_id=%(client_id)s',
    # redirect_uri here must be the same in the access token request!
    'redirect_uri=%(host_url)s/got_auth_code',
    'response_type=code',
    'state=%(state)s',
    ))

GET_ACCESS_TOKEN_URL = '&'.join((
    'https://graph.twitter.com/oauth/access_token?client_id=%(client_id)s',
    # redirect_uri here must be the same in the oauth request!
    # (the value here doesn't actually matter since it's requested server side.)
    'redirect_uri=%(host_url)s/got_auth_code',
    'client_secret=%(client_secret)s',
    'code=%(auth_code)s',
    ))


class GenerateHandler(webapp2.RequestHandler):
  """Registers the current user and generates a feed URL for their stream.

  Based on AddTwitter in salmon-unofficial/twitter.py.
  """

  def post(self):
    """Starts generating a feed URL by requesting a Twitter auth code.

    After retrieving an auth code, redirects to /twitter_got_auth_code,
    which makes the next request to get the access token.
    """
    logging.info('Generating a new feed. Asking FB for auth code.')

    url = GET_AUTH_CODE_URL % {
      'client_id': appengine_config.TWITTER_APP_ID,
      # TODO: CSRF protection identifier.
      # http://developers.twitter.com/docs/authentication/
      'host_url': self.request.host_url,
      'state': self.request.host_url + '/got_auth_token',
      }
    self.redirect(url)


class GotAuthCode(webapp2.RequestHandler):
  def get(self):
    """Gets an access token based on an auth code."""
    auth_code = self.request.get('code')
    assert auth_code
    logging.info('got auth code: %s', auth_code)

    redirect_uri = urllib.unquote(self.request.get('state'))
    assert '?' not in redirect_uri

    # TODO: handle permission declines, errors, etc
    url = GET_ACCESS_TOKEN_URL % {
      'auth_code': auth_code,
      'client_id': appengine_config.TWITTER_APP_ID,
      'client_secret': appengine_config.TWITTER_APP_SECRET,
      'host_url': self.request.host_url,
      }
    logging.info('getting access token via %s', url)
    resp = util.urlfetch(url)
    # TODO: error handling. handle permission declines, errors, etc
    logging.info('access token response: %s' % resp)
    params = urlparse.parse_qs(resp)
    access_token = params['access_token'][0]

    atom_url = '%s/atom?access_token=%s' % (self.request.host_url, access_token)
    logging.info('generated feed URL: %s', atom_url)
    self.response.out.write(template.render(GENERATED_TEMPLATE_FILE,
                                            {'atom_url': atom_url}))


class AtomHandler(webapp2.RequestHandler):
  """Proxies the Atom feed for a Twitter user's stream.

  Authenticates to the Twitter API with the user's stored OAuth credentials.
  """
  def get(self):
    access_token = self.request.get('access_token')
    assert access_token
    resp = json.loads(util.urlfetch(API_HOME_URL % access_token))

    fb = twitter.Twitter(self)
    actor = fb.user_to_actor(resp)
    posts = resp.get('home', {}).get('data', [])
    activities = [fb.post_to_activity(p) for p in posts]

    self.response.headers['Content-Type'] = 'text/xml'
    self.response.out.write(template.render(
        ATOM_TEMPLATE_FILE,
        {'title': 'Twitter news feed for %s' % actor['displayName'],
         'updated': activities[0]['object'].get('updated') if activities else '',
         'actor': actor,
         'items': activities,
         'request_url': self.request.path_url,
         }))


application = webapp2.WSGIApplication(
  [('/generate', GenerateHandler),
   ('/got_auth_code', GotAuthCode),
   ('/atom', AtomHandler),
   ],
  debug=appengine_config.DEBUG)
