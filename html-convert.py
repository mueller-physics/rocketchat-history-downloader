"""

Description:
    Converts the JSON channel history into a rudimentary HTML file
    for offline vieving

Commands:
    pipenv run python html-convert.py channel-name

"""

import json
import datetime
import os
import logging
import pprint
import argparse
import configparser
import requests

def main():

    argparser_main = argparse.ArgumentParser()
    argparser_main.add_argument('--config',
                                help='Location of configuration file')
    
    argparser_main.add_argument('channel',
                                help='Name of the channel to export')

    args = argparser_main.parse_args()

    logger = logging.getLogger('html-export')    
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    logger.addHandler(ch)

    config = configparser.ConfigParser()
    config.read( args.config if args.config else 'settings.cfg' )

    input_dir = config['files']['history_output_dir']

    rc_user = config['rc-api']['user']
    rc_pass = config['rc-api']['pass']
    rc_server = config['rc-api']['server']

    file_prefix = config.get('files','file_prefix', fallback='');
    file_folder = config.get('files','file_folder', fallback='attachments');

    logger.debug("Input folder: "+input_dir)

    outfile = open( args.channel+'.html', 'w')
    
    outfile.write('<!DOCTYPE html><html><head><meta charset="utf-8" />')
    outfile.write('<title>Export Rocketchat channel "'+args.channel+'"</title>')
    outfile.write('<link rel="stylesheet" href="simple.css" />');
    outfile.write('</head><body>\n')

    for filename in sorted( os.listdir(input_dir) ):
        if not filename[11:-5] == args.channel:
            continue

        data = json.load( open( input_dir + filename ))

        for m in data['messages']:
            outfile.write('<div class="message">\n')
            outfile.write('<div class="user">' + m['u']['name'] + '</div>\n')

            timestamp = m['ts'];

            outfile.write('<div class="stamp">' +"("+m['u']['username'] +") " 
                + timestamp[:10]+' ' +timestamp[11:19]  +'</div>\n')

            outfile.write('<div class="content">' + m['msg'] + '</div>\n')

            for a in m.get('attachments', []):

                #logger.debug(a)

                if 'title_link' in a:
                    urlname = a.get('title_link')
                    diskname = urlname

                    if urlname.startswith(file_prefix):
                        diskname = urlname[len(file_prefix):]

                    diskname = diskname.replace('/','-')

                    diskpath = input_dir + file_folder +'/'+ diskname

                    if not os.path.isfile( diskpath ):
                        req = requests.get(rc_server + urlname,
                            headers={ 'X-Auth-Token': rc_pass , 'X-User-Id': rc_user })

                        if req.status_code == 200 :
                            fout = open( diskpath, 'wb')
                            fout.write( req.content )
                            logger.debug('Downloaded: ' +urlname+' --> '+diskname)
                        else:
                            logger.warn('Failed download: '+urlname)

                    # no 'else' here, have to check if file was downloaded
                    if os.path.isfile(diskpath):
                        outfile.write('<div class="attachment"><a href="'
                            + diskpath+'">'
                            + os.path.basename(urlname)
                            +'</a></div>')


            outfile.write('</div>\n')





if __name__ == "__main__":
    main()

