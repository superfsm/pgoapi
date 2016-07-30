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


import json
import logging
import pprint
import time
import csv
import numpy as np
from collections import defaultdict

from pgoapi import PGoApi

from pgoapi.protos.POGOProtos.Inventory_pb2 import ItemId
from pgoapi.protos.POGOProtos.Enums_pb2 import PokemonId
from pgoapi.protos.POGOProtos.Networking.Responses_pb2 import (
    FortSearchResponse, EncounterResponse, CatchPokemonResponse, ReleasePokemonResponse,
    RecycleInventoryItemResponse, UseItemEggIncubatorResponse, EvolvePokemonResponse,
    NicknamePokemonResponse, DiskEncounterResponse, UseItemXpBoostResponse)

from google.protobuf.internal import encoder
from geopy.distance import great_circle
from s2sphere import CellId, LatLng

log = logging.getLogger(__name__)

POKEMON_ID_MAX = 151
CHEAP_LIST = []
WORTH_LIST = []
CHEAP_LIST = [
    PokemonId.Value('CATERPIE'),
    PokemonId.Value('WEEDLE'),
    PokemonId.Value('PIDGEY')]

# CHEAP_LIST += [
#     PokemonId.Value('SANDSHREW'),
#     PokemonId.Value('EKANS'),
#     PokemonId.Value('DIGLETT'),
#     PokemonId.Value('MEOWTH'),
#     PokemonId.Value('MANKEY'),
#     PokemonId.Value('KRABBY'),
#     PokemonId.Value('HORSEA')]

# WORTH_LIST = [
#     PokemonId.Value('EEVEE'),
#     PokemonId.Value('SNORLAX'),
#     PokemonId.Value('DRATINI'),
#     PokemonId.Value('BULBASAUR'),
#     PokemonId.Value('CHARMANDER'),
#     PokemonId.Value('GROWLITHE'),
#     PokemonId.Value('ABRA'),
#     PokemonId.Value('GRIMER'),
#     PokemonId.Value('MAGIKARP'),
#     PokemonId.Value('LAPRAS'),
#     PokemonId.Value('PIDGEY'),
#     PokemonId.Value('PIKACHU'),
#     PokemonId.Value('JIGGLYPUFF'),
#     PokemonId.Value('POLIWAG'),
#     PokemonId.Value('GROWLITHE'),
#     PokemonId.Value('GEODUDE'),
#     PokemonId.Value('GASTLY'),
#     PokemonId.Value('EXEGGCUTE'),
#     PokemonId.Value('MEWTWO'),
#     PokemonId.Value('MEW'),
#     PokemonId.Value('MOLTRES'),
#     PokemonId.Value('ZAPDOS'),
#     PokemonId.Value('ARTICUNO'),
#     PokemonId.Value('MEWTWO')]


class MyDict(dict):

    def __missing__(self, key):
        return MyDict({})

    def __getitem__(self, key):
        val = dict.__getitem__(self, key)
        if isinstance(val, dict) and not isinstance(val, MyDict):
            val = MyDict(val)
        return val

with open('data/GAME_MASTER_POKEMON.tsv') as tsv:
    lines = [line for line in csv.reader(tsv, delimiter="\t")]
    POKEDEX = {}
    for idx in range(1, POKEMON_ID_MAX + 1):
        POKE = {}
        for idx_k in range(len(lines[0])):
            try:
                POKE[lines[0][idx_k]] = int(lines[idx][idx_k])
            except ValueError:
                POKE[lines[0][idx_k]] = lines[idx][idx_k]

        POKEDEX[idx] = POKE
        POKEDEX[POKE['Identifier']] = POKE
    POKEDEX[133]['EvolvesTo'] = 'Vaporeon'

    # Family id
    for pokemon_id in range(1, POKEMON_ID_MAX + 1):
        family_id = pokemon_id
        while POKEDEX[family_id]['EvolvesFrom']:
            family_id = POKEDEX[POKEDEX[family_id]['EvolvesFrom']]['PkMn']
        POKEDEX[pokemon_id]['family_id'] = family_id

with open('data/level-to-cpm.json') as f:
    tmp = json.load(f)
    LEVEL_TO_CPM = {}
    for lv, cpm in tmp.iteritems():
        LEVEL_TO_CPM[float(lv)] = cpm
    del tmp

with open('data/level-to-dust.json') as f:
    tmp = json.load(f)
    LEVEL_TO_DUST = {}
    for lv, dust in tmp.iteritems():
        LEVEL_TO_DUST[float(lv)] = dust
    del tmp


def chain_api(func):
    def wrapper(self, *args, **kwargs):
        func(self, *args, **kwargs)
        return self
    return wrapper


def timing(func):
    def wrapper(self, *args, **kwargs):
        start_time = int(round(time.time() * 1000))
        func(self, *args, **kwargs)
        end_time = int(round(time.time() * 1000))
        print("###", end_time - start_time, 'ms')
        return self
    return wrapper


