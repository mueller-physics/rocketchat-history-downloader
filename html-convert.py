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
            outfile.write('</div>\n')








if __name__ == "__main__":
    main()

