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

from google.appengine.ext import db
from google.appengine.ext.webapp import template


GENERATED_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                       'templates', 'generated.html')
ATOM_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                  'activitystreams', 'templates', 'user_feed.atom')

# based on salmon-unofficial/twitter.py.
OAUTH_CALLBACK = '%s://%s/oauth_callback' % (appengine_config.SCHEME,
                                             appengine_config.HOST)


class OAuthToken(db.Model):
  """Datastore model class for an OAuth token.
  """
  token_key = db.StringProperty(required=True)
  token_secret = db.StringProperty(required=True)


class StartAuthHandler(webapp2.RequestHandler):
  """Starts three-legged OAuth with Twitter.

  Fetches an OAuth request token, then redirects to Twitter's auth page to
  request an access token.
  """
  def get(self):
    try:
      auth = tweepy.OAuthHandler(appengine_config.TWITTER_APP_KEY,
                                 appengine_config.TWITTER_APP_SECRET,
                                 OAUTH_CALLBACK)
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

    atom_url = '%s/atom?access_token_key=%s&access_token_secret=%s' % (
      self.request.host_url, access_token.key, access_token.secret)
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

    tw = twitter.Twitter(self)
    actor = tw.user_to_actor(resp)
    posts = resp.get('home', {}).get('data', [])
    activities = [tw.post_to_activity(p) for p in posts]

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
  [('/generate', StartAuthHandler),
   ('/oauth_callback', CallbackHandler),
   ('/atom', AtomHandler),
   ],
  debug=appengine_config.DEBUG)
