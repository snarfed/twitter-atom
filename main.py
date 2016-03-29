"""An App Engine app that provides "private" Atom feeds for your Twitter news
feed, ie tweets from people you follow.
"""

__author__ = 'Ryan Barrett <twitter-atom@ryanb.org>'

import logging
import os
import re
import urllib

import appengine_config
from granary import atom, twitter
from oauth_dropins import twitter as oauth_twitter
from oauth_dropins.webutil import handlers
from oauth_dropins.webutil import util

from google.appengine.ext.webapp import template
import webapp2


# Wrap webutil.util.tag_uri and hard-code the year this project started, 2013.
_orig_tag_uri = util.tag_uri
util.tag_uri = lambda domain, name: _orig_tag_uri(domain, name, year=2013)


class GenerateHandler(webapp2.RequestHandler):
  """Custom OAuth start handler so we can include consumer key in the callback.
  """
  handle_exception = handlers.handle_exception

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
  handle_exception = handlers.handle_exception

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
  handle_exception = handlers.handle_exception

  def get(self):
    self.response.headers['Content-Type'] = 'application/atom+xml'

    # New style feed with user-provided app (consumer) key and secret
    if (not self.request.get('consumer_key') and
        not self.request.get('consumer_secret')):
      # Welcome back message for old feeds
      self.response.out.write("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xml:lang="en-US" xmlns="http://www.w3.org/2005/Atom">
<generator uri="https://twitter-atom.appspot.com/" version="0.1">twitter-atom</generator>
<id>https://twitter-atom.appspot.com/</id>
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

    tw = twitter.Twitter(util.get_required_param(self, 'access_token_key'),
                         util.get_required_param(self, 'access_token_secret'))

    list_str = self.request.get('list')
    if list_str:
      # this pattern is duplicated in index.html.
      # also note that list names allow more characters that usernames, but the
      # allowed characters aren't explicitly documented. :/ details:
      # https://groups.google.com/d/topic/twitter-development-talk/lULdIVR3B9s/discussion
      match = re.match(r'@?([A-Za-z0-9_]+)/([A-Za-z0-9_-]+)', list_str)
      if not match:
        self.abort(400, 'List must be of the form username/list (got %r)' % list_str)
      user_id, group_id = match.groups()
      actor = tw.get_actor(user_id)
      activities = tw.get_activities(user_id=user_id, group_id=group_id, count=50)
    else:
      actor = tw.get_actor()
      activities = tw.get_activities(count=50)

    title = 'twitter-atom feed for %s' % (list_str or actor.get('username', ''))
    self.response.out.write(atom.activities_to_atom(
        activities, actor, title=title, host_url=self.request.host_url + '/',
        request_url=self.request.path_url, xml_base='https://twitter.com/'))


application = webapp2.WSGIApplication(
  [('/generate', GenerateHandler),
   ('/oauth_callback', CallbackHandler),
   ('/atom', AtomHandler),
   ],
  debug=False)
