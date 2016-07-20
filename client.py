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

from pgoapi import PGoApi
from pgoapi.utilities import f2i, h2f

from google.protobuf.internal import encoder
from geopy.geocoders import GoogleV3
from geopy.distance import great_circle
from s2sphere import CellId, LatLng

log = logging.getLogger(__name__)

class Client:

    def __init__(self):
        self._api = PGoApi()

        self._lat = 0
        self._lng = 0
        self._alt = 0

        self._lat_f2i = 0
        self._lng_f2i = 0
        self._alt_f2i = 0

        self.profile = {}
        self.item = {}
        self.pokemon = {}

        self.pokestop = []
        self.wild = []

    def get_position(self):
        return (self._lat, self._lng)

    # Move to object
    def move_to_obj(self, obj):
        self.move_to(obj['lat'], obj['lng'])

    # Move to position with at most 20m/s
    def move_to(self, lat, lng):
        a = (self._lat, self._lng)
        b = (lat, lng)

        dist = great_circle(a, b).meters
        steps = int(dist / 20) + 1

        delta_lat = (lat - self._lat) / steps
        delta_lng = (lng - self._lng) / steps

        for step in range(steps):
            log.info('Step {}'.format(step))
            self.jump_to(self._lat + delta_lat, self._lng + delta_lng)
            time.sleep(1)

    # Jump to position
    def jump_to(self, lat, lng, alt = 0):
        log.debug('Move to - Lat: %s Long: %s Alt: %s', lat, lng, alt)

        self._api.set_position(lat, lng, 0)

        self._lat = lat
        self._lng = lng
        # self._alt = alt

        self._lat_f2i = f2i(lat)
        self._lng_f2i = f2i(lng)
        # self._alt_f2i = f2i(alt)

    # Distance to an object
    def _dist_to_obj(self, obj):
        a = (self._lat, self._lng)
        b = (obj['lat'], obj['lng'])
        return great_circle(a, b).meters

    # Sort the items on map by distance
    def sort_map(self):
        for i in self.pokestop:
            i['dist'] = self._dist_to_obj(i)
        self.pokestop = sorted(self.pokestop , key=lambda k: k['dist'])
        for i in self.wild:
            i['dist'] = self._dist_to_obj(i)
        self.wild = sorted(self.pokestop , key=lambda k: k['dist'])

    # Scan the map around you
    def scan(self):

        self.pokestop = []
        self.wild = []

        timestamp = "\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000"
        cellid = self._get_cellid()
        self._api.get_map_objects(
            latitude=self._lat_f2i,
            longitude=self._lng_f2i,
            since_timestamp_ms=timestamp,
            cell_id=cellid)
        resp = self._api.call()
        for map_cell in resp['responses']['GET_MAP_OBJECTS']['map_cells']:
            if 'forts' in map_cell:
                for fort in map_cell['forts']:
                    if 'type' in fort and fort['type'] == 1:
                        pokestop = {}
                        pokestop['id'] = fort['id']
                        pokestop['lat'] = fort['latitude']
                        pokestop['lng'] = fort['longitude']
                        pokestop['enabled'] = fort['enabled']
                        self.pokestop.append(pokestop)
            if 'wild_pokemons' in map_cell:
                for wild_pokemon in map_cell['wild_pokemons']:
                    wild = {}
                    wild['encounter_id'] = wild_pokemon['encounter_id']
                    wild['spawnpoint_id'] = wild_pokemon['spawnpoint_id']
                    wild['encounter_id'] = wild_pokemon['encounter_id']
                    wild['lat'] = wild_pokemon['latitude']
                    wild['lng'] = wild_pokemon['longitude']
                    wild['spawnpoint_id'] = wild_pokemon['spawnpoint_id']
                    wild['last_modified_timestamp_ms'] = wild_pokemon['last_modified_timestamp_ms']
                    wild['time_till_hidden_ms'] = wild_pokemon['time_till_hidden_ms']
                    wild['expire_ms'] = time.time() * 1000 + wild_pokemon['time_till_hidden_ms']
                    self.wild.append(wild)

        log.debug('Response dictionary: \n\r{}'.format(json.dumps(resp, indent=2)))

    # Spin the pokestop
    def spin(self, pokestop):
        self._api.fort_search(fort_id=pokestop['id'],
            fort_latitude=pokestop['lat'],
            fort_longitude=pokestop['lng'],
            player_latitude=self._lat_f2i ,
            player_longitude=self._lng_f2i)

        resp = self._api.call()

        log.debug('Response dictionary: \n\r{}'.format(json.dumps(resp, indent=2)))

        if 'experience_awarded' in resp['responses']['FORT_SEARCH']:
            log.info('exp = {}'.format(resp['responses']['FORT_SEARCH']['experience_awarded']))
        else:
            log.info('no exp')

    # Login
    def login(self, auth_service, username, password):
        return self._api.login(auth_service, username, password)

    def get_player(self):
        self._api.get_player()
        resp = self._api.call()
        self.profile['username'] = resp['responses']['GET_PLAYER']['profile']['username']

        log.debug('Response dictionary: \n\r{}'.format(json.dumps(resp, indent=2)))

    def get_inventory(self):
        self._api.get_inventory()
        resp = self._api.call()

        # Save items in self.item
        for inventory_item in resp['responses']['GET_INVENTORY']['inventory_delta']['inventory_items']:

            try:
                if inventory_item['inventory_item_data']['item']['item'] == 1:
                    self.item['POKEBALL'] = inventory_item['inventory_item_data']['item']['count']
            except KeyError:
                pass

        log.debug('Response dictionary: \n\r{}'.format(json.dumps(resp, indent=2)))

    def _get_cellid(self):
        lat = self._lat
        long = self._lng
        origin = CellId.from_lat_lng(LatLng.from_degrees(lat, long)).parent(15)
        walk = [origin.id()]

        # 10 before and 10 after
        next = origin.next()
        prev = origin.prev()
        for i in range(10):
            walk.append(prev.id())
            walk.append(next.id())
            next = next.next()
            prev = prev.prev()
        return ''.join(map(self._encode, sorted(walk)))

    def _encode(self,cellid):
        output = []
        encoder._VarintEncoder()(output.append, cellid)
        return ''.join(output)

def main():
    pass

if __name__ == '__main__':
    main()
