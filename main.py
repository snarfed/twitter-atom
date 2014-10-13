"""An App Engine app that provides "private" Atom feeds for your Twitter news
feed, ie tweets from people you follow.
"""

__author__ = 'Ryan Barrett <twitter-atom@ryanb.org>'

import logging
import os
import urllib

import appengine_config
from activitystreams import atom
from activitystreams import twitter
from activitystreams.oauth_dropins import twitter as oauth_twitter
from activitystreams.oauth_dropins.webutil import util

from google.appengine.ext.webapp import template
import webapp2


class GenerateHandler(webapp2.RequestHandler):
  """Custom OAuth start handler so we can include consumer key in the callback.
  """
  def post(self):
    url = '/oauth_callback?%s' % urllib.urlencode({
        'list': self.request.get('list', ''),
        'consumer_key': util.get_required_param(self, 'consumer_key'),
        'consumer_secret': util.get_required_param(self, 'consumer_secret'),
        })
    handler = oauth_twitter.StartHandler.to(url)(self.request, self.response)
    return handler.post()


class CallbackHandler(oauth_twitter.CallbackHandler):
  """The OAuth callback. Generates a new feed URL."""

  def finish(self, auth_entity, state=None):
    if not auth_entity:
      logging.info('User declined Twitter auth prompt')
      return self.redirect('/')

    token_key, token_secret = auth_entity.access_token()
    atom_url = self.request.host_url + '/atom?' + urllib.urlencode({
        'consumer_key': util.get_required_param(self, 'consumer_key'),
        'consumer_secret': util.get_required_param(self, 'consumer_secret'),
        'access_token_key': token_key,
        'access_token_secret': token_secret,
        'list': self.request.get('list', ''),
        })
    logging.info('generated feed URL: %s', atom_url)
    self.response.out.write(template.render(
        os.path.join(os.path.dirname(__file__), 'templates', 'generated.html'),
        {'atom_url': atom_url}))


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
    else:
      actor = tw.get_actor()
      activities = tw.get_activities()

    title = 'twitter-atom feed for %s' % (list_str or actor.get('username', ''))
    self.response.out.write(atom.activities_to_atom(
        activities, actor, title=title, host_url=self.request.host_url + '/',
        request_url=self.request.path_url))


application = webapp2.WSGIApplication(
  [('/generate', GenerateHandler),
   ('/oauth_callback', CallbackHandler),
   ('/atom', AtomHandler),
   ],
  debug=appengine_config.DEBUG)
