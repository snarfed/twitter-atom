#!/usr/bin/python
"""An App Engine app that provides "private" Atom feeds for your Twitter news
feed, ie tweets from people you follow.

Based on both plusstreamfeed and salmon-unofficial.
"""


__author__ = 'Ryan Barrett <twitter-atom@ryanb.org>'

import json
import logging
import os
import urllib
from webob import exc

import appengine_config
from activitystreams import twitter
from activitystreams.webutil import util
from activitystreams.webutil import webapp2
import tweepy

from google.appengine.api import urlfetch
from google.appengine.ext import db
from google.appengine.ext.webapp import template


GENERATED_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                       'templates', 'generated.html')
ATOM_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                  'activitystreams', 'templates', 'user_feed.atom')
ACCESS_TOKEN_WHITELIST = appengine_config.read(os.path.join(os.path.dirname(__file__),
                                                            'access_token_whitelist'))
if ACCESS_TOKEN_WHITELIST:
  ACCESS_TOKEN_WHITELIST = ACCESS_TOKEN_WHITELIST.splitlines()

LIST_URL = 'http://twitter.com/%s'
TWEET_COUNT = 50
API_LIST_TIMELINE_URL = ('https://api.twitter.com/1.1/lists/statuses.json'
                         '?owner_screen_name=%%s&slug=%%s&count=%d' % TWEET_COUNT)

# based on salmon-unofficial/twitter.py.
OAUTH_CALLBACK = '%s://%s/oauth_callback?list=%%s' % (appengine_config.SCHEME,
                                                      appengine_config.HOST)


class OAuthToken(db.Model):
  """Datastore model class for an OAuth token.
  """
  token_key = db.StringProperty(required=True)
  token_secret = db.StringProperty(required=True)


class GenerateHandler(webapp2.RequestHandler):
  """Starts three-legged OAuth with Twitter.

  Fetches an OAuth request token, then redirects to Twitter's auth page to
  request an access token.
  """
  def get(self):
    list_str = self.request.get('list')
    if list_str:
      # does this list exist?
      resp = urlfetch.fetch(LIST_URL % list_str, method='HEAD', deadline=999)
      if resp.status_code == 404:
        self.abort(404, 'Twitter list not found: %s' % list_str)
      elif resp.status_code != 200:
        self.abort(resp.status_code, 'Error looking up Twitter list %s:\n%s' %
                   (list_str, resp.content))

    try:
      auth = tweepy.OAuthHandler(appengine_config.TWITTER_APP_KEY,
                                 appengine_config.TWITTER_APP_SECRET,
                                 OAUTH_CALLBACK % list_str)
      auth_url = auth.get_authorization_url()
    except tweepy.TweepError, e:
      msg = 'Could not create Twitter OAuth request token: '
      logging.exception(msg)
      raise exc.HTTPInternalServerError(msg + `e`)

    # store the request token for later use in the callback handler
    OAuthToken(token_key = auth.request_token.key,
               token_secret = auth.request_token.secret,
               ).put()
    logging.info('Generated request token, redirecting to Twitter: %s', auth_url)
    self.redirect(auth_url)


class CallbackHandler(webapp2.RequestHandler):
  """The OAuth callback. Fetches an access token and redirects to the front page.
  """

  def get(self):
    oauth_token = self.request.get('oauth_token', None)
    oauth_verifier = self.request.get('oauth_verifier', None)
    if oauth_token is None:
      raise exc.HTTPBadRequest('Missing required query parameter oauth_token.')

    # Lookup the request token
    request_token = OAuthToken.gql('WHERE token_key=:key', key=oauth_token).get()
    if request_token is None:
      raise exc.HTTPBadRequest('Invalid oauth_token: %s' % oauth_token)

    # Rebuild the auth handler
    auth = tweepy.OAuthHandler(appengine_config.TWITTER_APP_KEY,
                               appengine_config.TWITTER_APP_SECRET)
    auth.set_request_token(request_token.token_key, request_token.token_secret)

    # Fetch the access token
    try:
      access_token = auth.get_access_token(oauth_verifier)
    except tweepy.TweepError, e:
      msg = 'Twitter OAuth error, could not get access token: '
      logging.exception(msg)
      raise exc.HTTPInternalServerError(msg + `e`)

    atom_url = '%s/atom?list=%s&access_token_key=%s&access_token_secret=%s' % (
      self.request.host_url, self.request.get('list'),
      access_token.key, access_token.secret)
    logging.info('generated feed URL: %s', atom_url)
    self.response.out.write(template.render(GENERATED_TEMPLATE_FILE,
                                            {'atom_url': atom_url}))


