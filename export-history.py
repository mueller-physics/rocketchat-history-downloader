"""
Description:
    Download and store raw JSON channel history for joined standard
    channels and direct messages. Specify start and/or end date bounds
    or use defaults of room creation and yesterday (respectively)

Dependencies:
    pipenv install
        rocketchat_API - Python API wrapper for Rocket.Chat
            https://github.com/jadolg/rocketchat_API
            (pipenv install rocketchat_API)

    Actual Rocket.Chat API
        https://rocket.chat/docs/developer-guides/rest-api/channels/history/

Configuration:
    settings.cfg contains Rocket.Chat login information and file paths

Commands:
    pipenv run python export-history.py settings.cfg
    pipenv run python export-history.py -s 2000-01-01 -e 2018-01-01 -r settings.cfg
    etc

Notes:
    None

Author:
    Ben Willard <willardb@gmail.com> (https://github.com/willardb)
"""
import datetime
import pickle
import os
import logging
import pprint
import argparse
import configparser
import json
import re
import requests
import urllib
import re
from time import sleep
from rocketchat_API.rocketchat import RocketChat



#
# Initialize stuff
#
VERSION = 1.1

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
SHORT_DATE_FORMAT = "%Y-%m-%d"
ONE_DAY = datetime.timedelta(days=1)
FOURTY_DAYS = datetime.timedelta(days=40)
TODAY = datetime.datetime.today()
YESTERDAY = TODAY - ONE_DAY
NULL_DATE = datetime.datetime(1, 1, 1, 0, 0, 0, 0)


#
# Functions
#
def get_rocketchat_timestamp(in_date):
    """Take in a date and return it converted to a Rocket.Chat timestamp"""
    s = in_date.strftime(DATE_FORMAT)
    return s[:-4] + 'Z'

def incr_by_day_or_month(in_date, month_block):

    if month_block:
        out_date = in_date.replace(day=1)
        out_date = out_date + FOURTY_DAYS
        out_date = out_date.replace(day=1)
    else:
        out_date = in_date + ONE_DAY
    return out_date



def assemble_state(state_array, room_json, room_type, ims_name = None ):
    """Build the state_array that tracks what needs to be saved"""
    for channel in room_json[room_type]:
        if channel['_id'] not in state_array:
            
            displayname = channel['_id']

            if 'name' in channel:
                displayname = channel['name']

            if 'fname' in channel:
                displayname = channel['fname']

            if room_type == 'ims' and not ims_name == None:

                # renaming a 2-person chat to the username of the other chat partner
                if not ims_name == None and channel.get('usersCount',-1) == 2:
                    n = channel.get('usernames', [ displayname, displayname ])
                    if n[0] == ims_name:
                        displayname = n[1]
                    else:
                        displayname = n[0]

                displayname = 'direct-'+displayname


            state_array[channel['_id']] = {
                'name': displayname,
                'type': room_type,
                'lastsaved': NULL_DATE,
                'begintime': (datetime
                              .datetime
                              .strptime(channel['ts'], DATE_FORMAT)
                              .replace(hour=0, minute=0, second=0, microsecond=0)),
            }
        # Channels without messages don't have a lm field
        if channel.get('lm'):
            lm = datetime.datetime.strptime(channel['lm'], DATE_FORMAT)
        else:
            lm = NULL_DATE
        state_array[channel['_id']]['lastmessage'] = lm


def upgrade_state_schema(state_array, old_schema_version, logger):
    """Modify the datain the saved state file as needed for new versions"""
    cur_schema_version = old_schema_version
    logger.info('State schema version of '
                + str(old_schema_version)
                + ' is less than current version of '
                + str(VERSION))
    if cur_schema_version < 1.1:
        logger.info('Upgrading ' + str(cur_schema_version) + ' to 1.1...')
        # 1.0->1.1 update values for 'type' key
        t_typemap = {'direct': 'ims', 'channel': 'channels'}
        for t_id in state_array:
            state_array[t_id]['type'] = t_typemap[state_array[t_id]['type']]
        state_array['_meta'] = {'schema_version': 1.1}
        logger.info('Finished ' + str(cur_schema_version) + ' to 1.1...')
        cur_schema_version = state_array['_meta']['schema_version']
        logger.debug('\n' + pprint.pformat(state_array))


