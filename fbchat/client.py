# -*- coding: UTF-8 -*-

from __future__ import unicode_literals
import requests
import urllib
from uuid import uuid1
from random import choice
from datetime import datetime
from bs4 import BeautifulSoup as bs
from mimetypes import guess_type
from .utils import *
from .models import *
from .graphql import *
import time



class Client(object):
    """A client for the Facebook Chat (Messenger).

    See https://fbchat.readthedocs.io for complete documentation of the API.
    """

    listening = False
    """Whether the client is listening. Used when creating an external event loop to determine when to stop listening"""
    uid = None
    """
    The ID of the client.
    Can be used as `thread_id`. See :ref:`intro_threads` for more info.

    Note: Modifying this results in undefined behaviour
    """

    def __init__(self, email, password, user_agent=None, max_tries=5, session_cookies=None, logging_level=logging.INFO):
        """Initializes and logs in the client

        :param email: Facebook `email`, `id` or `phone number`
        :param password: Facebook account password
        :param user_agent: Custom user agent to use when sending requests. If `None`, user agent will be chosen from a premade list (see :any:`utils.USER_AGENTS`)
        :param max_tries: Maximum number of times to try logging in
        :param session_cookies: Cookies from a previous session (Will default to login if these are invalid)
        :param logging_level: Configures the `logging level <https://docs.python.org/3/library/logging.html#logging-levels>`_. Defaults to `INFO`
        :type max_tries: int
        :type session_cookies: dict
        :type logging_level: int
        :raises: FBchatException on failed login
        """

        self.sticky, self.pool = (None, None)
        self._session = requests.session()
        self.req_counter = 1
        self.seq = "0"
        self.payloadDefault = {}
        self.client = 'mercury'
        self.default_thread_id = None
        self.default_thread_type = None
        self.req_url = ReqUrl()

        if not user_agent:
            user_agent = choice(USER_AGENTS)

        self._header = {
            'Content-Type' : 'application/x-www-form-urlencoded',
            'Referer' : self.req_url.BASE,
            'Origin' : self.req_url.BASE,
            'User-Agent' : user_agent,
            'Connection' : 'keep-alive',
        }

        handler.setLevel(logging_level)

        # If session cookies aren't set, not properly loaded or gives us an invalid session, then do the login
        if not session_cookies or not self.setSession(session_cookies) or not self.isLoggedIn():
            self.login(email, password, max_tries)
        else:
            self.email = email
            self.password = password

    """
    INTERNAL REQUEST METHODS
    """

    def _generatePayload(self, query):
        """Adds the following defaults to the payload:
          __rev, __user, __a, ttstamp, fb_dtsg, __req
        """
        payload = self.payloadDefault.copy()
        if query:
            payload.update(query)
        payload['__req'] = str_base(self.req_counter, 36)
        payload['seq'] = self.seq
        self.req_counter += 1
        return payload

    def _fix_fb_errors(self, error_code):
        """
        This fixes "Please try closing and re-opening your browser window" errors (1357004)
        This error usually happens after 1-2 days of inactivity
        It may be a bad idea to do this in an exception handler, if you have a better method, please suggest it!
        """
        if error_code == '1357004':
            log.warning('Got error #1357004. Doing a _postLogin, and resending request')
            self._postLogin()
            return True
        return False

    def _get(self, url, query=None, timeout=30, fix_request=False, as_json=False, error_retries=3):
        payload = self._generatePayload(query)
        r = self._session.get(url, headers=self._header, params=payload, timeout=timeout)
        if not fix_request:
            return r
        try:
            return check_request(r, as_json=as_json)
        except FBchatFacebookError as e:
            if error_retries > 0 and self._fix_fb_errors(e.fb_error_code):
                return self._get(url, query=query, timeout=timeout, fix_request=fix_request, as_json=as_json, error_retries=error_retries-1)
            raise e

    def _post(self, url, query=None, timeout=30, fix_request=False, as_json=False, error_retries=3):
        payload = self._generatePayload(query)
        r = self._session.post(url, headers=self._header, data=payload, timeout=timeout)
        if not fix_request:
            return r
        try:
            return check_request(r, as_json=as_json)
        except FBchatFacebookError as e:
            if error_retries > 0 and self._fix_fb_errors(e.fb_error_code):
                return self._post(url, query=query, timeout=timeout, fix_request=fix_request, as_json=as_json, error_retries=error_retries-1)
            raise e

    def _graphql(self, payload, error_retries=3):
        content = self._post(self.req_url.GRAPHQL, payload, fix_request=True, as_json=False)
        try:
            return graphql_response_to_json(content)
        except FBchatFacebookError as e:
            if error_retries > 0 and self._fix_fb_errors(e.fb_error_code):
                return self._graphql(payload, error_retries=error_retries-1)
            raise e

    def _cleanGet(self, url, query=None, timeout=30):
        return self._session.get(url, headers=self._header, params=query, timeout=timeout)

    def _cleanPost(self, url, query=None, timeout=30):
        self.req_counter += 1
        return self._session.post(url, headers=self._header, data=query, timeout=timeout)

    def _postFile(self, url, files=None, query=None, timeout=30, fix_request=False, as_json=False, error_retries=3):
        payload=self._generatePayload(query)
        # Removes 'Content-Type' from the header
        headers = dict((i, self._header[i]) for i in self._header if i != 'Content-Type')
        r = self._session.post(url, headers=headers, data=payload, timeout=timeout, files=files)
        if not fix_request:
            return r
        try:
            return check_request(r, as_json=as_json)
        except FBchatFacebookError as e:
            if error_retries > 0 and self._fix_fb_errors(e.fb_error_code):
                return self._postFile(url, files=files, query=query, timeout=timeout, fix_request=fix_request, as_json=as_json, error_retries=error_retries-1)
            raise e

    def graphql_requests(self, *queries):
        """
        .. todo::
            Documenting this

        :raises: FBchatException if request failed
        """

        return tuple(self._graphql({
            'method': 'GET',
            'response_format': 'json',
            'queries': graphql_queries_to_json(*queries)
        }))

    def graphql_request(self, query):
        """
        Shorthand for `graphql_requests(query)[0]`

        :raises: FBchatException if request failed
        """
        return self.graphql_requests(query)[0]

    """
    END INTERNAL REQUEST METHODS
    """

    """
    LOGIN METHODS
    """

    def _resetValues(self):
        self.payloadDefault={}
        self._session = requests.session()
        self.req_counter = 1
        self.seq = "0"
        self.uid = None

    def _postLogin(self):
        self.payloadDefault = {}
        self.client_id = hex(int(random()*2147483648))[2:]
        self.start_time = now()
        self.uid = self._session.cookies.get_dict().get('c_user')
        if self.uid is None:
            raise FBchatException('Could not find c_user cookie')
        self.uid = str(self.uid)
        self.user_channel = "p_" + self.uid
        self.ttstamp = ''

        r = self._get(self.req_url.BASE)
        soup = bs(r.text, "lxml")
        self.fb_dtsg = soup.find("input", {'name':'fb_dtsg'})['value']
        self.fb_h = soup.find("input", {'name':'h'})['value']
        for i in self.fb_dtsg:
            self.ttstamp += str(ord(i))
        self.ttstamp += '2'
        # Set default payload
        self.payloadDefault['__rev'] = int(r.text.split('"client_revision":',1)[1].split(",",1)[0])
        self.payloadDefault['__user'] = self.uid
        self.payloadDefault['__a'] = '1'
        self.payloadDefault['ttstamp'] = self.ttstamp
        self.payloadDefault['fb_dtsg'] = self.fb_dtsg

        self.form = {
            'channel' : self.user_channel,
            'partition' : '-2',
            'clientid' : self.client_id,
            'viewer_uid' : self.uid,
            'uid' : self.uid,
            'state' : 'active',
            'format' : 'json',
            'idle' : 0,
            'cap' : '8'
        }

        self.prev = now()
        self.tmp_prev = now()
        self.last_sync = now()

    def _login(self):
        if not (self.email and self.password):
            raise FBchatUserError("Email and password not found.")

        soup = bs(self._get(self.req_url.MOBILE).text, "lxml")
        data = dict((elem['name'], elem['value']) for elem in soup.findAll("input") if elem.has_attr('value') and elem.has_attr('name'))
        data['email'] = self.email
        data['pass'] = self.password
        data['login'] = 'Log In'

        r = self._cleanPost(self.req_url.LOGIN, data)

        # Usually, 'Checkpoint' will refer to 2FA
        if ('checkpoint' in r.url
                and ('enter security code to continue' in r.text.lower()
                    or 'enter login code to continue' in r.text.lower())):
            r = self._2FA(r)

        # Sometimes Facebook tries to show the user a "Save Device" dialog
        if 'save-device' in r.url:
            r = self._cleanGet(self.req_url.SAVE_DEVICE)

        if 'home' in r.url:
            self._postLogin()
            return True, r.url
        else:
            return False, r.url

    def _2FA(self, r):
        soup = bs(r.text, "lxml")
        data = dict()

        s = self.on2FACode()

        data['approvals_code'] = s
        data['fb_dtsg'] = soup.find("input", {'name':'fb_dtsg'})['value']
        data['nh'] = soup.find("input", {'name':'nh'})['value']
        data['submit[Submit Code]'] = 'Submit Code'
        data['codes_submitted'] = 0
        log.info('Submitting 2FA code.')

        r = self._cleanPost(self.req_url.CHECKPOINT, data)

        if 'home' in r.url:
            return r

        del(data['approvals_code'])
        del(data['submit[Submit Code]'])
        del(data['codes_submitted'])

        data['name_action_selected'] = 'save_device'
        data['submit[Continue]'] = 'Continue'
        log.info('Saving browser.')  # At this stage, we have dtsg, nh, name_action_selected, submit[Continue]
        r = self._cleanPost(self.req_url.CHECKPOINT, data)

        if 'home' in r.url:
            return r

        del(data['name_action_selected'])
        log.info('Starting Facebook checkup flow.')  # At this stage, we have dtsg, nh, submit[Continue]
        r = self._cleanPost(self.req_url.CHECKPOINT, data)

        if 'home' in r.url:
            return r

        del(data['submit[Continue]'])
        data['submit[This was me]'] = 'This Was Me'
        log.info('Verifying login attempt.')  # At this stage, we have dtsg, nh, submit[This was me]
        r = self._cleanPost(self.req_url.CHECKPOINT, data)

        if 'home' in r.url:
            return r

        del(data['submit[This was me]'])
        data['submit[Continue]'] = 'Continue'
        data['name_action_selected'] = 'save_device'
        log.info('Saving device again.')  # At this stage, we have dtsg, nh, submit[Continue], name_action_selected
        r = self._cleanPost(self.req_url.CHECKPOINT, data)
        return r

    def isLoggedIn(self):
        """
        Sends a request to Facebook to check the login status

        :return: True if the client is still logged in
        :rtype: bool
        """
        # Send a request to the login url, to see if we're directed to the home page
        r = self._cleanGet(self.req_url.LOGIN)
        return 'home' in r.url

    def getSession(self):
        """Retrieves session cookies

        :return: A dictionay containing session cookies
        :rtype: dict
        """
        return self._session.cookies.get_dict()

    def setSession(self, session_cookies):
        """Loads session cookies

        :param session_cookies: A dictionay containing session cookies
        :type session_cookies: dict
        :return: False if `session_cookies` does not contain proper cookies
        :rtype: bool
        """

        # Quick check to see if session_cookies is formatted properly
        if not session_cookies or 'c_user' not in session_cookies:
            return False

        try:
            # Load cookies into current session
            self._session.cookies = requests.cookies.merge_cookies(self._session.cookies, session_cookies)
            self._postLogin()
        except Exception as e:
            log.exception('Failed loading session')
            self._resetValues()
            return False
        return True

    def login(self, email, password, max_tries=5):
        """
        Uses `email` and `password` to login the user (If the user is already logged in, this will do a re-login)

        :param email: Facebook `email` or `id` or `phone number`
        :param password: Facebook account password
        :param max_tries: Maximum number of times to try logging in
        :type max_tries: int
        :raises: FBchatException on failed login
        """
        self.onLoggingIn(email=email)

        if max_tries < 1:
            raise FBchatUserError('Cannot login: max_tries should be at least one')

        if not (email and password):
            raise FBchatUserError('Email and password not set')

        self.email = email
        self.password = password

        for i in range(1, max_tries+1):
            login_successful, login_url = self._login()
            if not login_successful:
                log.warning('Attempt #{} failed{}'.format(i, {True:', retrying'}.get(i < max_tries, '')))
                time.sleep(1)
                continue
            else:
                self.onLoggedIn(email=email)
                break
        else:
            raise FBchatUserError('Login failed. Check email/password. (Failed on url: {})'.format(login_url))

    def logout(self):
        """
        Safely logs out the client

        :param timeout: See `requests timeout <http://docs.python-requests.org/en/master/user/advanced/#timeouts>`_
        :return: True if the action was successful
        :rtype: bool
        """
        data = {
            'ref': "mb",
            'h': self.fb_h
        }

        r = self._get(self.req_url.LOGOUT, data)

        self._resetValues()

        return r.ok

    """
    END LOGIN METHODS
    """

    """
    DEFAULT THREAD METHODS
    """

    def _getThread(self, given_thread_id=None, given_thread_type=None):
        """
        Checks if thread ID is given, checks if default is set and returns correct values

        :raises ValueError: If thread ID is not given and there is no default
        :return: Thread ID and thread type
        :rtype: tuple
        """
        if given_thread_id is None:
            if self.default_thread_id is not None:
                return self.default_thread_id, self.default_thread_type
            else:
                raise ValueError('Thread ID is not set')
        else:
            return given_thread_id, given_thread_type

    def setDefaultThread(self, thread_id, thread_type):
        """Sets default thread to send messages to

        :param thread_id: User/Group ID to default to. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type thread_type: models.ThreadType
        """
        self.default_thread_id = thread_id
        self.default_thread_type = thread_type

    def resetDefaultThread(self):
        """Resets default thread"""
        self.setDefaultThread(None, None)

    """
    END DEFAULT THREAD METHODS
    """

    """
    FETCH METHODS
    """

    def fetchAllUsers(self):
        """
        Gets all users the client is currently chatting with

        :return: :class:`models.User` objects
        :rtype: list
        :raises: FBchatException if request failed
        """

        data = {
            'viewer': self.uid,
        }
        j = self._post(self.req_url.ALL_USERS, query=data, fix_request=True, as_json=True)
        if j.get('payload') is None:
            raise FBchatException('Missing payload while fetching users: {}'.format(j))

        users = []

        for key in j['payload']:
            k = j['payload'][key]
            if k['type'] in ['user', 'friend']:
                if k['id'] in ['0', 0]:
                    # Skip invalid users
                    pass
                users.append(User(k['id'], first_name=k.get('firstName'), url=k.get('uri'), photo=k.get('thumbSrc'), name=k.get('name'), is_friend=k.get('is_friend'), gender=GENDERS[k.get('gender')]))

        return users

    def searchForUsers(self, name, limit=1):
        """
        Find and get user by his/her name

        :param name: Name of the user
        :param limit: The max. amount of users to fetch
        :return: :class:`models.User` objects, ordered by relevance
        :rtype: list
        :raises: FBchatException if request failed
        """

        j = self.graphql_request(GraphQL(query=GraphQL.SEARCH_USER, params={'search': name, 'limit': limit}))

        return [graphql_to_user(node) for node in j[name]['users']['nodes']]

    def searchForPages(self, name, limit=1):
        """
        Find and get page by its name

        :param name: Name of the page
        :return: :class:`models.Page` objects, ordered by relevance
        :rtype: list
        :raises: FBchatException if request failed
        """

        j = self.graphql_request(GraphQL(query=GraphQL.SEARCH_PAGE, params={'search': name, 'limit': limit}))

        return [graphql_to_page(node) for node in j[name]['pages']['nodes']]

    def searchForGroups(self, name, limit=1):
        """
        Find and get group thread by its name

        :param name: Name of the group thread
        :param limit: The max. amount of groups to fetch
        :return: :class:`models.Group` objects, ordered by relevance
        :rtype: list
        :raises: FBchatException if request failed
        """

        j = self.graphql_request(GraphQL(query=GraphQL.SEARCH_GROUP, params={'search': name, 'limit': limit}))

        return [graphql_to_group(node) for node in j['viewer']['groups']['nodes']]

    def searchForThreads(self, name, limit=1):
        """
        Find and get a thread by its name

        :param name: Name of the thread
        :param limit: The max. amount of groups to fetch
        :return: :class:`models.User`, :class:`models.Group` and :class:`models.Page` objects, ordered by relevance
        :rtype: list
        :raises: FBchatException if request failed
        """

        j = self.graphql_request(GraphQL(query=GraphQL.SEARCH_THREAD, params={'search': name, 'limit': limit}))

        rtn = []
        for node in j[name]['threads']['nodes']:
            if node['__typename'] == 'User':
                rtn.append(graphql_to_user(node))
            elif node['__typename'] == 'MessageThread':
                # MessageThread => Group thread
                rtn.append(graphql_to_group(node))
            elif node['__typename'] == 'Page':
                rtn.append(graphql_to_page(node))
            elif node['__typename'] == 'Group':
                # We don't handle Facebook "Groups"
                pass
            else:
                log.warning('Unknown __typename: {} in {}'.format(repr(node['__typename']), node))

        return rtn

    def _fetchInfo(self, *ids):
        data = {
            "ids[{}]".format(i): _id for i, _id in enumerate(ids)
        }
        j = self._post(self.req_url.INFO, data, fix_request=True, as_json=True)

        if j.get('payload') is None or j['payload'].get('profiles') is None:
            raise FBchatException('No users/pages returned: {}'.format(j))

        entries = {}
        for _id in j['payload']['profiles']:
            k = j['payload']['profiles'][_id]
            if k['type'] in ['user', 'friend']:
                entries[_id] = {
                    'id': _id,
                    'type': ThreadType.USER,
                    'url': k.get('uri'),
                    'first_name': k.get('firstName'),
                    'is_viewer_friend': k.get('is_friend'),
                    'gender': k.get('gender'),
                    'profile_picture': {'uri': k.get('thumbSrc')},
                    'name': k.get('name')
                }
            elif k['type'] == 'page':
                entries[_id] = {
                    'id': _id,
                    'type': ThreadType.PAGE,
                    'url': k.get('uri'),
                    'profile_picture': {'uri': k.get('thumbSrc')},
                    'name': k.get('name')
                }
            else:
                raise FBchatException('{} had an unknown thread type: {}'.format(_id, k))

        log.debug(entries)
        return entries

    def fetchUserInfo(self, *user_ids):
        """
        Get users' info from IDs, unordered

        .. warning::
            Sends two requests, to fetch all available info!

        :param user_ids: One or more user ID(s) to query
        :return: :class:`models.User` objects, labeled by their ID
        :rtype: dict
        :raises: FBchatException if request failed
        """

        threads = self.fetchThreadInfo(*user_ids)
        users = {}
        for k in threads:
            if threads[k].type == ThreadType.USER:
                users[k] = threads[k]
            else:
                raise FBchatUserError('Thread {} was not a user'.format(threads[k]))

        return users

    def fetchPageInfo(self, *page_ids):
        """
        Get pages' info from IDs, unordered

        .. warning::
            Sends two requests, to fetch all available info!

        :param page_ids: One or more page ID(s) to query
        :return: :class:`models.Page` objects, labeled by their ID
        :rtype: dict
        :raises: FBchatException if request failed
        """

        threads = self.fetchThreadInfo(*page_ids)
        pages = {}
        for k in threads:
            if threads[k].type == ThreadType.PAGE:
                pages[k] = threads[k]
            else:
                raise FBchatUserError('Thread {} was not a page'.format(threads[k]))

        return pages

    def fetchGroupInfo(self, *group_ids):
        """
        Get groups' info from IDs, unordered

        :param group_ids: One or more group ID(s) to query
        :return: :class:`models.Group` objects, labeled by their ID
        :rtype: dict
        :raises: FBchatException if request failed
        """

        threads = self.fetchThreadInfo(*group_ids)
        groups = {}
        for k in threads:
            if threads[k].type == ThreadType.GROUP:
                groups[k] = threads[k]
            else:
                raise FBchatUserError('Thread {} was not a group'.format(threads[k]))

        return groups

    def fetchThreadInfo(self, *thread_ids):
        """
        Get threads' info from IDs, unordered

        .. warning::
            Sends two requests if users or pages are present, to fetch all available info!

        :param thread_ids: One or more thread ID(s) to query
        :return: :class:`models.Thread` objects, labeled by their ID
        :rtype: dict
        :raises: FBchatException if request failed
        """

        queries = []
        for thread_id in thread_ids:
            queries.append(GraphQL(doc_id='1386147188135407', params={
                'id': thread_id,
                'message_limit': 0,
                'load_messages': False,
                'load_read_receipts': False,
                'before': None
            }))

        j = self.graphql_requests(*queries)

        for i, entry in enumerate(j):
            if entry.get('message_thread') is None:
                # If you don't have an existing thread with this person, attempt to retrieve user data anyways
                j[i]['message_thread'] = {
                    'thread_key': {
                        'other_user_id': thread_ids[i]
                    },
                    'thread_type': 'ONE_TO_ONE'
                }

        pages_and_user_ids = [k['message_thread']['thread_key']['other_user_id'] for k in j if k['message_thread'].get('thread_type') == 'ONE_TO_ONE']
        pages_and_users = {}
        if len(pages_and_user_ids) != 0:
            pages_and_users = self._fetchInfo(*pages_and_user_ids)

        rtn = {}
        for i, entry in enumerate(j):
            entry = entry['message_thread']
            if entry.get('thread_type') == 'GROUP':
                _id = entry['thread_key']['thread_fbid']
                rtn[_id] = graphql_to_group(entry)
            elif entry.get('thread_type') == 'ONE_TO_ONE':
                _id = entry['thread_key']['other_user_id']
                if pages_and_users.get(_id) is None:
                    raise FBchatException('Could not fetch thread {}'.format(_id))
                entry.update(pages_and_users[_id])
                if entry['type'] == ThreadType.USER:
                    rtn[_id] = graphql_to_user(entry)
                else:
                    rtn[_id] = graphql_to_page(entry)
            else:
                raise FBchatException('{} had an unknown thread type: {}'.format(thread_ids[i], entry))

        return rtn

    def fetchThreadMessages(self, thread_id=None, limit=20, before=None):
        """
        Get the last messages in a thread

        :param thread_id: User/Group ID to default to. See :ref:`intro_threads`
        :param limit: Max. number of messages to retrieve
        :param before: A timestamp, indicating from which point to retrieve messages
        :type limit: int
        :type before: int
        :return: :class:`models.Message` objects
        :rtype: list
        :raises: FBchatException if request failed
        """

        j = self.graphql_request(GraphQL(doc_id='1386147188135407', params={
            'id': thread_id,
            'message_limit': limit,
            'load_messages': True,
            'load_read_receipts': False,
            'before': before
        }))

        if j.get('message_thread') is None:
            raise FBchatException('Could not fetch thread {}: {}'.format(thread_id, j))

        return list(reversed([graphql_to_message(message) for message in j['message_thread']['messages']['nodes']]))

    def fetchThreadList(self, offset=0, limit=20):
        """Get thread list of your facebook account

        :param offset: The offset, from where in the list to recieve threads from
        :param limit: Max. number of threads to retrieve. Capped at 20
        :type offset: int
        :type limit: int
        :return: :class:`models.Thread` objects
        :rtype: list
        :raises: FBchatException if request failed
        """

        if limit > 20 or limit < 1:
            raise FBchatUserError('`limit` should be between 1 and 20')

        data = {
            'client' : self.client,
            'inbox[offset]' : offset,
            'inbox[limit]' : limit,
        }

        j = self._post(self.req_url.THREADS, data, fix_request=True, as_json=True)
        if j.get('payload') is None:
            raise FBchatException('Missing payload: {}, with data: {}'.format(j, data))

        participants = {}
        for p in j['payload']['participants']:
            if p['type'] == 'page':
                participants[p['fbid']] = Page(p['fbid'], url=p['href'], photo=p['image_src'], name=p['name'])
            elif p['type'] == 'user':
                participants[p['fbid']] = User(p['fbid'], url=p['href'], first_name=p['short_name'], is_friend=p['is_friend'], gender=GENDERS[p['gender']], photo=p['image_src'], name=p['name'])
            else:
                raise FBchatException('A participant had an unknown type {}: {}'.format(p['type'], p))

        entries = []
        for k in j['payload']['threads']:
            if k['thread_type'] == 1:
                if k['other_user_fbid'] not in participants:
                    raise FBchatException('The thread {} was not in participants: {}'.format(k, j['payload']))
                participants[k['other_user_fbid']].message_count = k['message_count']
                entries.append(participants[k['other_user_fbid']])
            elif k['thread_type'] == 2:
                entries.append(Group(k['thread_fbid'], participants=set([p.strip('fbid:') for p in k['participants']]), photo=k['image_src'], name=k['name'], message_count=k['message_count']))
            else:
                raise FBchatException('A thread had an unknown thread type: {}'.format(k))

        return entries

    def fetchUnread(self):
        """
        .. todo::
            Documenting this

        :raises: FBchatException if request failed
        """
        form = {
            'client': 'mercury_sync',
            'folders[0]': 'inbox',
            'last_action_timestamp': now() - 60*1000
            # 'last_action_timestamp': 0
        }

        j = self._post(self.req_url.THREAD_SYNC, form, fix_request=True, as_json=True)

        return {
            "message_counts": j['payload']['message_counts'],
            "unseen_threads": j['payload']['unseen_thread_ids']
        }

    """
    END FETCH METHODS
    """

    """
    SEND METHODS
    """

    def _getSendData(self, thread_id=None, thread_type=ThreadType.USER):
        """Returns the data needed to send a request to `SendURL`"""
        messageAndOTID = generateOfflineThreadingID()
        timestamp = now()
        date = datetime.now()
        data = {
            'client': self.client,
            'author' : 'fbid:' + str(self.uid),
            'timestamp' : timestamp,
            'timestamp_absolute' : 'Today',
            'timestamp_relative' : str(date.hour) + ":" + str(date.minute).zfill(2),
            'timestamp_time_passed' : '0',
            'is_unread' : False,
            'is_cleared' : False,
            'is_forward' : False,
            'is_filtered_content' : False,
            'is_filtered_content_bh': False,
            'is_filtered_content_account': False,
            'is_filtered_content_quasar': False,
            'is_filtered_content_invalid_app': False,
            'is_spoof_warning' : False,
            'source' : 'source:chat:web',
            'source_tags[0]' : 'source:chat',
            'html_body' : False,
            'ui_push_phase' : 'V3',
            'status' : '0',
            'offline_threading_id': messageAndOTID,
            'message_id' : messageAndOTID,
            'threading_id': generateMessageID(self.client_id),
            'ephemeral_ttl_mode:': '0',
            'manual_retry_cnt' : '0',
            'signatureID' : getSignatureID()
        }

        # Set recipient
        if thread_type in [ThreadType.USER, ThreadType.PAGE]:
            data["other_user_fbid"] = thread_id
        elif thread_type == ThreadType.GROUP:
            data["thread_fbid"] = thread_id

        return data

    def _doSendRequest(self, data):
        """Sends the data to `SendURL`, and returns the message ID or None on failure"""
        j = self._post(self.req_url.SEND, data, fix_request=True, as_json=True)

        try:
            message_ids = [action['message_id'] for action in j['payload']['actions'] if 'message_id' in action]
            if len(message_ids) != 1:
                log.warning("Got multiple message ids' back: {}".format(message_ids))
            message_id = message_ids[0]
        except (KeyError, IndexError) as e:
            raise FBchatException('Error when sending message: No message IDs could be found: {}'.format(j))

        # update JS token if received in response
        if j.get('jsmods') is not None and j['jsmods'].get('require') is not None:
            try:
                self.payloadDefault['fb_dtsg'] = j['jsmods']['require'][0][3][0]
            except (KeyError, IndexError) as e:
                log.warning('Error when updating fb_dtsg. Facebook might have changed protocol!')

        return message_id

    def sendMessage(self, message, thread_id=None, thread_type=ThreadType.USER):
        """
        Sends a message to a thread

        :param message: Message to send
        :param thread_id: User/Group ID to send to. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type thread_type: models.ThreadType
        :return: :ref:`Message ID <intro_message_ids>` of the sent message
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        data = self._getSendData(thread_id, thread_type)

        data['action_type'] = 'ma-type:user-generated-message'
        data['body'] = message or ''
        data['has_attachment'] = False
        data['specific_to_list[0]'] = 'fbid:' + thread_id
        data['specific_to_list[1]'] = 'fbid:' + self.uid

        return self._doSendRequest(data)

    def sendEmoji(self, emoji=None, size=EmojiSize.SMALL, thread_id=None, thread_type=ThreadType.USER):
        """
        Sends an emoji to a thread

        :param emoji: The chosen emoji to send. If not specified, the default `like` emoji is sent
        :param size: If not specified, a small emoji is sent
        :param thread_id: User/Group ID to send to. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type size: models.EmojiSize
        :type thread_type: models.ThreadType
        :return: :ref:`Message ID <intro_message_ids>` of the sent emoji
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        data = self._getSendData(thread_id, thread_type)
        data['action_type'] = 'ma-type:user-generated-message'
        data['has_attachment'] = False
        data['specific_to_list[0]'] = 'fbid:' + thread_id
        data['specific_to_list[1]'] = 'fbid:' + self.uid

        if emoji:
            data['body'] = emoji
            data['tags[0]'] = 'hot_emoji_size:' + size.name.lower()
        else:
            data["sticker_id"] = size.value

        return self._doSendRequest(data)

    def _uploadImage(self, image_path, data, mimetype):
        """Upload an image and get the image_id for sending in a message"""

        j = self._postFile(self.req_url.UPLOAD, {
            'file': (
                image_path,
                data,
                mimetype
            )
        }, fix_request=True, as_json=True)
        # Return the image_id
        return j['payload']['metadata'][0]['image_id']

    def sendImage(self, image_id, message=None, thread_id=None, thread_type=ThreadType.USER):
        """
        Sends an already uploaded image to a thread. (Used by :func:`Client.sendRemoteImage` and :func:`Client.sendLocalImage`)

        :param image_id: ID of an image that's already uploaded to Facebook
        :param message: Additional message
        :param thread_id: User/Group ID to send to. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type thread_type: models.ThreadType
        :return: :ref:`Message ID <intro_message_ids>` of the sent image
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        data = self._getSendData(thread_id, thread_type)

        data['action_type'] = 'ma-type:user-generated-message'
        data['body'] = message or ''
        data['has_attachment'] = True
        data['specific_to_list[0]'] = 'fbid:' + str(thread_id)
        data['specific_to_list[1]'] = 'fbid:' + str(self.uid)

        data['image_ids[0]'] = image_id

        return self._doSendRequest(data)

    def sendRemoteImage(self, image_url, message=None, thread_id=None, thread_type=ThreadType.USER):
        """
        Sends an image from a URL to a thread

        :param image_url: URL of an image to upload and send
        :param message: Additional message
        :param thread_id: User/Group ID to send to. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type thread_type: models.ThreadType
        :return: :ref:`Message ID <intro_message_ids>` of the sent image
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        mimetype = guess_type(image_url)[0]
        remote_image = requests.get(image_url).content
        image_id = self._uploadImage(image_url, remote_image, mimetype)
        return self.sendImage(image_id=image_id, message=message, thread_id=thread_id, thread_type=thread_type)

    def sendLocalImage(self, image_path, message=None, thread_id=None, thread_type=ThreadType.USER):
        """
        Sends a local image to a thread

        :param image_path: Path of an image to upload and send
        :param message: Additional message
        :param thread_id: User/Group ID to send to. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type thread_type: models.ThreadType
        :return: :ref:`Message ID <intro_message_ids>` of the sent image
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        mimetype = guess_type(image_path)[0]
        image_id = self._uploadImage(image_path, open(image_path, 'rb'), mimetype)
        return self.sendImage(image_id=image_id, message=message, thread_id=thread_id, thread_type=thread_type)

    def addUsersToGroup(self, user_ids, thread_id=None):
        """
        Adds users to a group.

        :param user_ids: One or more user IDs to add
        :param thread_id: Group ID to add people to. See :ref:`intro_threads`
        :type user_ids: list
        :return: :ref:`Message ID <intro_message_ids>` of the executed action
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, None)
        data = self._getSendData(thread_id, ThreadType.GROUP)

        data['action_type'] = 'ma-type:log-message'
        data['log_message_type'] = 'log:subscribe'

        if type(user_ids) is not list:
            user_ids = [user_ids]

        # Make list of users unique
        user_ids = set(user_ids)

        for i, user_id in enumerate(user_ids):
            if user_id == self.uid:
                raise FBchatUserError('Error when adding users: Cannot add self to group thread')
            else:
                data['log_message_data[added_participants][' + str(i) + ']'] = "fbid:" + str(user_id)

        return self._doSendRequest(data)

    def removeUserFromGroup(self, user_id, thread_id=None):
        """
        Removes users from a group.

        :param user_id: User ID to remove
        :param thread_id: Group ID to remove people from. See :ref:`intro_threads`
        :raises: FBchatException if request failed
        """

        thread_id, thread_type = self._getThread(thread_id, None)

        data = {
            "uid": user_id,
            "tid": thread_id
        }

        j = self._post(self.req_url.REMOVE_USER, data, fix_request=True, as_json=True)

    def changeThreadTitle(self, title, thread_id=None, thread_type=ThreadType.USER):
        """
        Changes title of a thread.
        If this is executed on a user thread, this will change the nickname of that user, effectively changing the title

        :param title: New group thread title
        :param thread_id: Group ID to change title of. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type thread_type: models.ThreadType
        :raises: FBchatException if request failed
        """

        thread_id, thread_type = self._getThread(thread_id, thread_type)

        if thread_type == ThreadType.USER:
            # The thread is a user, so we change the user's nickname
            return self.changeNickname(title, thread_id, thread_id=thread_id, thread_type=thread_type)
        else:
            data = self._getSendData(thread_id, thread_type)

            data['action_type'] = 'ma-type:log-message'
            data['log_message_data[name]'] = title
            data['log_message_type'] = 'log:thread-name'

            return self._doSendRequest(data)

    def changeNickname(self, nickname, user_id, thread_id=None, thread_type=ThreadType.USER):
        """
        Changes the nickname of a user in a thread

        :param nickname: New nickname
        :param user_id: User that will have their nickname changed
        :param thread_id: User/Group ID to change color of. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type thread_type: models.ThreadType
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, thread_type)

        data = {
            'nickname': nickname,
            'participant_id': user_id,
            'thread_or_other_fbid': thread_id
        }

        j = self._post(self.req_url.THREAD_NICKNAME, data, fix_request=True, as_json=True)

    def changeThreadColor(self, color, thread_id=None):
        """
        Changes thread color

        :param color: New thread color
        :param thread_id: User/Group ID to change color of. See :ref:`intro_threads`
        :type color: models.ThreadColor
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, None)

        data = {
            'color_choice': color.value,
            'thread_or_other_fbid': thread_id
        }

        j = self._post(self.req_url.THREAD_COLOR, data, fix_request=True, as_json=True)

    def changeThreadEmoji(self, emoji, thread_id=None):
        """
        Changes thread color

        Trivia: While changing the emoji, the Facebook web client actually sends multiple different requests, though only this one is required to make the change

        :param color: New thread emoji
        :param thread_id: User/Group ID to change emoji of. See :ref:`intro_threads`
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, None)

        data = {
            'emoji_choice': emoji,
            'thread_or_other_fbid': thread_id
        }

        j = self._post(self.req_url.THREAD_EMOJI, data, fix_request=True, as_json=True)

    def reactToMessage(self, message_id, reaction):
        """
        Reacts to a message

        :param message_id: :ref:`Message ID <intro_message_ids>` to react to
        :param reaction: Reaction emoji to use
        :type reaction: models.MessageReaction
        :raises: FBchatException if request failed
        """
        full_data = {
            "doc_id": 1491398900900362,
            "dpr": 1,
            "variables": {
                "data": {
                    "action": "ADD_REACTION",
                    "client_mutation_id": "1",
                    "actor_id": self.uid,
                    "message_id": str(message_id),
                    "reaction": reaction.value
                }
            }
        }
        try:
            url_part = urllib.parse.urlencode(full_data)
        except AttributeError:
            # This is a very hacky solution for python 2 support, please suggest a better one ;)
            url_part = urllib.urlencode(full_data)\
                .replace('u%27', '%27')\
                .replace('%5CU{}'.format(MessageReactionFix[reaction.value][0]), MessageReactionFix[reaction.value][1])

        j = self._post('{}/?{}'.format(self.req_url.MESSAGE_REACTION, url_part), fix_request=True, as_json=True)

    def setTypingStatus(self, status, thread_id=None, thread_type=None):
        """
        Sets users typing status in a thread

        :param status: Specify the typing status
        :param thread_id: User/Group ID to change status in. See :ref:`intro_threads`
        :param thread_type: See :ref:`intro_threads`
        :type status: models.TypingStatus
        :type thread_type: models.ThreadType
        :raises: FBchatException if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, None)

        data = {
            "typ": status.value,
            "thread": thread_id,
            "to": thread_id if thread_type == ThreadType.USER else "",
            "source": "mercury-chat"
        }

        j = self._post(self.req_url.TYPING, data, fix_request=True, as_json=True)

    """
    END SEND METHODS
    """

    def markAsDelivered(self, userID, threadID):
        """
        .. todo::
            Documenting this
        """
        data = {
            "message_ids[0]": threadID,
            "thread_ids[%s][0]" % userID: threadID
        }

        r = self._post(self.req_url.DELIVERED, data)
        return r.ok

    def markAsRead(self, userID):
        """
        .. todo::
            Documenting this
        """
        data = {
            "watermarkTimestamp": now(),
            "shouldSendReadReceipt": True,
            "ids[%s]" % userID: True
        }

        r = self._post(self.req_url.READ_STATUS, data)
        return r.ok

    def markAsSeen(self):
        """
        .. todo::
            Documenting this
        """
        r = self._post(self.req_url.MARK_SEEN, {"seen_timestamp": 0})
        return r.ok

    def friendConnect(self, friend_id):
        """
        .. todo::
            Documenting this
        """
        data = {
            "to_friend": friend_id,
            "action": "confirm"
        }

        r = self._post(self.req_url.CONNECT, data)
        return r.ok


    """
    LISTEN METHODS
    """

    def _ping(self, sticky, pool):
        data = {
            'channel': self.user_channel,
            'clientid': self.client_id,
            'partition': -2,
            'cap': 0,
            'uid': self.uid,
            'sticky_token': sticky,
            'sticky_pool': pool,
            'viewer_uid': self.uid,
            'state': 'active'
        }
        self._get(self.req_url.PING, data, fix_request=True, as_json=False)

    def _fetchSticky(self):
        """Call pull api to get sticky and pool parameter, newer api needs these parameters to work"""

        data = {
            "msgs_recv": 0,
            "channel": self.user_channel,
            "clientid": self.client_id
        }

        j = self._get(self.req_url.STICKY, data, fix_request=True, as_json=True)

        if j.get('lb_info') is None:
            raise FBchatException('Missing lb_info: {}'.format(j))

        return j['lb_info']['sticky'], j['lb_info']['pool']

    def _pullMessage(self, sticky, pool):
        """Call pull api with seq value to get message data."""

        data = {
            "msgs_recv": 0,
            "sticky_token": sticky,
            "sticky_pool": pool,
            "clientid": self.client_id,
        }

        j = self._get(ReqUrl.STICKY, data, fix_request=True, as_json=True)

        self.seq = j.get('seq', '0')
        return j

    def _parseMessage(self, content):
        """Get message and author name from content. May contain multiple messages in the content."""

        if 'ms' not in content: return

        for m in content["ms"]:
            mtype = m.get("type")
            try:
                # Things that directly change chat
                if mtype == "delta":

                    def getThreadIdAndThreadType(msg_metadata):
                        """Returns a tuple consisting of thread ID and thread type"""
                        id_thread = None
                        type_thread = None
                        if 'threadFbId' in msg_metadata['threadKey']:
                            id_thread = str(msg_metadata['threadKey']['threadFbId'])
                            type_thread = ThreadType.GROUP
                        elif 'otherUserFbId' in msg_metadata['threadKey']:
                            id_thread = str(msg_metadata['threadKey']['otherUserFbId'])
                            type_thread = ThreadType.USER
                        return id_thread, type_thread

                    delta = m["delta"]
                    delta_type = delta.get("type")
                    metadata = delta.get("messageMetadata")

                    if metadata is not None:
                        mid = metadata["messageId"]
                        author_id = str(metadata['actorFbId'])
                        ts = int(metadata.get("timestamp"))

                    # Added participants
                    if 'addedParticipants' in delta:
                        added_ids = [str(x['userFbId']) for x in delta['addedParticipants']]
                        thread_id = str(metadata['threadKey']['threadFbId'])
                        self.onPeopleAdded(mid=mid, added_ids=added_ids, author_id=author_id, thread_id=thread_id,
                                           ts=ts, msg=m)

                    # Left/removed participants
                    elif 'leftParticipantFbId' in delta:
                        removed_id = str(delta['leftParticipantFbId'])
                        thread_id = str(metadata['threadKey']['threadFbId'])
                        self.onPersonRemoved(mid=mid, removed_id=removed_id, author_id=author_id, thread_id=thread_id,
                                             ts=ts, msg=m)

                    # Color change
                    elif delta_type == "change_thread_theme":
                        new_color = graphql_color_to_enum(delta["untypedData"]["theme_color"])
                        thread_id, thread_type = getThreadIdAndThreadType(metadata)
                        self.onColorChange(mid=mid, author_id=author_id, new_color=new_color, thread_id=thread_id,
                                           thread_type=thread_type, ts=ts, metadata=metadata, msg=m)

                    # Emoji change
                    elif delta_type == "change_thread_icon":
                        new_emoji = delta["untypedData"]["thread_icon"]
                        thread_id, thread_type = getThreadIdAndThreadType(metadata)
                        self.onEmojiChange(mid=mid, author_id=author_id, new_emoji=new_emoji, thread_id=thread_id,
                                           thread_type=thread_type, ts=ts, metadata=metadata, msg=m)

                    # Thread title change
                    elif delta.get("class") == "ThreadName":
                        new_title = delta["name"]
                        thread_id, thread_type = getThreadIdAndThreadType(metadata)
                        self.onTitleChange(mid=mid, author_id=author_id, new_title=new_title, thread_id=thread_id,
                                           thread_type=thread_type, ts=ts, metadata=metadata, msg=m)

                    # Nickname change
                    elif delta_type == "change_thread_nickname":
                        changed_for = str(delta["untypedData"]["participant_id"])
                        new_nickname = delta["untypedData"]["nickname"]
                        thread_id, thread_type = getThreadIdAndThreadType(metadata)
                        self.onNicknameChange(mid=mid, author_id=author_id, changed_for=changed_for,
                                              new_nickname=new_nickname,
                                              thread_id=thread_id, thread_type=thread_type, ts=ts, metadata=metadata, msg=m)

                    # Message delivered
                    elif delta.get("class") == "DeliveryReceipt":
                        message_ids = delta["messageIds"]
                        delivered_for = str(delta.get("actorFbId") or delta["threadKey"]["otherUserFbId"])
                        ts = int(delta["deliveredWatermarkTimestampMs"])
                        thread_id, thread_type = getThreadIdAndThreadType(delta)
                        self.onMessageDelivered(msg_ids=message_ids, delivered_for=delivered_for,
                                                thread_id=thread_id, thread_type=thread_type, ts=ts, metadata=metadata, msg=m)

                    # Message seen
                    elif delta.get("class") == "ReadReceipt":
                        seen_by = str(delta.get("actorFbId") or delta["threadKey"]["otherUserFbId"])
                        seen_ts = int(delta["actionTimestampMs"])
                        delivered_ts = int(delta["watermarkTimestampMs"])
                        thread_id, thread_type = getThreadIdAndThreadType(delta)
                        self.onMessageSeen(seen_by=seen_by, thread_id=thread_id, thread_type=thread_type,
                                           seen_ts=seen_ts, ts=delivered_ts, metadata=metadata, msg=m)

                    # Messages marked as seen
                    elif delta.get("class") == "MarkRead":
                        seen_ts = int(delta.get("actionTimestampMs") or delta.get("actionTimestamp"))
                        delivered_ts = int(delta.get("watermarkTimestampMs") or delta.get("watermarkTimestamp"))

                        threads = []
                        if "folders" not in delta:
                            threads = [getThreadIdAndThreadType({"threadKey": thr}) for thr in delta.get("threadKeys")]

                        # thread_id, thread_type = getThreadIdAndThreadType(delta)
                        self.onMarkedSeen(threads=threads, seen_ts=seen_ts, ts=delivered_ts, metadata=delta, msg=m)

                    # New message
                    elif delta.get("class") == "NewMessage":
                        message = delta.get('body', '')
                        thread_id, thread_type = getThreadIdAndThreadType(metadata)
                        self.onMessage(mid=mid, author_id=author_id, message=message,
                                       thread_id=thread_id, thread_type=thread_type, ts=ts, metadata=metadata, msg=m)

                    # Unknown message type
                    else:
                        self.onUnknownMesssageType(msg=m)

                # Inbox
                elif mtype == "inbox":
                    self.onInbox(unseen=m["unseen"], unread=m["unread"], recent_unread=m["recent_unread"], msg=m)

                # Typing
                # elif mtype == "typ":
                #     author_id = str(m.get("from"))
                #     typing_status = TypingStatus(m.get("st"))
                #     self.onTyping(author_id=author_id, typing_status=typing_status)

                # Delivered

                # Seen
                # elif mtype == "m_read_receipt":
                #
                #     self.onSeen(m.get('realtime_viewer_fbid'), m.get('reader'), m.get('time'))

                # elif mtype in ['jewel_requests_add']:
                #         from_id = m['from']
                #         self.on_friend_request(from_id)

                # Happens on every login
                elif mtype == "qprimer":
                    self.onQprimer(ts=m.get("made"), msg=m)

                # Is sent before any other message
                elif mtype == "deltaflow":
                    pass

                # Chat timestamp
                elif mtype == "chatproxy-presence":
                    buddylist = {}
                    for _id in m.get('buddyList', {}):
                        payload = m['buddyList'][_id]
                        buddylist[_id] = payload.get('lat')
                    self.onChatTimestamp(buddylist=buddylist, msg=m)

                # Unknown message type
                else:
                    self.onUnknownMesssageType(msg=m)

            except Exception as e:
                self.onMessageError(exception=e, msg=m)

    def startListening(self):
        """
        Start listening from an external event loop

        :raises: FBchatException if request failed
        """
        self.listening = True
        self.sticky, self.pool = self._fetchSticky()

    def doOneListen(self, markAlive=True):
        """
        Does one cycle of the listening loop.
        This method is useful if you want to control fbchat from an external event loop

        :param markAlive: Whether this should ping the Facebook server before running
        :type markAlive: bool
        :return: Whether the loop should keep running
        :rtype: bool
        """
        try:
            if markAlive:
                self._ping(self.sticky, self.pool)
            content = self._pullMessage(self.sticky, self.pool)
            if content:
                self._parseMessage(content)
        except KeyboardInterrupt:
            return False
        except requests.Timeout:
            pass
        except requests.ConnectionError:
            # If the client has lost their internet connection, keep trying every 30 seconds
            time.sleep(30)
        except FBchatFacebookError as e:
            # Fix 502 and 503 pull errors
            if e.request_status_code in [502, 503]:
                self.req_url.change_pull_channel()
                self.startListening()
            else:
                raise e
        except Exception as e:
            return self.onListenError(exception=e)

        return True

    def stopListening(self):
        """Cleans up the variables from startListening"""
        self.listening = False
        self.sticky, self.pool = (None, None)

    def listen(self, markAlive=True):
        """
        Initializes and runs the listening loop continually

        :param markAlive: Whether this should ping the Facebook server each time the loop runs
        :type markAlive: bool
        """
        self.startListening()
        self.onListening()

        while self.listening and self.doOneListen(markAlive):
            pass

        self.stopListening()

    """
    END LISTEN METHODS
    """

    """
    EVENTS
    """

    def onLoggingIn(self, email=None):
        """
        Called when the client is logging in

        :param email: The email of the client
        """
        log.info("Logging in {}...".format(email))

    def on2FACode(self):
        """Called when a 2FA code is needed to progress"""
        return input('Please enter your 2FA code --> ')

    def onLoggedIn(self, email=None):
        """
        Called when the client is successfully logged in

        :param email: The email of the client
        """
        log.info("Login of {} successful.".format(email))

    def onListening(self):
        """Called when the client is listening"""
        log.info("Listening...")

    def onListenError(self, exception=None):
        """
        Called when an error was encountered while listening

        :param exception: The exception that was encountered
        :return: Whether the loop should keep running
        """
        log.exception('Got exception while listening')
        return True


    def onMessage(self, mid=None, author_id=None, message=None, thread_id=None, thread_type=ThreadType.USER, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and somebody sends a message

        :param mid: The message ID
        :param author_id: The ID of the author
        :param message: The message
        :param thread_id: Thread ID that the message was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the message was sent to. See :ref:`intro_threads`
        :param ts: The timestamp of the message
        :param metadata: Extra metadata about the message
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        log.info("Message from {} in {} ({}): {}".format(author_id, thread_id, thread_type.name, message))

    def onColorChange(self, mid=None, author_id=None, new_color=None, thread_id=None, thread_type=ThreadType.USER, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and somebody changes a thread's color

        :param mid: The action ID
        :param author_id: The ID of the person who changed the color
        :param new_color: The new color
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type new_color: models.ThreadColor
        :type thread_type: models.ThreadType
        """
        log.info("Color change from {} in {} ({}): {}".format(author_id, thread_id, thread_type.name, new_color))

    def onEmojiChange(self, mid=None, author_id=None, new_emoji=None, thread_id=None, thread_type=ThreadType.USER, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and somebody changes a thread's emoji

        :param mid: The action ID
        :param author_id: The ID of the person who changed the emoji
        :param new_emoji: The new emoji
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        log.info("Emoji change from {} in {} ({}): {}".format(author_id, thread_id, thread_type.name, new_emoji))

    def onTitleChange(self, mid=None, author_id=None, new_title=None, thread_id=None, thread_type=ThreadType.USER, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and somebody changes the title of a thread

        :param mid: The action ID
        :param author_id: The ID of the person who changed the title
        :param new_title: The new title
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        log.info("Title change from {} in {} ({}): {}".format(author_id, thread_id, thread_type.name, new_title))

    def onNicknameChange(self, mid=None, author_id=None, changed_for=None, new_nickname=None, thread_id=None, thread_type=ThreadType.USER, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and somebody changes the nickname of a person

        :param mid: The action ID
        :param author_id: The ID of the person who changed the nickname
        :param changed_for: The ID of the person whom got their nickname changed
        :param new_nickname: The new nickname
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        log.info("Nickname change from {} in {} ({}) for {}: {}".format(author_id, thread_id, thread_type.name, changed_for, new_nickname))


    def onMessageSeen(self, seen_by=None, thread_id=None, thread_type=ThreadType.USER, seen_ts=None, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and somebody marks a message as seen

        :param seen_by: The ID of the person who marked the message as seen
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param seen_ts: A timestamp of when the person saw the message
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        log.info("Messages seen by {} in {} ({}) at {}s".format(seen_by, thread_id, thread_type.name, seen_ts/1000))

    def onMessageDelivered(self, msg_ids=None, delivered_for=None, thread_id=None, thread_type=ThreadType.USER, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and somebody marks messages as delivered

        :param msg_ids: The messages that are marked as delivered
        :param delivered_for: The person that marked the messages as delivered
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        log.info("Messages {} delivered to {} in {} ({}) at {}s".format(msg_ids, delivered_for, thread_id, thread_type.name, ts/1000))

    def onMarkedSeen(self, threads=None, seen_ts=None, ts=None, metadata=None, msg={}):
        """
        Called when the client is listening, and the client has successfully marked threads as seen

        :param threads: The threads that were marked
        :param author_id: The ID of the person who changed the emoji
        :param seen_ts: A timestamp of when the threads were seen
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        log.info("Marked messages as seen in threads {} at {}s".format([(x[0], x[1].name) for x in threads], seen_ts/1000))


    def onPeopleAdded(self, mid=None, added_ids=None, author_id=None, thread_id=None, ts=None, msg={}):
        """
        Called when the client is listening, and somebody adds people to a group thread

        :param mid: The action ID
        :param added_ids: The IDs of the people who got added
        :param author_id: The ID of the person who added the people
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        log.info("{} added: {}".format(author_id, ', '.join(added_ids)))

    def onPersonRemoved(self, mid=None, removed_id=None, author_id=None, thread_id=None, ts=None, msg={}):
        """
        Called when the client is listening, and somebody removes a person from a group thread

        :param mid: The action ID
        :param removed_id: The ID of the person who got removed
        :param author_id: The ID of the person who removed the person
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        log.info("{} removed: {}".format(author_id, removed_id))

    def onFriendRequest(self, from_id=None, msg={}):
        """
        Called when the client is listening, and somebody sends a friend request

        :param from_id: The ID of the person that sent the request
        :param msg: A full set of the data recieved
        """
        log.info("Friend request from {}".format(from_id))

    def onInbox(self, unseen=None, unread=None, recent_unread=None, msg={}):
        """
        .. todo::
            Documenting this

        :param unseen: --
        :param unread: --
        :param recent_unread: --
        :param msg: A full set of the data recieved
        """
        log.info('Inbox event: {}, {}, {}'.format(unseen, unread, recent_unread))

    def onQprimer(self, ts=None, msg={}):
        """
        Called when the client just started listening

        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        pass

    def onChatTimestamp(self, buddylist={}, msg={}):
        """
        Called when the client receives chat online presence update

        :param buddylist: A list of dicts with friend id and last seen timestamp
        :param msg: A full set of the data recieved
        """
        log.debug('Chat Timestamps received: {}'.format(buddylist))

    def onUnknownMesssageType(self, msg={}):
        """
        Called when the client is listening, and some unknown data was recieved

        :param msg: A full set of the data recieved
        """
        log.debug('Unknown message received: {}'.format(msg))

    def onMessageError(self, exception=None, msg={}):
        """
        Called when an error was encountered while parsing recieved data

        :param exception: The exception that was encountered
        :param msg: A full set of the data recieved
        """
        log.exception('Exception in parsing of {}'.format(msg))

    """
    END EVENTS
    """
