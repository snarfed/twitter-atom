"""Serves your Twitter feed as Atom so you can read it in a feed reader."""
import datetime
import logging
import re
from urllib.parse import urlencode

from flask import Flask, redirect, render_template, request
from flask.views import View
from flask_caching import Cache
import flask_gae_static
from granary import atom, microformats2, twitter
from oauth_dropins import twitter as oauth_twitter
from oauth_dropins.webutil import appengine_config, appengine_info, flask_util, util
from oauth_dropins.webutil.flask_util import flash

CACHE_EXPIRATION = datetime.timedelta(minutes=15)

# Wrap webutil.util.tag_uri and hard-code the year this project started, 2013.
_orig_tag_uri = util.tag_uri
util.tag_uri = lambda domain, name: _orig_tag_uri(domain, name, year=2013)

# Flask app
app = Flask('twitter-atom', static_folder=None)
app.template_folder = './templates'
app.config.from_mapping(
    ENV='development' if appengine_info.DEBUG else 'production',
    CACHE_TYPE='SimpleCache',
    SECRET_KEY=util.read('flask_secret_key'),
)
app.after_request(flask_util.default_modern_headers)
app.register_error_handler(Exception, flask_util.handle_exception)
flask_gae_static.init_app(app)
app.wsgi_app = flask_util.ndb_context_middleware(
    app.wsgi_app, client=appengine_config.ndb_client)

cache = Cache(app)


BLACKLISTED_USER_IDS = {
  # 2022-03-22, fetching 12 lists as often as once per minute
  # https://github.com/snarfed/twitter-atom/issues/14
  # 'Gearnine1',
}

@app.route('/generate', methods=['POST'])
def generate():
  """Custom OAuth start view so we can include consumer key in the callback."""
  url = '/oauth_callback?%s' % urlencode({
    'list': request.values.get('list', '').encode('utf-8'),
    'consumer_key': request.values['consumer_key'],
    'consumer_secret': request.values['consumer_secret'],
  })
  return oauth_twitter.Start(url).dispatch_request()


class Callback(oauth_twitter.Callback):
  """The OAuth callback. Generates a new feed URL."""
  def finish(self, auth_entity, state=None):
    if not auth_entity:
      logging.info('User declined Twitter auth prompt')
      return redirect('/')

    token_key, token_secret = auth_entity.access_token()
    atom_url = request.host_url + 'atom?' + urlencode({
        'consumer_key': request.values['consumer_key'],
        'consumer_secret': request.values['consumer_secret'],
        'access_token_key': token_key,
        'access_token_secret': token_secret,
        'list': request.values.get('list', '').encode('utf-8'),
        })
    logging.info('generated feed URL: %s', atom_url)
    return render_template('generated.html', atom_url=atom_url)


class Feed(View):
  """Base class for converting a Twitter user feed or list to Atom or HTML.

  Authenticates to the Twitter API with the user's stored OAuth credentials.

  Attributes:
    actor: AS1 object, current user
  """
  @flask_util.cached(cache, CACHE_EXPIRATION)
  def dispatch_request(self):
    tw = twitter.Twitter(request.args['access_token_key'],
                         request.args['access_token_secret'])
    list_str = request.values.get('list')
    kwargs = {
      'count': 50,
      'include_shares': request.values.get('retweets', '').lower() != 'false',
    }

    try:
      if list_str:
        # this pattern is duplicated in index.html.
        # also note that list names allow more characters that usernames, but the
        # allowed characters aren't explicitly documented. :/ details:
        # https://groups.google.com/d/topic/twitter-development-talk/lULdIVR3B9s/discussion
        match = re.match(r'@?([A-Za-z0-9_]+)/([A-Za-z0-9_-]+)', list_str)
        if not match:
          return flask_util.error(f'List must be of the form username/list (got {list_str})')
        user_id, group_id = match.groups()
        if user_id in BLACKLISTED_USER_IDS:
          return flask_util.error('Too many requests. Please slow down!', status=429)
        actor = tw.get_actor(user_id)
        activities = tw.get_activities(user_id=user_id, group_id=group_id, **kwargs)
      else:
        actor = tw.get_actor()
        activities = tw.get_activities(**kwargs)

    except BaseException as e:
      code, text = util.interpret_http_exception(e)
      if code in ('401', '403'):
        return self.write_activities([{
          'object': {
            'url': request.url,
            'content': 'Your twitter-atom login isn\'t working. <a href="%s">Click here to regenerate your feed!</a>' % request.host_url,
          },
        }])
      raise

    activities.sort(key=lambda a: (a.get('published'), a.get('id')), reverse=True)
    return self.write_activities(activities, actor=actor)

  def write_activities(self, activities, actor=None):
    """Writes the given AS1 activities in the desired output format.

    Args:
      activities: sequence of AS1 activity dicts
      actor: AS1 actor dict for the current user; optional
    """
    raise NotImplementedError()


class Atom(Feed):
  def write_activities(self, activities, actor=None):
    title = 'twitter-atom'
    if actor:
      title += ' feed for %s' % (request.values.get('list') or
                                 actor.get('username', ''))

    return atom.activities_to_atom(
      activities, actor or {}, title=title, host_url=request.host_url,
      request_url=request.url, xml_base='https://twitter.com/',
    ), {'Content-Type': 'application/atom+xml'}


class Html(Feed):
  def write_activities(self, activities, actor=None):
    return microformats2.activities_to_html(activities)


app.add_url_rule('/oauth_callback', view_func=Callback.as_view('oauth_callback', 'unused'))
app.add_url_rule('/atom', view_func=Atom.as_view('atom'))
app.add_url_rule('/html', view_func=Html.as_view('html'))
