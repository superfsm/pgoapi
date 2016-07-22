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
import pprint
import time
from collections import defaultdict

from pgoapi import PGoApi
from pgoapi.utilities import f2i, h2f

from Evolvable import evolvable
from pgoapi.protos.POGOProtos.Inventory_pb2 import ItemId
from pgoapi.protos.POGOProtos.Enums_pb2 import PokemonId
from pgoapi.protos.POGOProtos.Networking.Responses_pb2 import (FortSearchResponse, 
    EncounterResponse, CatchPokemonResponse, ReleasePokemonResponse, RecycleInventoryItemResponse)

from google.protobuf.internal import encoder
from geopy.geocoders import GoogleV3
from geopy.distance import great_circle
from s2sphere import CellId, LatLng

log = logging.getLogger(__name__)

POKEMON_ID_MAX = 151

class MyDict(dict):

    def __missing__(self, key):
        return MyDict({})

    def __getitem__(self, key):
        val = dict.__getitem__(self, key)
        if isinstance(val, dict) and not isinstance(val, MyDict):
            val = MyDict(val)
        return val

def chain_api(func):
    def wrapper(self, *args, **kwargs):
        func(self, *args, **kwargs)
        return self
    return wrapper

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
        self.item = defaultdict(int)
        self.pokemon = defaultdict(list)
        self.candy = defaultdict(int)
        self.egg = []

        self.pokestop = {}
        self.wild_pokemon = []

    def get_pokestop(self):
        return self.pokestop.values()

    def get_wild_pokemon(self):
        return self.wild_pokemon[:]

    def get_position(self):
        return (self._lat, self._lng)

    # Move to object
    @chain_api
    def move_to_obj(self, obj, speed = 20):
        self.move_to(obj['latitude'], obj['longitude'], speed = speed)

    # Move to position at speed(m)/s
    @chain_api
    def move_to(self, lat, lng, speed = 20):
        a = (self._lat, self._lng)
        b = (lat, lng)

        dist = great_circle(a, b).meters
        steps = int(dist / speed) + 1

        delta_lat = (lat - self._lat) / steps
        delta_lng = (lng - self._lng) / steps

        log.info('Moving ... %d steps' % steps)
        for step in range(steps):
            self.jump_to(self._lat + delta_lat, self._lng + delta_lng)
            time.sleep(1)

    # Jump to position
    @chain_api
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
        self.pokestop = sorted(self.pokestop, key=lambda k: k['dist'])
        for i in self.pokewild:
            i['dist'] = self._dist_to_obj(i)
        self.pokewild = sorted(self.pokewild, key=lambda k: k['dist'])

    # Send request and parse response
    def _call(self):

        # Call api
        resp = self._api.call()
        log.debug('Response dictionary: \n\r{}'.format(
            pprint.PrettyPrinter(indent=2, width=60).pformat(resp)))

        if not resp:
            return

        responses = MyDict(resp)['responses']

        # GET_MAP_OBJECTS
        for map_cell in responses['GET_MAP_OBJECTS']['map_cells']:
            map_cell = MyDict(map_cell)

            for fort in map_cell['forts']:
                fort = MyDict(fort)
                if fort['type'] == 1:
                    self.pokestop[fort['id']] = fort
                    # log.debug('POKESTOP = {}'.format(fort))

            for wild_pokemon in map_cell['wild_pokemons']:
                self.wild_pokemon.append(wild_pokemon)
                # log.debug('POKEMON = {}'.format(wild_pokemon))

        # FORT_SEARCH
        if responses['FORT_SEARCH']:
            experience_awarded = responses['FORT_SEARCH']['experience_awarded']
            result = responses['FORT_SEARCH']['result']
            if result:
                log.info('FORT_SEARCH {}, EXP = {}'.format(
                    FortSearchResponse.Result.Name(result),experience_awarded))
                if result == FortSearchResponse.Result.Value('INVENTORY_FULL'):
                    self.bulk_recycle_inventory_item()
            else:
                log.warning('FORT_SEARCH result = {}'.format(result))

        # GET_INVENTORY
        if responses['GET_INVENTORY']['success']:
            for inventory_item in responses['GET_INVENTORY']['inventory_delta']['inventory_items']:
                inventory_item = MyDict(inventory_item)

                if inventory_item['deleted_item_key']:
                    log.warning('*** captured deleted_item_key in inventory')
                    log.warning(inventory_item)

                #Item
                item_id = inventory_item['inventory_item_data']['item']['item_id']
                count = inventory_item['inventory_item_data']['item']['count']
                if item_id and count:
                    self.item[item_id] = count
                    # log.debug('ITEM {} = {}'.format(item, count))

                #Stats
                player_stats = inventory_item['inventory_item_data']['player_stats']
                self.profile.update(player_stats)
                # log.debug('PROFILE {}'.format(self.profile))

                #Pokemon
                pokemon = inventory_item['inventory_item_data']['pokemon_data']
                if pokemon['cp']:
                    self.pokemon[pokemon['pokemon_id']].append(pokemon)
                elif pokemon['is_egg'] is True:
                    self.egg.append(pokemon)

                for idx in range(POKEMON_ID_MAX):
                    self.pokemon[idx].sort(reverse=True, key=lambda p: p['cp'])

                #Candy
                pokemon_family = inventory_item['inventory_item_data']['pokemon_family']
                candy = pokemon_family['candy']
                family_id = pokemon_family['family_id']
                if candy and family_id:
                    self.candy[family_id] = candy

        # GET_PLAYER
        if responses['GET_PLAYER']['success'] == True:
            self.profile.update(responses['GET_PLAYER']['player_data'])

        # ENCOUNTER
        if 'ENCOUNTER' in responses:
            if responses['ENCOUNTER']['status']:
                log.info('ENCOUNTER = {}'.format(
                    EncounterResponse.Status.Name(responses['ENCOUNTER']['status'])))
            else:
                log.warning('ENCOUNTER = {}')

            if responses['ENCOUNTER']['status'] == 1:
                log.info('ENCOUNTER CP = {} PROB = {}'.format(
                    responses['ENCOUNTER']['wild_pokemon']['pokemon_data']['cp'],
                    responses['ENCOUNTER']['capture_probability']['capture_probability']))
                return True
            else:
                if responses['ENCOUNTER']['status'] == EncounterResponse.Status.Value('POKEMON_INVENTORY_FULL'):
                    self.bulk_release_pokemon()
                return False

        # CATCH_POKEMON
        if 'CATCH_POKEMON' in responses:

            status = responses['CATCH_POKEMON']['status']

            if status:
                log.info('CATCH_POKEMON = {}'.format(CatchPokemonResponse.CatchStatus.Name(status)))
            else:
                log.warning('CATCH_POKEMON = {}')


            if status == 1:
                log.info('CATCH_POKEMON EXP = {}'.format(
                    sum(responses['CATCH_POKEMON']['capture_award']['xp'])))
            return status

        # RELEASE_POKEMON
        if 'RELEASE_POKEMON' in responses:
            candy_awarded = responses['RELEASE_POKEMON']['candy_awarded']
            result = responses['RELEASE_POKEMON']['result']
            if result:
                log.info('RELEASE_POKEMON = {}, +{}'.format(
                    ReleasePokemonResponse.Result.Name(result), candy_awarded))
            else:
                log.warning('RELEASE_POKEMON = {}')

        # RECYCLE_INVENTORY_ITEM
        if 'RECYCLE_INVENTORY_ITEM' in responses:
            new_count = responses['RECYCLE_INVENTORY_ITEM']['new_count']
            result = responses['RECYCLE_INVENTORY_ITEM']['result']
            if result:
                log.info('RECYCLE_INVENTORY_ITEM = {}, {} left'.format(
                    RecycleInventoryItemResponse.Result.Name(result), new_count))
            else:
                log.warning('RECYCLE_INVENTORY_ITEM = {}')

    @chain_api
    def bulk_recycle_inventory_item(self):
        for item_id, count in self.item.iteritems():

            if item_id == ItemId.Value('ITEM_POTION'):
                self.recycle_inventory_item(item_id, count)
            if item_id == ItemId.Value('ITEM_SUPER_POTION') and count > 50:
                self.recycle_inventory_item(item_id, count-50)
            if item_id == ItemId.Value('ITEM_REVIVE') and count > 50:
                self.recycle_inventory_item(item_id, count-50)
            if item_id == ItemId.Value('ITEM_RAZZ_BERRY') and count > 50:
                self.recycle_inventory_item(item_id, count - 50)
            if item_id == ItemId.Value('ITEM_POKE_BALL') and count > 50:
                self.recycle_inventory_item(item_id, count-50)

        self.summary()

    @chain_api
    def recycle_inventory_item(self, item_id, count):
        self._api.recycle_inventory_item(item_id=item_id,count=count)
        self._call()

        self.summary()

    @chain_api
    def bulk_release_pokemon(self):
        for idx in range(POKEMON_ID_MAX):
            if idx not in evolvable and len(self.pokemon[idx]) >= 2:
                for pokemon in self.pokemon[idx][1:]:
                    if pokemon['cp'] < 1000:
                        log.info('RELEASING #%3d CP=%d' % (idx,pokemon['cp']))
                        self.release_pokemon(pokemon['id'])

            if idx in evolvable and len(self.pokemon[idx]) > 2:
                for pokemon in self.pokemon[idx][2:]:
                    if pokemon['cp'] < 450:
                        log.info('RELEASING #%3d CP=%d' % (idx,pokemon['cp']))
                        self.release_pokemon(pokemon['id'])

    @chain_api
    def release_pokemon(self, pokemon_id):
        self._api.release_pokemon(pokemon_id=pokemon_id)
        self._call()

    @chain_api
    def status(self):
        cnt_item = 0
        for v in self.item.values():
            cnt_item += v

        cnt_pokemon = 0
        for idx in range(POKEMON_ID_MAX):
            cnt_pokemon += len(self.pokemon[idx])

        exp = self.profile['experience'] - self.profile['prev_level_xp']
        exp_total = self.profile['next_level_xp'] - self.profile['prev_level_xp']

        print '==ITEM %d/%d, POKEMON %d/%d, [Lv %d, %d/%d, (%d%%)]==' % (
            cnt_item, self.profile['max_item_storage'],
            cnt_pokemon, self.profile['max_pokemon_storage'],
            self.profile['level'],exp,exp_total,float(exp)/exp_total*100)




    @chain_api
    def summary(self):

        print 'POSITION (lat,lng) = {},{}'.format(self._lat, self._lng)
        print 'POKESTOP # =', len(self.pokestop)
        print 'WILD POKEMON # =', len(self.wild_pokemon)

        cnt_pokemon = 0
        for idx in range(POKEMON_ID_MAX):
            if idx not in evolvable:
                star = 'x'
            else:
                star = ''
            print '%03d (%15s)[%1s]: %3d =' % (
                idx, PokemonId.Name(idx), star, self.candy[idx]), [p['cp'] for p in self.pokemon[idx]]
            cnt_pokemon += len(self.pokemon[idx])
        print 'POKEMON # =\n   ', cnt_pokemon

        cnt_item = 0
        for k,v in self.item.iteritems():
            print "*%3d (%s) = %d" % (k, ItemId.Name(k), v)
            cnt_item += v
        print "ITEM # =\n   ", cnt_item

        print 'PROFILE ='
        pprint.pprint(self.profile, indent=4)

        print 'EGG # =\n   ', len(self.egg)

        exp = self.profile['experience'] - self.profile['prev_level_xp']
        exp_total = self.profile['next_level_xp'] - self.profile['prev_level_xp']
        print '====Lv %d, %d/%d (%.2f%%)' % (
            self.profile['level'], exp, exp_total, float(exp)/exp_total*100)

    # Scan the map around you
    @chain_api
    def scan(self):

        self.wild_pokemon = []

        cell_ids = self._get_cell_ids()
        timestamps = [0, ] * len(cell_ids)
        self._get_inventory()
        self._get_player()
        self._api.get_map_objects(
            latitude=self._lat_f2i,
            longitude=self._lng_f2i,
            since_timestamp_ms=timestamps,
            cell_id=cell_ids)
        self._call()

    @chain_api
    def catch_pokemon(self, pokemon):

        self._encounter(pokemon)
        ret = self._call()
        if ret:

            if self.item[ItemId.Value('ITEM_POKE_BALL')] > 0:
                pokeball = ItemId.Value('ITEM_POKE_BALL')
            elif self.item[ItemId.Value('ITEM_GREAT_BALL')] > 0:
                pokeball = ItemId.Value('ITEM_GREAT_BALL')
            else:
                log.warning('CATCH_POKEMON no balls!')
                return

            self._catch_pokemon(pokeball, pokemon)
            ret = self._call()
            while ret == 2 or ret == 4:
                self._catch_pokemon(pokeball, pokemon)
                ret = self._call()

    def _encounter(self, pokemon):
        self._api.encounter(
            encounter_id=pokemon['encounter_id'],
            spawnpoint_id=pokemon['spawnpoint_id'],
            player_latitude=self._lat_f2i,
            player_longitude=self._lng_f2i)

    def _catch_pokemon(self, pokeball, pokemon):
        self._api.catch_pokemon(
                encounter_id=pokemon['encounter_id'],
                pokeball=pokeball,
                normalized_reticle_size=1.950,
                spawn_point_guid=pokemon['spawnpoint_id'],
                hit_pokemon=1,
                spin_modifier=1,
                normalized_hit_position=1)

    # Spin the pokestop
    @chain_api
    def fort_search(self, pokestop):
        self._api.fort_search(
            fort_id=pokestop['id'],
            fort_latitude=pokestop['latitude'],
            fort_longitude=pokestop['longitude'],
            player_latitude=self._lat_f2i,
            player_longitude=self._lng_f2i)
        self._call()

    # Login
    def login(self, auth_service, username, password):
        return self._api.login(auth_service, username, password)

    def test(self):
        self._api.get_player()
        self._api.get_inventory()
        resp = self._api._call()
        log.info('Response dictionary: \n\r{}'.format(json.dumps(resp, indent=2)))


    def _get_player(self):
        self._api.get_player()


    def _get_inventory(self):
        self.egg = []
        self.pokemon = defaultdict(list)
        self.candy = defaultdict(int)
        self.item = defaultdict(int)
        self._api.get_inventory()

    def _get_cell_ids(self, radius=10):
        lat = self._lat
        long = self._lng
        origin = CellId.from_lat_lng(LatLng.from_degrees(lat, long)).parent(15)
        walk = [origin.id()]
        right = origin.next()
        left = origin.prev()

        # Search around provided radius
        for i in range(radius):
            walk.append(right.id())
            walk.append(left.id())
            right = right.next()
            left = left.prev()

        # Return everything
        return sorted(walk)

    def _encode(self,cellid):
        output = []
        encoder._VarintEncoder()(output.append, cellid)
        return ''.join(output)

def main():
    pass

if __name__ == '__main__':
    main()