#
# Main
#
def main():
    """Main export process"""
    # args
    argparser_main = argparse.ArgumentParser()
    argparser_main.add_argument('configfile',
                                help='Location of configuration file')
    argparser_main.add_argument('-s', '--datestart',
                                help='Datetime to use for global starting point ' + \
                                'e.g. 2016-01-01 (implied T00:00:00.000Z)')
    argparser_main.add_argument('-e', '--dateend',
                                help='Datetime to use for global ending point ' + \
                                'e.g. 2016-01-01 (implied T23:59:59.999Z)')
    argparser_main.add_argument('-r', '--readonlystate',
                                help='Do not create or update history state file.',
                                action="store_true")
    argparser_main.add_argument('-l', '--list',
                                help='Print a room list (for use in "include" and "exclude") and exit',
                                action="store_true")

    args = argparser_main.parse_args()

    start_time = (datetime
                  .datetime
                  .strptime(args.datestart,
                            SHORT_DATE_FORMAT)
                  .replace(hour=0,
                           minute=0,
                           second=0,
                           microsecond=0) if args.datestart else None)

    end_time = (datetime
                .datetime
                .strptime(args.dateend,
                          SHORT_DATE_FORMAT)
                .replace(hour=23,
                         minute=59,
                         second=59,
                         microsecond=999999) if args.dateend \
                         else YESTERDAY.replace(hour=23,
                                                minute=59,
                                                second=59,
                                                microsecond=999999))


    # config
    config_main = configparser.ConfigParser()
    config_main.read(args.configfile)

    polite_pause = int(config_main['rc-api']['pause_seconds'])
    count_max = int(config_main['rc-api']['max_msg_count_per_day'])
    output_dir = config_main['files']['history_output_dir']
    state_file = config_main['files']['history_statefile']

    skip_if_file_exists = config_main.get('files', 'skip_when_file_exists', fallback = False)

    rc_auth = config_main.get('rc-api','auth', fallback='classic')
    rc_user = config_main['rc-api']['user']
    rc_pass = config_main['rc-api']['pass']
    rc_server = config_main['rc-api']['server']

    #month_block = config_main.get('files','month_blocks', fallback=False)
    month_block = config_main.get('files','month_blocks')

    file_prefix = config_main.get('files','file_prefix', fallback='');
    file_folder = config_main.get('files','file_folder', fallback='attachments');

    
    # include and exclude rooms
    rooms_exclude = []
    rooms_include = []

    if  config_main.has_section('rooms') :
        rooms_include = json.loads( config_main.get('rooms','include', fallback = "[]"))
        rooms_exclude = json.loads( config_main.get('rooms','exclude', fallback = "[]"))


    # logging
    logger = logging.getLogger('export-history')
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler('export-history.log')
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False
    
    room_state = {}
    

    logger.info('BEGIN execution at %s', str(datetime.datetime.today()))
    logger.debug('Command line arguments: %s', pprint.pformat(args))

    if args.readonlystate:
        logger.info('Running in readonly state mode. No state file updates.')

    if os.path.isfile(state_file):
        logger.debug('LOAD state from %s', state_file)
        sf = open(state_file, 'rb')
        room_state = pickle.load(sf)
        sf.close()
        logger.debug('\n%s', pprint.pformat(room_state))
        schema_version = 1.0 if '_meta' not in room_state else room_state['_meta']['schema_version']
        if schema_version < VERSION:
            upgrade_state_schema(room_state, schema_version, logger)

    else:
        logger.debug('No state file at %s, so state will be created', state_file)
        room_state = {'_meta': {'schema_version': VERSION}}

    if rooms_exclude:
        logger.debug("Excluded rooms: " + ", ".join(rooms_exclude))
    if rooms_include:
        logger.debug("Included rooms: " + ", ".join(rooms_include))

    if ( rc_auth == "token"):
        logger.debug('Initialize rocket.chat API connection (token)')
        rocket = RocketChat(auth_token=rc_pass, user_id=rc_user, server_url=rc_server)
    else:
        logger.debug('Initialize rocket.chat API connection (user/password)')
        rocket = RocketChat(rc_user, rc_pass, server_url=rc_server)

    sleep(polite_pause)
    if skip_if_file_exists :
        logger.debug("Skip set to TRUE: will not retrieve history for days where a file already exists")

    if month_block:
        logger.debug("Month block set to TRUE")

    logger.debug('LOAD / UPDATE room state')
    assemble_state(room_state, rocket.channels_list_joined().json(), 'channels')

    assemble_state(room_state, rocket.im_list().json(), 'ims', ims_name = config_main.get('rooms','ims_ownname', fallback = None))

    assemble_state(room_state, rocket.groups_list().json(), 'groups')

    if args.list:
        for channel_id, channel_data in room_state.items():
            if channel_id != '_meta':  # skip state metadata which is not a channel
                logger.info( "subscribed: \""+channel_data['name'] + "\" (type " +channel_data['type'] + ")")
        return    

    sleep(polite_pause)

    userkeys = []

    for channel_id, channel_data in room_state.items():

        if channel_id != '_meta':  # skip state metadata which is not a channel
            logger.info('------------------------')

            if channel_data['name'] in rooms_exclude:
                logger.info('Skipping room (in exclude list): '+channel_data['name'])
                continue

            if rooms_include and channel_data['name'] not in rooms_include:
                logger.info('Skipping room (not in include list): '+channel_data['name'])
                continue

            logger.info('Processing room: ' + channel_id + ' - ' + channel_data['name'])

            logger.debug('Global start time: %s', str(start_time))
            logger.debug('Global end time: %s', str(end_time))
            logger.debug('Room start ts: %s', str(channel_data['begintime']))
            logger.debug('Last message: %s', str(channel_data['lastmessage']))
            logger.debug('Last saved: %s ', str(channel_data['lastsaved']))

            if start_time is not None:
                # use globally specified start time but if the start time
                # is before the channel existed, fast-forward to its creation
                t_oldest = channel_data['begintime'] if channel_data['begintime'] > start_time \
                else start_time
            elif channel_data['lastsaved'] != NULL_DATE:
                # no global override for start time, so use a tick after
                # the last saved date if it exists
                t_oldest = channel_data['lastsaved'] + datetime.timedelta(microseconds=1)
            else:
                # nothing specified at all so use the beginning time of the channel
                t_oldest = channel_data['begintime']

            t_latest = NULL_DATE

            if month_block:
                t_oldest = t_oldest.replace(day=1)
                logger.info('Month mode: grabbing messages in blocks of months')

            if (t_oldest < end_time) and (t_oldest < channel_data['lastmessage']):
                logger.info('Grabbing messages since '
                            + str(t_oldest)
                            + ' through '
                            + str(end_time))
            else:
                logger.info('Nothing to grab between '
                            + str(t_oldest)
                            + ' through '
                            + str(end_time))

            while (t_oldest < end_time) and (t_oldest < channel_data['lastmessage']):
                logger.info('')

                if month_block:
                    outfilename = t_oldest.strftime('%Y-%m')+'-NN'
                else:
                    outfilename = t_oldest.strftime('%Y-%m-%d')

                if skip_if_file_exists :
                    while ( os.path.isfile( output_dir
                        + outfilename
                        + '-'
                        + channel_data['name']
                        + '.json' )):

                        t_oldest = incr_by_day_or_month( t_oldest, month_block)

                        if month_block:
                            outfilename = t_oldest.strftime('%Y-%m')+'-NN'
                        else:
                            outfilename = t_oldest.strftime('%Y-%m-%d')

                        logger.info('skipping %s (as history file already exists) '+outfilename, get_rocketchat_timestamp(t_oldest))

                t_latest = incr_by_day_or_month( t_oldest, month_block) - datetime.timedelta(microseconds=1)

                logger.info('start: %s', get_rocketchat_timestamp(t_oldest))

                history_data_obj = {}
                retry_flag = True
                retry_count = 0

                while retry_flag:
                    retry_count += 1
                    logger.debug('invoking API to get messages (attempt %d)', retry_count)
                    if channel_data['type'] == 'channels':
                        history_data_obj = rocket.channels_history(
                            channel_id,
                            count=count_max,
                            include='true',
                            latest=get_rocketchat_timestamp(t_latest),
                            oldest=get_rocketchat_timestamp(t_oldest))
                    elif channel_data['type'] == 'ims':
                        history_data_obj = rocket.im_history(
                            channel_id,
                            count=count_max,
                            include='true',
                            latest=get_rocketchat_timestamp(t_latest),
                            oldest=get_rocketchat_timestamp(t_oldest))
                    elif channel_data['type'] == 'groups':
                        history_data_obj = rocket.groups_history(
                            channel_id,
                            count=count_max,
                            include='true',
                            latest=get_rocketchat_timestamp(t_latest),
                            oldest=get_rocketchat_timestamp(t_oldest))

                    history_data = history_data_obj.json()
                    history_data_text = history_data_obj.text

                    if not history_data['success']:
                        error_text = history_data['error']
                        logger.error('Error response from API endpoint: %s', error_text)
                        if 'error-too-many-requests' in error_text:
                            seconds_search = re.search(r'must wait (\d+) seconds',
                                                       error_text,
                                                       re.IGNORECASE)
                            if seconds_search:
                                seconds_to_wait = int(seconds_search.group(1))
                                if seconds_to_wait < 300:
                                    polite_pause += seconds_to_wait \
                                    if seconds_to_wait < polite_pause \
                                    else polite_pause
                                    logger.error('Attempting handle API rate limit error by \
                                                 sleeping for %d and updating polite_pause \
                                                 to %d for the duration of this execution',
                                                 seconds_to_wait, polite_pause)
                                    sleep(seconds_to_wait)
                                else:
                                    raise Exception('Unresonable amount of time to wait '
                                                    + 'for API rate limit')
                            else:
                                raise Exception('Can not parse too-many-requests error message')
                        else:
                            raise Exception('Untrapped error response from history API: '
                                            + '{error_text}'
                                            .format(error_text=error_text))
                    else:
                        retry_flag = False


                num_messages = len(history_data['messages'])
                logger.info('Messages found: %s', str(num_messages))

                # attachments download
                for m in history_data['messages']:
                    for a in m.get('attachments', []):
                        if 'title_link' in a:
                            urlname = a.get('title_link')

                            if urlname.startswith(file_prefix):
                                diskname = urlname[len(file_prefix):]

                            diskname = urllib.parse.unquote(diskname)
                            diskname = re.sub('\s+|\:','_',diskname)                             

                            diskname = diskname.replace('/','-')
                            diskpath = output_dir + file_folder +'/'+ diskname

                            if not os.path.isfile( diskpath ):
                                # TODO: this only works with access tokens. Would need a switching
                                # statement for the username/password option
                                req = requests.get(rc_server + urlname,
                                    headers={ 'X-Auth-Token': rc_pass , 'X-User-Id': rc_user })

                                if req.status_code == 200 :
                                    fout = open( diskpath, 'wb')
                                    fout.write( req.content )
                                    logger.debug('Downloaded attachment: ' +urlname+' --> '+diskname)
                                else:
                                    logger.warn('Failed to download: '+urlname)
                                    
                                sleep(polite_pause)

                            else:
                                    logger.debug('Attachment exists: '+diskname)

                # avatar download
                for m in history_data['messages']:
                        a =  m.get('u',[]).get('username','none')
                        
                        if a in userkeys:
                                continue

                        diskpath =  output_dir + '/avatar/' + a + '.jpg'

                        if not os.path.isfile(diskpath):
                                req = requests.get( rc_server + '/avatar/' + a + '?format=jpeg' )
                                if req.status_code == 200 :
                                    fout = open( diskpath, 'wb')
                                    fout.write( req.content )
                                    logger.debug('Downloaded avatar: ' + a)
                                    userkeys.append(a)
                                else:
                                    logger.warn('Failed to download avatar: '+ a)
                                sleep(polite_pause)
                        else:
                                logger.debug('Avatar on disk:' + a)
                                userkeys.append(a)

                                 



                if num_messages > 0:
                    with open(output_dir
                              + outfilename
                              + '-'
                              + re.sub('\s+','_',channel_data['name'])
                              + '.json', 'wb') as f:
                        f.write(history_data_text.encode('utf-8').strip())
                elif num_messages > count_max:
                    logger.error('Too many messages for this room today. SKIPPING.')

                logger.info('end: %s', get_rocketchat_timestamp(t_latest))
                logger.info('')
                t_oldest = incr_by_day_or_month( t_oldest, month_block)
                sleep(polite_pause)

            logger.info('------------------------\n')

        # I am changing what 'lastsaved' means here. It used to denote the
        # last time a file was actually saved to disk for this channel
        # but I think it is more useful if it represents the maximum time for
        # which the channel has been checked. this will reduce lots
        # of unnecessary day checks if a channel is dormant for a while and then
        # suddenly has a message in it. This is only helpful if the
        # history export script is run on a periodic basis.
        room_state[channel_id]['lastsaved'] = end_time

    if not args.readonlystate:
        logger.debug('UPDATE state file')
        logger.debug('\n%s', pprint.pformat(room_state))
        sf = open(state_file, 'wb')
        pickle.dump(room_state, sf)
        sf.close()
    else:
        logger.debug('Running in readonly state mode: SKIP updating state file')

    logger.info('END execution at %s\n------------------------\n\n',
                str(datetime.datetime.today()))

if __name__ == "__main__":
    main()
