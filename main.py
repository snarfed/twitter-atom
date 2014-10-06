"""An App Engine app that provides "private" Atom feeds for your Twitter news
feed, ie tweets from people you follow.
"""

__author__ = 'Ryan Barrett <twitter-atom@ryanb.org>'

import json
import logging
import os
import urllib
import urlparse
from webob import exc

import appengine_config
from activitystreams import atom
from activitystreams import twitter
from activitystreams.oauth_dropins.webutil import util
import tweepy

from google.appengine.api import urlfetch
from google.appengine.ext import db
from google.appengine.ext.webapp import template
import webapp2


GENERATED_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                       'templates', 'generated.html')

# based on salmon-unofficial/twitter.py.
OAUTH_CALLBACK = '%s://%s/oauth_callback?list=%%s' % (appengine_config.SCHEME,
                                                      appengine_config.HOST)


class OAuthToken(db.Model):
  """Datastore model class for an OAuth token.
  """
  token_key = db.StringProperty(required=True)
  token_secret = db.StringProperty(required=True)
  consumer_key = db.StringProperty(required=True)
  consumer_secret = db.StringProperty(required=True)


class GenerateHandler(webapp2.RequestHandler):
  """Starts three-legged OAuth with Twitter.

  Fetches an OAuth request token, then redirects to Twitter's auth page to
  request an access token.
  """
  def get(self):
    list_str = self.request.get('list')
    if list_str:
      # does this list exist?
      # nevermind, this fails on private lists.
      # resp = urlfetch.fetch(LIST_URL % list_str, method='HEAD', deadline=999)
      # if resp.status_code == 404:
      #   self.abort(404, 'Twitter list not found: %s' % list_str)
      # elif resp.status_code != 200:
      #   self.abort(resp.status_code, 'Error looking up Twitter list %s:\n%s' %
      #              (list_str, resp.content))
      pass

    consumer_key = str(util.get_required_param(self, 'consumer_key'))
    consumer_secret = str(util.get_required_param(self, 'consumer_secret'))

    try:
      auth = tweepy.OAuthHandler(consumer_key, consumer_secret,
                                 OAUTH_CALLBACK % list_str)
      auth_url = auth.get_authorization_url()
    except tweepy.TweepError, e:
      msg = 'Could not create Twitter OAuth request token: '
      logging.exception(msg)
      raise exc.HTTPInternalServerError(msg + `e`)

    # store the request token for later use in the callback handler
    OAuthToken(token_key=auth.request_token['oauth_token'],
               token_secret=auth.request_token['oauth_token_secret'],
               consumer_key=consumer_key,
               consumer_secret=consumer_secret,
               ).put()
    logging.info('Generated request token, redirecting to Twitter: %s', auth_url)
    self.redirect(str(auth_url))


class CallbackHandler(webapp2.RequestHandler):
  """The OAuth callback. Fetches an access token and redirects to the front page.
  """

  def get(self):
    oauth_token = util.get_required_param(self, 'oauth_token')
    oauth_verifier = self.request.get('oauth_verifier', None)

    # Lookup the request token
    request_token = OAuthToken.gql('WHERE token_key=:key', key=oauth_token).get()
    if request_token is None:
      raise exc.HTTPBadRequest('Invalid oauth_token: %s' % oauth_token)

    # Rebuild the auth handler
    auth = tweepy.OAuthHandler(request_token.consumer_key,
                               request_token.consumer_secret)
    auth.request_token = {'oauth_token': request_token.token_key,
                          'oauth_token_secret': request_token.token_secret}

    # Fetch the access token
    try:
      access_token_key, access_token_secret = auth.get_access_token(oauth_verifier)
    except tweepy.TweepError, e:
      msg = 'Twitter OAuth error, could not get access token: '
      logging.exception(msg)
      raise exc.HTTPInternalServerError(msg + `e`)

    atom_url = '%s/atom?list=%s&access_token_key=%s&access_token_secret=%s&consumer_key=%s&consumer_secret=%s' % (
      self.request.host_url, self.request.get('list'),
      access_token_key, access_token_secret,
      request_token.consumer_key, request_token.consumer_secret)
    logging.info('generated feed URL: %s', atom_url)
    self.response.out.write(template.render(GENERATED_TEMPLATE_FILE,
                                            {'atom_url': atom_url}))


def actor_name(actor):
  return actor.get('displayName') or actor.get('username') or 'you'


class AtomHandler(webapp2.RequestHandler):
  """Proxies the Atom feed for a Twitter user's stream.

  Authenticates to the Twitter API with the user's stored OAuth credentials.
  """
  def get(self):
    self.response.headers['Content-Type'] = 'application/atom+xml'

    if (not self.request.get('consumer_key') and
        not self.request.get('consumer_secret')):
      # Welcome back message for old feeds
      self.response.out.write("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xml:lang="en-US" xmlns="http://www.w3.org/2005/Atom">
<generator uri="https://github.com/snarfed/activitystreams-unofficial" version="0.1">
  activitystreams-unofficial</generator>
<id>%s</id>
<title>Twitter Atom feeds is back!</title>
<updated>2013-07-08T20:00:00</updated>
<entry>
<id>tag:twitter-atom.appspot.com,2013:2</id>
<title>Twitter Atom feeds is back!</title>
<content type="xhtml">
<div xmlns="http://www.w3.org/1999/xhtml">
<p style="color: red; font-style: italic;"><b>Twitter Atom feeds is back! I'm experimenting with a new design that Twitter will (hopefully) be ok with. You can try it out by <a href="http://twitter-atom.appspot.com/">generating a new feed here</a>. Feel free to <a href="http://twitter.com/snarfed_org">ping me</a> if you have any questions. Welcome back!</b></p>
</div>
</content>
<published>2013-07-08T20:00:00</published>
</entry>
</feed>
""")
      return

    # New style feed with user-provided app (consumer) key and secret
    tw = twitter.Twitter(util.get_required_param(self, 'access_token_key'),
                         util.get_required_param(self, 'access_token_secret'))

    list_str = self.request.get('list')
    if list_str:
      if list_str.startswith('@'):
        list_str = list_str[1:]
      user_id, group_id = list_str.split('/')
      actor = tw.get_actor(user_id)
      activities = tw.get_activities(user_id=user_id, group_id=group_id)
      title = 'Twitter list %s' % list_str
    else:
      actor = tw.get_actor()
      activities = tw.get_activities()
      title = 'Twitter stream for %s' % actor_name(actor)

    self.response.out.write(atom.activities_to_atom(
        activities, actor, host_url=self.request.host_url + '/',
        request_url=self.request.path_url))


application = webapp2.WSGIApplication(
  [('/generate', GenerateHandler),
   ('/oauth_callback', CallbackHandler),
   ('/atom', AtomHandler),
   ],
  debug=appengine_config.DEBUG)
