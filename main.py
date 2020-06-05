"""An App Engine app that provides "private" Atom feeds for your Twitter news
feed, ie tweets from people you follow.
"""
import datetime
import logging
import re
from urllib.parse import urlencode

from granary import atom, microformats2, twitter
import jinja2
from oauth_dropins import twitter as oauth_twitter
from oauth_dropins.webutil import appengine_config, appengine_info, handlers, util
import webapp2
from webob import exc

CACHE_EXPIRATION = datetime.timedelta(minutes=15)

# Wrap webutil.util.tag_uri and hard-code the year this project started, 2013.
_orig_tag_uri = util.tag_uri
util.tag_uri = lambda domain, name: _orig_tag_uri(domain, name, year=2013)


class GenerateHandler(webapp2.RequestHandler):
  """Custom OAuth start handler so we can include consumer key in the callback.
  """
  handle_exception = handlers.handle_exception

  def post(self):
    url = '/oauth_callback?%s' % urlencode({
        'list': self.request.get('list', '').encode('utf-8'),
        'consumer_key': util.get_required_param(self, 'consumer_key'),
        'consumer_secret': util.get_required_param(self, 'consumer_secret'),
        })
    handler = oauth_twitter.StartHandler.to(url)(self.request, self.response)
    return handler.post()


class CallbackHandler(oauth_twitter.CallbackHandler, handlers.ModernHandler):
  """The OAuth callback. Generates a new feed URL."""
  handle_exception = handlers.handle_exception

  def finish(self, auth_entity, state=None):
    if not auth_entity:
      logging.info('User declined Twitter auth prompt')
      return self.redirect('/')

    token_key, token_secret = auth_entity.access_token()
    atom_url = self.request.host_url + '/atom?' + urlencode({
        'consumer_key': util.get_required_param(self, 'consumer_key'),
        'consumer_secret': util.get_required_param(self, 'consumer_secret'),
        'access_token_key': token_key,
        'access_token_secret': token_secret,
        'list': self.request.get('list', '').encode('utf-8'),
        })
    logging.info('generated feed URL: %s', atom_url)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(('.')), autoescape=True)
    self.response.out.write(env.get_template('templates/generated.html').render(
      {'atom_url': atom_url}))


class FeedHandler(handlers.ModernHandler):
  """Base class for converting a Twitter user feed or list to Atom or HTML.

  Authenticates to the Twitter API with the user's stored OAuth credentials.

  Attributes:
    actor: AS1 object, current user
  """
  def handle_exception(self, e, debug):
    code, text = util.interpret_http_exception(e)
    if code in ('401', '403'):
      return self.write_activities([{
        'object': {
          'url': self.request.url,
          'content': 'Your twitter-atom login isn\'t working. <a href="%s">Click here to regenerate your feed!</a>' % self.request.host_url,
        },
      }])

    return handlers.handle_exception(self, e, debug)

  @handlers.cache_response(CACHE_EXPIRATION)
  def get(self):
    tw = twitter.Twitter(util.get_required_param(self, 'access_token_key'),
                         util.get_required_param(self, 'access_token_secret'))

    list_str = self.request.get('list')
    if list_str:
      if list_str == 'tonysss13/financial':
        raise exc.HTTPTooManyRequests("Please reduce your feed reader's polling rate.")

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

    self.write_activities(activities, actor=actor)

  def write_activities(self, activities, actor=None):
    """Writes the given AS1 activities in the desired output format.

    Args:
      activities: sequence of AS1 activity dicts
      actor: AS1 actor dict for the current user; optional
    """
    raise NotImplementedError()


class AtomHandler(FeedHandler):
  def write_activities(self, activities, actor=None):
    title = 'twitter-atom'
    if actor:
      title += ' feed for %s' % (self.request.get('list') or actor.get('username', ''))

    self.response.headers['Content-Type'] = 'application/atom+xml'
    self.response.out.write(atom.activities_to_atom(
      activities, actor or {}, title=title, host_url=self.request.host_url + '/',
      request_url=self.request.path_url, xml_base='https://twitter.com/'))


class HtmlHandler(FeedHandler):
  def write_activities(self, activities, actor=None):
    self.response.headers['Content-Type'] = 'text/html'
    self.response.out.write(microformats2.activities_to_html(activities))


application = handlers.ndb_context_middleware(webapp2.WSGIApplication(
  [('/generate', GenerateHandler),
   ('/oauth_callback', CallbackHandler),
   ('/atom', AtomHandler),
   ('/html', HtmlHandler),
   ],
  debug=appengine_info.DEBUG), client=appengine_config.ndb_client)
