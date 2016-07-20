#!/usr/bin/env python
"""
Copyright (c) 2016 superfsm@gmail.com

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
OR OTHER DEALINGS IN THE SOFTWARE.

"""

import os
import re
import json
import struct
import logging
import requests
import argparse
import getpass
import time

from client import Client

from pgoapi import PGoApi
from pgoapi.utilities import f2i, h2f

from google.protobuf.internal import encoder
from geopy.geocoders import GoogleV3
from s2sphere import CellId, LatLng

log = logging.getLogger(__name__)

def get_pos_by_name(location_name):
    geolocator = GoogleV3()
    loc = geolocator.geocode(location_name)

    log.info('Your given location: %s', loc.address.encode('utf-8'))
    log.info('lat/long/alt: %s %s %s', loc.latitude, loc.longitude, loc.altitude)

    return (loc.latitude, loc.longitude)

def init_config():
    parser = argparse.ArgumentParser()
    config_file = "config.json"

    # If config file exists, load variables from json
    load   = {}
    if os.path.isfile(config_file):
        with open(config_file) as data:
            load.update(json.load(data))

    # Read passed in Arguments
    required = lambda x: not x in load
    parser.add_argument("-a", "--auth_service", help="Auth Service ('ptc' or 'google')",
        required=required("auth_service"))
    parser.add_argument("-u", "--username", help="Username", required=required("username"))
    parser.add_argument("-p", "--password", help="Password")
    parser.add_argument("-l", "--location", help="Location", required=required("location"))
    parser.add_argument("-d", "--debug", help="Debug Mode", action='store_true')
    parser.add_argument("-t", "--test", help="Only parse the specified location", action='store_true')
    parser.set_defaults(DEBUG=False, TEST=False)
    config = parser.parse_args()

    # Passed in arguments shoud trump
    for key in config.__dict__:
        if key in load and config.__dict__[key] == None:
            config.__dict__[key] = load[key]

    # Get password fron stdin if no exist
    if config.__dict__['password'] is None:
        config.__dict__['password'] = getpass.getpass('Password:')

    if config.auth_service not in ['ptc', 'google']:
      log.error("Invalid Auth service specified! ('ptc' or 'google')")
      return None

    return config


def main():
    # log settings
    # log format
    logging.basicConfig(level=logging.ERROR, format='%(asctime)s [%(module)10s] [%(levelname)5s] %(message)s')
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("pgoapi").setLevel(logging.WARNING)
    logging.getLogger("rpc_api").setLevel(logging.WARNING)
    logging.getLogger("client").setLevel(logging.INFO)

    config = init_config()
    if not config:
        return

    if config.debug:
        logging.getLogger("client").setLevel(logging.DEBUG)
        logging.getLogger("requests").setLevel(logging.DEBUG)
        logging.getLogger("pgoapi").setLevel(logging.DEBUG)
        logging.getLogger("rpc_api").setLevel(logging.DEBUG)

    # provide player position on the earth
    position = get_pos_by_name(config.location)
    if config.test:
        return

    # instantiate client:
    client = Client()

    # login
    if not client.login(str(config.auth_service), str(config.username), str(config.password)):
        return

    # set initial location
    client.jump_to(*position)

    ################################################

    # Operate on client
    # client.move_to(*position)
    # client.scan()
    # client.get_player()
    client.get_inventory().call()
    # client.get_map_objects()
    # client.test()

    ################################################ Test code

    # client.scan().call()
    # client.sort_map()

    # for i in client.pokestop:
    #     print i


    # while True:
    #     # client.scan()
    #     # client.sort_map()
    #     print "# of stop =", len(client.pokestop)
    #     for i in client.pokestop:
    #         print 'Moving to Pokestop', i['id'], i['dist']
    #         client.move_to_obj(i)
    #         client.spin(i)
    #         time.sleep(5)

    ################################################ Need to move to client.py

    # release/transfer a pokemon and get candy for it
    # ----------------------
    #api.release_pokemon(pokemon_id = <your pokemonid>)

    # get download settings call
    # ----------------------
    #api.download_settings(hash="4a2e9bc330dae60e7b74fc85b98868ab4700802e")

    # execute the RPC call
    # response_dict = api.call()
    # print('Response dictionary: \n\r{}'.format(json.dumps(response_dict, indent=2)))

    # alternative:
    # api.get_player().get_inventory().get_map_objects().download_settings(hash="4a2e9bc330dae60e7b74fc85b98868ab4700802e").call()

if __name__ == '__main__':
    main()
