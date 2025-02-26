"""Scraper for http://site.api.espn.com/apis/site/v2/sports/soccer."""
import datetime
import itertools
import json
import re
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd
import requests

from ._common import BaseReader, standardize_colnames
from ._config import DATA_DIR, NOCACHE, NOSTORE, TEAMNAME_REPLACEMENTS

# http://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event=513466
# http://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates=20180901

ESPN_DATADIR = DATA_DIR / 'ESPN'
ESPN_API = 'http://site.api.espn.com/apis/site/v2/sports/soccer'


class ESPN(BaseReader):
    """Provides pd.DataFrames from JSON api available at http://site.api.espn.com.

    Data will be downloaded as necessary and cached locally in
    ``~/soccerdata/data/ESPN``.

    Parameters
    ----------
    leagues : string or iterable, optional
        IDs of leagues to include.
    seasons : string, int or list, optional
        Seasons to include. Supports multiple formats.
        Examples: '16-17'; 2016; '2016-17'; [14, 15, 16]
    use_tor : bool
        Whether to use the Tor network to hide your IP.
    no_cache : bool
        If True, will not use cached data.
    no_store : bool
        If True, will not store downloaded data.
    data_dir : Path
        Path to directory where data will be cached.
    """

    def __init__(
        self,
        leagues: Optional[Union[str, List[str]]] = None,
        seasons: Optional[Union[str, int, List]] = None,
        use_tor: bool = False,
        no_cache: bool = NOCACHE,
        no_store: bool = NOSTORE,
        data_dir: Path = ESPN_DATADIR,
    ):
        """Initialize a new ESPN reader."""
        super().__init__(
            leagues=leagues,
            use_tor=use_tor,
            no_cache=no_cache,
            no_store=no_store,
            data_dir=data_dir,
        )
        self.seasons = seasons  # type: ignore

    def read_schedule(self, force_cache: bool = False) -> pd.DataFrame:
        """Retrieve the game schedule for the selected leagues and seasons.

        Parameters
        ----------
        force_cache : bool
             By default no cached data is used for the current season.
             If True, will force the use of cached data anyway.

        Returns
        -------
        pd.DataFrame
        """
        urlmask = ESPN_API + '/{}/scoreboard?dates={}'
        filemask = 'Schedule_{}_{}.json'

        df_list = []
        # Get match days
        for lkey, skey in itertools.product(self._selected_leagues.values(), self.seasons):
            if int(skey[:2]) > int(str(datetime.datetime.now().year + 1)[-2:]):
                start_date = ''.join(['19', skey[:2], '07', '01'])
            else:
                start_date = ''.join(['20', skey[:2], '07', '01'])

            url = urlmask.format(lkey, start_date)
            resp = requests.get(url=url)
            data = resp.json()

            match_dates = [
                datetime.datetime.strptime(d, '%Y-%m-%dT%H:%MZ').strftime('%Y%m%d')
                for d in data['leagues'][0]['calendar']
            ]
            for date in match_dates:
                url = urlmask.format(lkey, date)
                filepath = self.data_dir / filemask.format(lkey, date)
                current_season = not self._is_complete(lkey, skey)
                reader = self._download_and_save(
                    url, filepath, no_cache=current_season and not force_cache
                )

                data = json.load(reader)
                df_list.extend(
                    [
                        {
                            'game_id': int(e['id']),
                            'league_id': lkey,
                            'season': skey,
                            'date': e['date'],
                            'home_team': e['competitions'][0]['competitors'][0]['team']['name'],
                            'away_team': e['competitions'][0]['competitors'][1]['team']['name'],
                        }
                        for e in data['events']
                    ]
                )
        df = (
            pd.DataFrame(df_list)
            .assign(league=lambda x: x.league_id)
            .pipe(self._translate_league)
            .replace({'home_team': TEAMNAME_REPLACEMENTS, 'away_team': TEAMNAME_REPLACEMENTS})
            .dropna(subset=['home_team', 'away_team'])
            .set_index(['league', 'season', 'game_id'])
            .sort_index()
        )

        return df

    def read_matchsheet(self, match_id: Optional[Union[int, List[int]]] = None) -> pd.DataFrame:
        """Retrieve match sheets for the selected leagues and seasons.

        Parameters
        ----------
        match_id : int or list of int, optional
            Retrieve the match sheet for a specific game.

        Raises
        ------
        ValueError
            If no games with the given IDs were found for the selected seasons and leagues.

        Returns
        -------
        pd.DataFrame.
        """
        urlmask = ESPN_API + '/{}/summary?event={}'
        filemask = 'Summary_{}.json'

        df_schedule = self.read_schedule().reset_index()
        if match_id is not None:
            iterator = df_schedule[
                df_schedule.game_id.isin([match_id] if isinstance(match_id, int) else match_id)
            ]
            if len(iterator) == 0:
                raise ValueError(
                    'No games with the given IDs found for the selected seasons and leagues.'
                )
        else:
            iterator = df_schedule

        df_list = []
        for i, match in iterator.iterrows():
            url = urlmask.format(match['league_id'], match['game_id'])
            filepath = self.data_dir / filemask.format(match['game_id'])
            reader = self._download_and_save(url, filepath)

            data = json.load(reader)
            for i in range(2):
                match_sheet = {
                    'game_id': int(match['game_id']),
                    'league': match['league'],
                    'season': match['season'],
                    'team': data['boxscore']['form'][i]['team']['displayName'],
                    'is_home': (i == 0),
                    'venue': data['gameInfo']['venue']['fullName']
                    if 'venue' in data['gameInfo']
                    else None,
                    'attendance': data['gameInfo']['attendance'],
                    'capacity': data['gameInfo']['venue']['capacity']
                    if 'venue' in data['gameInfo']
                    else None,
                    'roster': data['rosters'][i]['roster'],
                }
                if 'statistics' in data['boxscore']['teams'][i]:
                    for stat in data['boxscore']['teams'][i]['statistics']:
                        match_sheet[stat['name']] = stat['displayValue']
                df_list.append(match_sheet)
        df = (
            pd.DataFrame(df_list)
            .replace({'team': TEAMNAME_REPLACEMENTS})
            .pipe(standardize_colnames)
            .set_index(['league', 'season', 'game_id', 'team'])
            .sort_index()
        )
        return df

    def read_lineup(  # noqa: C901
        self, match_id: Optional[Union[int, List[int]]] = None
    ) -> pd.DataFrame:
        """Retrieve lineups for the selected leagues and seasons.

        Parameters
        ----------
        match_id : int or list of int, optional
            Retrieve the lineup for a specific game.

        Raises
        ------
        ValueError
            If no games with the given IDs were found for the selected seasons and leagues.

        Returns
        -------
        pd.DataFrame.
        """
        urlmask = ESPN_API + '/{}/summary?event={}'
        filemask = 'Summary_{}.json'

        df_schedule = self.read_schedule().reset_index()
        if match_id is not None:
            iterator = df_schedule[
                df_schedule.game_id.isin([match_id] if isinstance(match_id, int) else match_id)
            ]
            if len(iterator) == 0:
                raise ValueError(
                    'No games with the given IDs found for the selected seasons and leagues.'
                )
        else:
            iterator = df_schedule

        df_list = []
        for i, match in iterator.iterrows():
            url = urlmask.format(match['league_id'], match['game_id'])
            filepath = self.data_dir / filemask.format(match['game_id'])
            reader = self._download_and_save(url, filepath)

            data = json.load(reader)
            for i in range(2):
                for p in data['rosters'][i]['roster']:
                    match_sheet = {
                        'game_id': match['game_id'],
                        'league': match['league'],
                        'season': match['season'],
                        'team': data['boxscore']['form'][i]['team']['displayName'],
                        'is_home': (i == 0),
                        'player': p['athlete']['displayName'],
                        'position': p['position']['name'] if 'position' in p else None,
                        'formation_place': p['formationPlace'] if 'formationPlace' in p else None,
                    }

                    if p['starter']:
                        match_sheet['sub_in'] = 'start'
                    elif p['subbedIn']:
                        ii = [i for i, x in enumerate(p['plays']) if x['substitution']][0]
                        match_sheet['sub_in'] = sum(
                            map(
                                int,
                                re.findall(
                                    r'(\d{1,3})',
                                    p['plays'][ii]['clock']['displayValue'],
                                ),
                            )
                        )
                    else:
                        match_sheet['sub_in'] = None

                    if (p['starter'] or p['subbedIn']) and not p['subbedOut']:
                        match_sheet['sub_out'] = 'end'
                    elif p['subbedOut']:
                        j = 0 if not p['subbedIn'] else 1
                        ii = [i for i, x in enumerate(p['plays']) if x['substitution']][j]
                        match_sheet['sub_out'] = sum(
                            map(
                                int,
                                re.findall(
                                    r'(\d{1,3})',
                                    p['plays'][ii]['clock']['displayValue'],
                                ),
                            )
                        )
                    else:
                        match_sheet['sub_out'] = None

                    if 'stats' in p:
                        for stat in p['stats']:
                            match_sheet[stat['name']] = stat['value']

                    df_list.append(match_sheet)
        df = (
            pd.DataFrame(df_list)
            .replace({'team': TEAMNAME_REPLACEMENTS})
            .pipe(standardize_colnames)
            .set_index(['league', 'season', 'game_id', 'team', 'player'])
            .sort_index()
        )
        return df