class Client:

    def __init__(self):
        self._api = PGoApi()
        self._req = self._api.create_request()

        self._lat = 0
        self._lng = 0
        self._alt = 0
        self.step = 0

        self.profile = {}
        self.profile['level'] = 1
        self.profile['cnt_pokemon'] = 0
        self.profile['cnt_item'] = 0

        self.incubator = {}
        self.item = defaultdict(int)
        self.family = defaultdict(list)
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

    @chain_api
    def move_to_pokestop_catch(self, pokestop, speed=40):
        a = (self._lat, self._lng)
        b = (pokestop['latitude'], pokestop['longitude'])

        dist = great_circle(a, b).meters
        steps = int(dist / speed)

        if steps != 0:
            delta_lat = (pokestop['latitude'] - self._lat) / steps
            delta_lng = (pokestop['longitude'] - self._lng) / steps

            log.info('Moving ... %d steps' % steps)
            for _ in range(steps):
                self.step += 1
                if self.step % 2 == 0:
                    self.scan()
                    for wild_pokemon in self.wild_pokemon:
                        self.catch_pokemon(wild_pokemon)
                self.jump_to(self._lat + delta_lat, self._lng + delta_lng)
                log.info('-')
                time.sleep(1)
        self.fort_search(pokestop)

    # Spin the pokestop
    @chain_api
    def fort_search(self, pokestop):
        if 'lure_info' in self.pokestop[pokestop['id']]:
            self.disk_catch_pokemon(self.pokestop[pokestop['id']]['lure_info'])
        self._req.fort_search(
            fort_id=pokestop['id'],
            fort_latitude=pokestop['latitude'],
            fort_longitude=pokestop['longitude'],
            player_latitude=self._lat,
            player_longitude=self._lng)
        self._call()

    # Move to object
    @chain_api
    def move_to_obj(self, obj, speed=20):
        self.move_to(obj['latitude'], obj['longitude'], speed=speed)

    # Move to position at speed(m)/s
    @chain_api
    def move_to(self, lat, lng, speed=20):
        a = (self._lat, self._lng)
        b = (lat, lng)

        dist = great_circle(a, b).meters
        steps = int(dist / speed) + 1

        delta_lat = (lat - self._lat) / steps
        delta_lng = (lng - self._lng) / steps

        log.info('Moving ... %d steps' % steps)
        prev_time = time.time()
        for step in range(steps):
            self.jump_to(self._lat + delta_lat, self._lng + delta_lng)
            time.sleep(1)
            if time.time() - prev_time > 30:
                self.scan()
                prev_time = time.time()

    # Jump to position
    @chain_api
    def jump_to(self, lat, lng, alt=0):
        log.debug('Move to - Lat: %s Long: %s Alt: %s', lat, lng, alt)

        self._api.set_position(lat, lng, alt)
        self._req.set_position(lat, lng, alt)

        self._lat = lat
        self._lng = lng
        # self._alt = alt

    # Distance to an object
    def _dist_to_obj(self, obj):
        a = (self._lat, self._lng)
        b = (obj['lat'], obj['lng'])
        return great_circle(a, b).meters

    # Send request and parse response
    def _call(self):

        time.sleep(0.34)
        # Call api
        resp = self._req.call()
        self._req = self._api.create_request()
        self._req.set_position(self._lat, self._lng, self._alt)
        log.debug('Response dictionary: \n\r{}'.format(
            pprint.PrettyPrinter(indent=2, width=3).pformat(resp)))

        if not resp:
            return

        responses = MyDict(resp)['responses']

        # GET_PLAYER
        if responses['GET_PLAYER']['success'] is True:
            self.profile.update(responses['GET_PLAYER']['player_data'])

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

        # FORT_SEARCH
        if responses['FORT_SEARCH']:
            experience_awarded = responses['FORT_SEARCH']['experience_awarded']
            result = responses['FORT_SEARCH']['result']
            if result:
                log.info('FORT_SEARCH {}, EXP = {}'.format(
                    FortSearchResponse.Result.Name(result), experience_awarded))
                if result == FortSearchResponse.Result.Value('INVENTORY_FULL'):
                    self.summary()
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

                # Item
                item_id = inventory_item['inventory_item_data']['item']['item_id']
                count = inventory_item['inventory_item_data']['item']['count']
                if item_id and count:
                    self.item[item_id] = count
                    self.profile['cnt_item'] += count
                    # log.debug('ITEM {} = {}'.format(item, count))

                # Stats
                player_stats = inventory_item['inventory_item_data']['player_stats']
                self.profile.update(player_stats)
                # log.debug('PROFILE {}'.format(self.profile))

                # Pokemon
                pokemon = inventory_item['inventory_item_data']['pokemon_data']
                if pokemon['cp']:
                    self._calc_attr(pokemon)
                    self.family[POKEDEX[pokemon['pokemon_id']]['family_id']].append(pokemon)
                    self.profile['cnt_pokemon'] += 1

                elif pokemon['is_egg'] is True:
                    self.egg.append(pokemon)

                # Candy
                candy = inventory_item['inventory_item_data']['candy']
                family_id = candy['family_id']
                count = candy['candy']
                if count and family_id:
                    self.candy[family_id] = count

                # Incubators
                egg_incubators = inventory_item['inventory_item_data']['egg_incubators']['egg_incubator']
                if egg_incubators:
                    for egg_incubator in egg_incubators:
                        self.incubator[egg_incubator['id']] = egg_incubator

            # Sort egg by km
            self.egg.sort(reverse=True, key=lambda e: e['egg_km_walked_target'])

        # DISK_ENCOUNTER
        if 'DISK_ENCOUNTER' in responses:
            if responses['DISK_ENCOUNTER']['result']:
                log.info('DISK_ENCOUNTER = {}'.format(
                    DiskEncounterResponse.Result.Name(responses['DISK_ENCOUNTER']['result'])))
            else:
                log.warning('DISK_ENCOUNTER = {}')

            if responses['DISK_ENCOUNTER']['result'] == 1:
                pokemon = responses['DISK_ENCOUNTER']['pokemon_data']
                self._calc_attr(pokemon)
                log.info('DISK_ENCOUNTER = "{}", PROB = {}'.format(
                    PokemonId.Name(pokemon['pokemon_id']), responses['DISK_ENCOUNTER']['capture_probability']['capture_probability']))
                # Bool, CP, ID
                return (
                    True,
                    pokemon['max_cp'],
                    pokemon['family_id'])
            else:
                if responses['DISK_ENCOUNTER']['result'] == DiskEncounterResponse.Result.Value('POKEMON_INVENTORY_FULL'):
                    self.bulk_release_pokemon()
                return (False, )

        # ENCOUNTER
        if 'ENCOUNTER' in responses:
            if responses['ENCOUNTER']['status']:
                log.info('ENCOUNTER = {}'.format(
                    EncounterResponse.Status.Name(responses['ENCOUNTER']['status'])))
            else:
                log.warning('ENCOUNTER = {}')

            if responses['ENCOUNTER']['status'] == 1:
                pokemon = responses['ENCOUNTER']['wild_pokemon']['pokemon_data']
                self._calc_attr(pokemon)
                log.info('ENCOUNTER = "{}", PROB = {}'.format(
                    PokemonId.Name(pokemon['pokemon_id']), responses['ENCOUNTER']['capture_probability']['capture_probability']))
                # Bool, CP, ID
                return (
                    True,
                    pokemon['max_cp'],
                    pokemon['family_id'])
            else:
                if responses['ENCOUNTER']['status'] == EncounterResponse.Status.Value('POKEMON_INVENTORY_FULL'):
                    self.bulk_release_pokemon()
                return (False, )

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

        # EVOLVE_POKEMON
        if 'EVOLVE_POKEMON' in responses:
            result = responses['EVOLVE_POKEMON']['result']
            if result:
                log.info('EVOLVE_POKEMON = {}, exp = {}, +{} Candy'.format(
                    EvolvePokemonResponse.Result.Name(result),
                    responses['EVOLVE_POKEMON']['experience_awarded'],
                    responses['EVOLVE_POKEMON']['candy_awarded']))
            else:
                log.warning('RELEASE_POKEMON = {}')

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

        # USE_ITEM_CAPTURE
        if 'USE_ITEM_CAPTURE' in responses:
            log.info('USE_ITEM_CAPTURE success = {}'.format(responses['USE_ITEM_CAPTURE']['success']))

            if responses['USE_ITEM_CAPTURE']['success'] is True:
                return True
            else:
                return False

        # USE_ITEM_EGG_INCUBATOR
        if 'USE_ITEM_EGG_INCUBATOR' in responses:
            log.info('USE_ITEM_EGG_INCUBATOR result = {}'.format(
                UseItemEggIncubatorResponse.Result.Name(responses['USE_ITEM_EGG_INCUBATOR']['result'])))

        # NICKNAME_POKEMON
        if 'NICKNAME_POKEMON' in responses:
            log.info('NICKNAME_POKEMON result = {}'.format(
                NicknamePokemonResponse.Result.Name(responses['NICKNAME_POKEMON']['result'])))

        # USE_ITEM_XP_BOOST
        if 'USE_ITEM_XP_BOOST' in responses:
            log.info('USE_ITEM_XP_BOOST result = {}'.format(
                UseItemXpBoostResponse.Result.Name(responses['USE_ITEM_XP_BOOST']['result'])))

    @chain_api
    def bulk_recycle_inventory_item(self):

        max_item_storage = self.profile['max_item_storage'] - 50

        BALL_MAX = max_item_storage / 3
        POTION_MAX = max_item_storage / 3
        REVIVE_MAX = max_item_storage / 3 / 2
        BERRY_MAX = max_item_storage / 3 / 2

        cnt_poke_ball = self.item[ItemId.Value('ITEM_POKE_BALL')]
        cnt_great_ball = self.item[ItemId.Value('ITEM_GREAT_BALL')]
        cnt_ultra_ball = self.item[ItemId.Value('ITEM_ULTRA_BALL')]
        cnt_master_ball = self.item[ItemId.Value('ITEM_MASTER_BALL')]

        cnt_potion = self.item[ItemId.Value('ITEM_POTION')]
        cnt_super_potion = self.item[ItemId.Value('ITEM_SUPER_POTION')]
        cnt_hyper_potion = self.item[ItemId.Value('ITEM_HYPER_POTION')]
        cnt_max_potion = self.item[ItemId.Value('ITEM_MAX_POTION')]

        cnt_revive = self.item[ItemId.Value('ITEM_REVIVE')]
        cnt_max_revive = self.item[ItemId.Value('ITEM_MAX_REVIVE')]
        cnt_berry = self.item[ItemId.Value('ITEM_RAZZ_BERRY')]

        if cnt_max_potion > POTION_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_MAX_POTION'), cnt_max_potion - POTION_MAX)
            self._recycle_inventory_item(ItemId.Value('ITEM_HYPER_POTION'), cnt_hyper_potion)
            self._recycle_inventory_item(ItemId.Value('ITEM_SUPER_POTION'), cnt_super_potion)
            self._recycle_inventory_item(ItemId.Value('ITEM_POTION'), cnt_potion)
        elif cnt_max_potion + cnt_hyper_potion > POTION_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_HYPER_POTION'), cnt_max_potion + cnt_hyper_potion - POTION_MAX)
            self._recycle_inventory_item(ItemId.Value('ITEM_SUPER_POTION'), cnt_super_potion)
            self._recycle_inventory_item(ItemId.Value('ITEM_POTION'), cnt_potion)
        elif cnt_max_potion + cnt_hyper_potion + cnt_super_potion > POTION_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_SUPER_POTION'), cnt_max_potion + cnt_hyper_potion + cnt_super_potion - POTION_MAX)
            self._recycle_inventory_item(ItemId.Value('ITEM_POTION'), cnt_potion)
        elif cnt_max_potion + cnt_hyper_potion + cnt_super_potion + cnt_potion > POTION_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_POTION'), cnt_max_potion + cnt_hyper_potion + cnt_super_potion + cnt_potion - POTION_MAX)

        if cnt_master_ball > BALL_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_MASTER_BALL'), cnt_master_ball - BALL_MAX)
            self._recycle_inventory_item(ItemId.Value('ITEM_ULTRA_BALL'), cnt_ultra_ball)
            self._recycle_inventory_item(ItemId.Value('ITEM_GREAT_BALL'), cnt_great_ball)
            self._recycle_inventory_item(ItemId.Value('ITEM_POKE_BALL'), cnt_poke_ball)
        elif cnt_master_ball + cnt_ultra_ball > BALL_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_ULTRA_BALL'), cnt_master_ball + cnt_ultra_ball - BALL_MAX)
            self._recycle_inventory_item(ItemId.Value('ITEM_GREAT_BALL'), cnt_great_ball)
            self._recycle_inventory_item(ItemId.Value('ITEM_POKE_BALL'), cnt_poke_ball)
        elif cnt_master_ball + cnt_ultra_ball + cnt_great_ball > BALL_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_GREAT_BALL'), cnt_master_ball + cnt_ultra_ball + cnt_great_ball - BALL_MAX)
            self._recycle_inventory_item(ItemId.Value('ITEM_POKE_BALL'), cnt_poke_ball)
        elif cnt_master_ball + cnt_ultra_ball + cnt_great_ball + cnt_poke_ball > BALL_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_POKE_BALL'), cnt_master_ball + cnt_ultra_ball + cnt_great_ball + cnt_poke_ball - BALL_MAX)

        if cnt_max_revive > REVIVE_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_MAX_REVIVE'), cnt_max_revive - REVIVE_MAX)
            self._recycle_inventory_item(ItemId.Value('ITEM_REVIVE'), cnt_revive)
        elif cnt_max_revive + cnt_revive > REVIVE_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_REVIVE'), cnt_max_revive + cnt_revive - REVIVE_MAX)
        if cnt_berry > BERRY_MAX:
            self._recycle_inventory_item(ItemId.Value('ITEM_RAZZ_BERRY'), cnt_berry - BERRY_MAX)
        self._call()

    @chain_api
    def _recycle_inventory_item(self, item_id, count):
        log.info('RECYCLE_INVENTORY_ITEM {} = {}'.format(ItemId.Name(item_id), count))
        self._req.recycle_inventory_item(item_id=item_id, count=count)

    def _calc_attr(self, pokemon):
        pokemon_id = pokemon['pokemon_id']
        pokemon['family_id'] = POKEDEX[pokemon_id]['family_id']

        while POKEDEX[pokemon_id]['EvolvesTo']:
            pokemon_id = POKEDEX[POKEDEX[pokemon_id]['EvolvesTo']]['PkMn']

        # Stats
        _ba = POKEDEX[pokemon_id]['BaseAttack']
        _bd = POKEDEX[pokemon_id]['BaseDefense']
        _bs = POKEDEX[pokemon_id]['BaseStamina']
        _ia = _id = _is = 0
        if pokemon['individual_attack']:
            _ia = pokemon['individual_attack']
        if pokemon['individual_defense']:
            _id = pokemon['individual_defense']
        if pokemon['individual_stamina']:
            _is = pokemon['individual_stamina']
        # pokemon['piv'] = (_ia + _id + _is) / 45.0
        max_cp = (_ba+_ia) * ((_bd+_id)**0.5) * ((_bs+_is)**0.5) * (LEVEL_TO_CPM[40]**2) / 10
        pokemon['max_cp'] = max_cp

    def _calc_attr_detail(self, pokemon):
        pokemon_id = pokemon['pokemon_id']

        dust_needed = 0
        candy_needed = 0

        while POKEDEX[pokemon_id]['EvolvesTo']:
            candy_needed += POKEDEX[pokemon_id]['CandyToEvolve']
            pokemon_id = POKEDEX[POKEDEX[pokemon_id]['EvolvesTo']]['PkMn']
        pokemon['candy_needed_evolve'] = candy_needed

        # Level
        cpm = pokemon['cp_multiplier']
        if pokemon['additional_cp_multiplier']:
            cpm += pokemon['additional_cp_multiplier']

        pokemon['level'] = 0
        for lv, value in LEVEL_TO_CPM.iteritems():
            if abs(value - cpm) < 0.0001:
                pokemon['level'] = lv
                break

        # Stats
        _ba = POKEDEX[pokemon_id]['BaseAttack']
        _bd = POKEDEX[pokemon_id]['BaseDefense']
        _bs = POKEDEX[pokemon_id]['BaseStamina']
        _ia = _id = _is = 0
        if pokemon['individual_attack']:
            _ia = pokemon['individual_attack']
        if pokemon['individual_defense']:
            _id = pokemon['individual_defense']
        if pokemon['individual_stamina']:
            _is = pokemon['individual_stamina']
        # pokemon['piv'] = (_ia + _id + _is) / 45.0

        # Dust/Candy
        ceiling_level = self.profile['level'] + 1.5
        if ceiling_level > 40:
            ceiling_level = 40
        for lv in np.arange(pokemon['level'], ceiling_level, 0.5):
            dust_needed += LEVEL_TO_DUST[lv][0]
            candy_needed += LEVEL_TO_DUST[lv][1]
        pokemon['dust_needed_curr'] = dust_needed
        pokemon['candy_needed_curr'] = candy_needed

        for lv in np.arange(ceiling_level, 40, 0.5):
            dust_needed += LEVEL_TO_DUST[lv][0]
            candy_needed += LEVEL_TO_DUST[lv][1]

        pokemon['dust_needed_max'] = dust_needed
        pokemon['candy_needed_max'] = candy_needed

        # CP
        evolve_cp  = (_ba+_ia) * ((_bd+_id)**0.5) * ((_bs+_is)**0.5) * (LEVEL_TO_CPM[pokemon['level']]**2) / 10
        evolve_up_cp  = (_ba+_ia) * ((_bd+_id)**0.5) * ((_bs+_is)**0.5) * (LEVEL_TO_CPM[ceiling_level]**2) / 10
        perfect_cp = (_ba+ 15) * ((_bd+ 15)**0.5) * ((_bs+ 15)**0.5) * (LEVEL_TO_CPM[40]**2) / 10
        worst_cp   = _ba * (_bd**0.5) * (_bs**0.5) * (LEVEL_TO_CPM[40]**2) / 10

        pokemon['evolve_cp'] = evolve_cp
        pokemon['evolve_up_cp'] = evolve_up_cp
        pokemon['perfect_cp'] = perfect_cp
        pokemon['pcp'] = (pokemon['max_cp'] - worst_cp) / (perfect_cp - worst_cp)

        return pokemon

    @chain_api
    def bulk_release_pokemon(self):

        self.summary_pokemon()

        removed = 0
        for family_id in range(1, POKEMON_ID_MAX + 1):
            for pokemon in self.family[family_id]:
                if not (pokemon['isKeepMax'] or pokemon['isKeepEvo'] or pokemon['isKeepCp']):
                    print pokemon['pokemon_id'], 'RELEASE_POKEMON max_cp =', pokemon['max_cp']
                    removed += 1
                    self._release_pokemon(pokemon['id'])
                    self._call()

    @chain_api
    def _release_pokemon(self, pokemon_id):
        self._req.release_pokemon(pokemon_id=pokemon_id)

    @chain_api
    def bulk_evolve_pokemon(self, dry=True):

        self.summary_pokemon(block_on_full=False)

        cnt = 0

        for family_id in range(1, POKEMON_ID_MAX + 1):
            if len(self.family[family_id]) == 0:
                continue

            for pokemon in self.family[family_id]:
                if pokemon['isKeepEvo']:
                    self.evolve_pokemon(pokemon, dry=dry)
                    cnt += 1

        log.info('TOTAL EVOLVED = {}'.format(cnt))
        log.info('TOTAL EXP = {} ({})'.format(cnt * 500, cnt * 1000))

    @chain_api
    def manual_evolve_pokemon(self, pokemon_id, dry=True):
        log.info('MANUAL EVOLVE_POKEMON "%d"' % (pokemon_id))
        if not dry:
            self._req.evolve_pokemon(pokemon_id=pokemon_id)
            self._call()

    @chain_api
    def evolve_pokemon(self, pokemon, dry=True):
        log.info('EVOLVE_POKEMON "%s" %d -> %d' % (PokemonId.Name(pokemon['pokemon_id']), pokemon['cp'], pokemon['evolve_cp']))
        if pokemon['pokemon_id'] == PokemonId.Value('EEVEE'):
            if ('nickname' not in pokemon) or (pokemon['nickname'] != 'Rainer'):
                self.nickname_pokemon(pokemon['id'], 'Rainer')
        if not dry:
            self._req.evolve_pokemon(pokemon_id=pokemon['id'])
            self._call()

    @chain_api
    def use_item_xp_boost(self):
        log.info('USE_ITEM_XP_BOOST')
        self._req.use_item_xp_boost(item_id=ItemId.Value('ITEM_LUCKY_EGG'))
        self._call()

    @chain_api
    def nickname_pokemon(self, pokemon_id, nickname):
        log.info('RENAME to ' + nickname)
        self._req.nickname_pokemon(pokemon_id=pokemon_id, nickname=nickname)
        self._call()

    @chain_api
    def status(self):

        exp = self.profile['experience'] - self.profile['prev_level_xp']
        exp_total = self.profile['next_level_xp'] - self.profile['prev_level_xp']

        print '[Lv %d, %d/%d, (%.2f%%)]\nWALK = %.3f ITEM = %d/%d, POKEMON = %d/%d' % (
            self.profile['level'], exp, exp_total, float(exp) / exp_total * 100,
            self.profile['km_walked'], self.profile['cnt_item'], self.profile['max_item_storage'],
            self.profile['cnt_pokemon'], self.profile['max_pokemon_storage'])

    @chain_api
    def summary_pokemon(self, block_on_full=True):

        KEEP_CP = 1700

        title =  ' ID      NAME         (CAND)| LEVEL  CURR -> [CND] +EVO -> [DUST, CANDY]  +UP-> [DUST, CANDY]  MAX / THEORY    %   | ATK DEF STA EVO/CP/MAX ID'
        line  =  '----------------------------------------------------------------------------------------------------------------------------------------'

        for family_id in range(1, POKEMON_ID_MAX + 1):
            for pokemon in self.family[family_id]:
                self._calc_attr_detail(pokemon)

        release_cnt = 0
        keep_cnt = 0
        evo_cnt = 0
        total_cnt = 0

        for family_id in range(1, POKEMON_ID_MAX + 1):
            if len(self.family[family_id]) == 0:
                continue

            candy_left = self.candy[family_id]

            # Keep Max CP
            pokemon = max(self.family[family_id], key=lambda p: p['max_cp'])
            pokemon['isKeepMax'] = True
            _id = pokemon['id']
            candy_left -= pokemon['candy_needed_max']

            # Keep Max CP (Lv >= 25)
            high_level = filter(lambda p: p['level'] >= 25, self.family[family_id])
            if len(high_level) > 0:
                pokemon = max(high_level, key=lambda p: p['max_cp'])
                pokemon['isKeepMax'] = True
                if _id != pokemon['id']:
                    candy_left -= pokemon['candy_needed_max']

            self.family[family_id].sort(reverse=True, key=lambda p: p['evolve_cp'])
            for pokemon in self.family[family_id]:
                total_cnt += 1

                # Keep High CP
                if pokemon['cp'] > KEEP_CP:
                    pokemon['isKeepCp'] = True

                if family_id in CHEAP_LIST: # CHECP
                    if pokemon['pokemon_id'] == family_id:  # Evolve Base Form Only
                        if candy_left > POKEDEX[family_id]['CandyToEvolve']:
                            pokemon['isKeepEvo'] = True
                            candy_left -= POKEDEX[family_id]['CandyToEvolve']
                else:
                    if pokemon['candy_needed_evolve'] > 0:  # Evolve
                        if candy_left > pokemon['candy_needed_evolve']:
                            pokemon['isKeepEvo'] = True
                            candy_left -= pokemon['candy_needed_evolve']

            print line
            print family_id
            for pokemon in self.family[family_id]:
                attack = 0
                defense = 0
                stamina = 0

                if pokemon['individual_attack']:
                    attack = pokemon['individual_attack']
                if pokemon['individual_defense']:
                    defense = pokemon['individual_defense']
                if pokemon['individual_stamina']:
                    stamina = pokemon['individual_stamina']
                if pokemon['isKeepMax']:
                    isKeepMax = 'M'
                else:
                    isKeepMax = ''

                if pokemon['isKeepEvo']:
                    isKeepEvo = '*'
                    evo_cnt += 1
                else:
                    isKeepEvo = ''

                if pokemon['isKeepCp']:
                    isKeepCp = 'C'
                else:
                    isKeepCp = ''

                if pokemon['isKeepMax'] or pokemon['isKeepEvo'] or pokemon['isKeepCp']:
                    keep_cnt += 1
                else:
                    release_cnt += 1

                pokemon_id = pokemon['pokemon_id']
                print '#%03d  %-15s (%4d)| Lv%3g  %4d -> [%3d] %4d -> [%6d, %3d] %4d-> [%6d, %3d] %4d / %4d (%3d %% ) |  %2d  %2d  %2d %01s%01s%01s %s' % (
                    pokemon_id, PokemonId.Name(pokemon_id), self.candy[family_id],
                    round(pokemon['level'], 1), pokemon['cp'],
                    pokemon['candy_needed_evolve'], pokemon['evolve_cp'],
                    pokemon['dust_needed_curr'], pokemon['candy_needed_curr'], pokemon['evolve_up_cp'],
                    pokemon['dust_needed_max'], pokemon['candy_needed_max'], pokemon['max_cp'],
                    pokemon['perfect_cp'], pokemon['pcp'] * 100,
                    attack, defense, stamina, isKeepEvo, isKeepCp, isKeepMax, pokemon['id'])

            print '# TOTAL = %d X [%d] = %d' % (
                len(self.family[family_id]), POKEDEX[family_id]['CandyToEvolve'],
                len(self.family[family_id]) * POKEDEX[family_id]['CandyToEvolve'])

        print line
        print title
        print 'TOTAL =', total_cnt
        print 'KEEP =', keep_cnt
        print 'RELEASE =', release_cnt
        print 'EVO =', evo_cnt

        if total_cnt - release_cnt >= self.profile['max_pokemon_storage'] - len(self.egg) and block_on_full:
            print '============== pokemon full, nothing to release'
            exit(0)

    @chain_api
    def summary(self):
        print 'PROFILE ='
        pprint.pprint(self.profile, indent=4)

        for _, v in self.incubator.iteritems():
            if 'pokemon_id' in v:
                if 'start_km_walked' not in v:
                    v['start_km_walked'] = 0
                current_km = round(self.profile['km_walked'] - v['start_km_walked'],3)
                target_km = round(v['target_km_walked'] - v['start_km_walked'],3)
                print 'INCUBATOR: {}/{}'.format(current_km, target_km)

        for k,v in self.item.iteritems():
            print "*%3d (%s) = %d" % (k, ItemId.Name(k), v)

        print "ITEM # =\n   ", self.profile['cnt_item']
        print 'POKEMON # =\n   ', self.profile['cnt_pokemon']
        print 'POSITION (lat,lng) = {},{}'.format(self._lat, self._lng)
        print 'POKESTOP # =', len(self.pokestop)
        print 'WILD POKEMON # =', len(self.wild_pokemon)
        print 'EGG # =\n   ', len(self.egg)

        exp = self.profile['experience'] - self.profile['prev_level_xp']
        exp_total = self.profile['next_level_xp'] - self.profile['prev_level_xp']
        print '====Lv %d, %d/%d (%.2f%%)' % (
            self.profile['level'], exp, exp_total, float(exp)/exp_total*100)

    # Scan the map around you
    @chain_api
    def scan(self):
        self.use_item_egg_incubator()
        self.wild_pokemon = []
        cell_ids = self._get_cell_ids()
        timestamps = [0, ] * len(cell_ids)
        self._get_player()
        self._get_inventory()
        self._get_hatched_eggs()
        self._req.get_map_objects(
            latitude=self._lat,
            longitude=self._lng,
            since_timestamp_ms=timestamps,
            cell_id=cell_ids)
        self._call()

    @chain_api
    def disk_catch_pokemon(self, lure_info):
        encounter_id = lure_info['encounter_id']
        fort_id = lure_info['fort_id']
        self._disk_encounter(encounter_id, fort_id)
        ret = self._call()
        if ret[0]:
            max_cp = ret[1]
            family_id = ret[2]
            self._choose_ball_and_catch(max_cp, family_id, encounter_id, fort_id)

    @chain_api
    def catch_pokemon(self, pokemon):

        self._encounter(pokemon)
        ret = self._call()
        if ret is None:
            return
        if ret[0]:
            max_cp = ret[1]
            family_id = ret[2]
            self._choose_ball_and_catch(max_cp, family_id, pokemon['encounter_id'], pokemon['spawn_point_id'])

    def _choose_ball_and_catch(self, max_cp, family_id, encounter_id, spawn_point_id):
            ret = -1
            for _ in range(10):
                if ret == -1 or ret == 2 or ret == 4:
                    if len(self.family[family_id]) == 0 or self.candy[family_id] < 200 or max_cp > 2500:
                        self.use_item_capture(encounter_id, spawn_point_id)
                        if self.item[ItemId.Value('ITEM_ULTRA_BALL')] > 0:
                            pokeball = ItemId.Value('ITEM_ULTRA_BALL')
                        elif self.item[ItemId.Value('ITEM_GREAT_BALL')] > 0:
                            pokeball = ItemId.Value('ITEM_GREAT_BALL')
                        elif self.item[ItemId.Value('ITEM_POKE_BALL')] > 0:
                            pokeball = ItemId.Value('ITEM_POKE_BALL')
                        else:
                            log.warning('CATCH_POKEMON no balls!')
                            return
                    else:
                        if self.item[ItemId.Value('ITEM_RAZZ_BERRY')] > 30:
                            self.use_item_capture(encounter_id, spawn_point_id)

                        cnt_poke_ball = self.item[ItemId.Value('ITEM_POKE_BALL')]
                        cnt_great_ball = self.item[ItemId.Value('ITEM_GREAT_BALL')]
                        cnt_ultra_ball = self.item[ItemId.Value('ITEM_ULTRA_BALL')]

                        if cnt_ultra_ball > 100:
                            pokeball = ItemId.Value('ITEM_ULTRA_BALL')
                        elif cnt_great_ball > 0 and cnt_great_ball + cnt_ultra_ball > 100:
                            pokeball = ItemId.Value('ITEM_GREAT_BALL')
                        elif cnt_poke_ball > 0:
                            pokeball = ItemId.Value('ITEM_POKE_BALL')
                        elif cnt_great_ball > 0:
                            pokeball = ItemId.Value('ITEM_GREAT_BALL')
                        elif cnt_ultra_ball > 0:
                            pokeball = ItemId.Value('ITEM_ULTRA_BALL')
                        else:
                            log.warning('CATCH_POKEMON no balls!')
                            return

                    self._catch_pokemon(pokeball, encounter_id, spawn_point_id)
                    ret = self._call()
                else:
                    break

    @chain_api
    def use_item_capture(self, encounter_id, spawn_point_id):
        for _ in range(3):
            if self.item[ItemId.Value('ITEM_RAZZ_BERRY')] > 0:
                self._req.use_item_capture(
                    item_id=ItemId.Value('ITEM_RAZZ_BERRY'),
                    encounter_id=encounter_id,
                    spawn_point_id=spawn_point_id)
                if self._call():
                    break
            else:
                log.info('USE_ITEM_CAPTURE, out of berry :(')
                break
    def _disk_encounter(self, encounter_id, fort_id):
        self._req.disk_encounter(
            encounter_id=encounter_id,
            fort_id=fort_id,
            player_latitude=self._lat,
            player_longitude=self._lng)

    def _encounter(self, pokemon):
        self._req.encounter(
            encounter_id=pokemon['encounter_id'],
            spawn_point_id=pokemon['spawn_point_id'],
            player_latitude=self._lat,
            player_longitude=self._lng)

    def _catch_pokemon(self, pokeball, encounter_id, spawn_point_id):
        self._req.catch_pokemon(
            encounter_id=encounter_id,
            pokeball=pokeball,
            normalized_reticle_size=1.950,
            spawn_point_id=spawn_point_id,
            hit_pokemon=True,
            spin_modifier=1,
            normalized_hit_position=1)

    @chain_api
    def use_item_egg_incubator(self):
        for _, incubator in self.incubator.iteritems():
            if 'pokemon_id' not in incubator:
                for egg in self.egg:
                    if 'egg_incubator_id' not in egg:
                        self._use_item_egg_incubator(incubator['id'], egg['id'])
                        self._call()
                        break

    def _use_item_egg_incubator(self, item_id, pokemon_id):
        self._req.use_item_egg_incubator(item_id=item_id, pokemon_id=pokemon_id)

    # Login
    def login(self, auth_service, username, password, auth_token=None):
        ret = self._api.login(
            auth_service, username, password,

            app_simulation=False, auth_token=auth_token)
        if ret:
            self.scan()
        return ret

    def test(self):
        self._req.get_player()
        self._req.get_inventory()
        resp = self._req._call()
        log.info('Response dictionary: \n\r{}'.format(json.dumps(resp, indent=2)))

    def _get_player(self):
        self._req.get_player()

    def _get_hatched_eggs(self):
        self._req.get_hatched_eggs()

    def _get_inventory(self):
        self.egg = []
        self.incubator = {}
        self.profile['cnt_pokemon'] = 0
        self.profile['cnt_item'] = 0
        self.family = defaultdict(list)
        self.candy = defaultdict(int)
        self.item = defaultdict(int)
        self._req.get_inventory()

    def _get_cell_ids(self, radius=10):
        lat = self._lat
        lng = self._lng
        origin = CellId.from_lat_lng(LatLng.from_degrees(lat, lng)).parent(15)
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

    def _encode(self, cellid):
        output = []
        encoder._VarintEncoder()(output.append, cellid)
        return ''.join(output)


def main():
    pass

if __name__ == '__main__':
    main()