class AtomHandler(webapp2.RequestHandler):
  """Proxies the Atom feed for a Twitter user's stream.

  Authenticates to the Twitter API with the user's stored OAuth credentials.
  """
  def get(self):
    token_key = self.request.get('access_token_key')

    if ACCESS_TOKEN_WHITELIST and token_key in ACCESS_TOKEN_WHITELIST:
      tw = twitter.Twitter(self)
      actor = tw.get_actor()

      list_str = self.request.get('list')
      if list_str:
        # Twitter.urlfetch passes through access_token_key and access_token_secret
        resp = tw.urlfetch(API_LIST_TIMELINE_URL % tuple(list_str.split('/')))
        title = 'Twitter list %s' % list_str
      else:
        resp = tw.urlfetch(twitter.API_TIMELINE_URL % TWEET_COUNT)
        title = 'Twitter stream for %s' % actor['displayName']

      activities = [tw.tweet_to_activity(t) for t in json.loads(resp)]

    else:
      title = 'Goodbye from Twitter Atom feeds!'
      actor = {
        'displayName': 'Ryan Barrett',
        'id': 'http://snarfed.org/',
        'url': 'http://snarfed.org/',
        }
      activities = [{
        'verb': 'post',
        'published': '2013-04-13T12:00:00',
        'id': 'tag:twitter-atom.appspot.com,2013:1',
        'url': 'http://twitter-atom.appspot.com/',
        'title': title,
        'actor': actor,
        'object': {
          'id': 'tag:twitter-atom.appspot.com,2013:1',
          'published': '2013-04-13T12:00:00',
          'content': '''
<p style="color: red; font-style: italic;"><b>Twitter Atom feeds is signing off! <a href="http://twitter-atom.appspot.com/">More details here.</a> This is the last item you'll see in your feed. Goodbye!</b></p>''',
          },
        }]

#     # shutdown warning
#     activities.append({
#         'verb': 'post',
#         'published': '2013-03-30T15:00:00',
#         'id': 'tag:twitter-atom.appspot.com,2013:0',
#         'url': 'http://twitter-atom.appspot.com/',
#         'title': 'ATTENTION: Twitter Atom feeds is shutting down!',
#         'actor': {
#           'displayName': 'Ryan Barrett',
#           'id': 'http://snarfed.org/',
#           'url': 'http://snarfed.org/',
#           },
#         'object': {
#           'id': 'tag:twitter-atom.appspot.com,2013:0',
#           'published': '2013-03-30T15:00:00',
#           'content': '''
# <div style="color: red; font-style: italic;">
# <p><b>Bad news! This service (Twitter Atom feeds) is shutting down</b>.</p>

# <p> Twitter has told me that it violates <a href="https://dev.twitter.com/terms/api-terms">their TOS</a> because it republishes tweets, which is forbidden. They say I can keep it running if clients fetch the tweets directly, but the clients are feed readers I don't control, so that's not really possible.</p>

# <p>So, it's the end of the line. I'll keep it running for a couple weeks, but not much longer. I wish I had an alternative to recommend, but any alternative would have the same problem. If you're ambitious, you're welcome to deploy <a href="https://github.com/snarfed/twitter-atom">the code</a> on your own <a href="https://developers.google.com/appengine/">App Engine app</a> and <a href="https://dev.twitter.com/apps/new">Twitter app id</a>.</p>

# <p>Otherwise, apologies, and thanks for your support, it's been fun. So long, and thanks for all the fish!</p>
# </div>''',
#           },
#         })

    self.response.headers['Content-Type'] = 'text/xml'
    self.response.out.write(template.render(
        ATOM_TEMPLATE_FILE,
        {'title': title,
         'updated': activities[0]['object'].get('published') if activities else '',
         'actor': actor,
         'items': activities,
         'request_url': self.request.path_url,
         }))


application = webapp2.WSGIApplication(
  [('/generate', GenerateHandler),
   ('/oauth_callback', CallbackHandler),
   ('/atom', AtomHandler),
   ],
  debug=appengine_config.DEBUG)
