"""Scraper for http://www.football-data.co.uk/data.php."""
import itertools
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from ._common import BaseReader, make_game_id
from ._config import DATA_DIR, NOCACHE, NOSTORE, TEAMNAME_REPLACEMENTS

MATCH_HISTORY_DATA_DIR = DATA_DIR / 'MatchHistory'
MATCH_HISTORY_API = 'https://www.football-data.co.uk'


class MatchHistory(BaseReader):
    """Provides pd.DataFrames from CSV files available at http://www.football-data.co.uk/data.php.

    Data will be downloaded as necessary and cached locally in
    ``~/soccerdata/data/MatchHistory``.

    Parameters
    ----------
    leagues : string or iterable
        IDs of leagues to include.
    seasons : string, int or list
        Seasons to include. Supports multiple formats.
        Examples: '16-17'; 2016; '2016-17'; [14, 15, 16]
    no_cache : bool
        If True, will not use cached data.
    no_store : bool
        If True, will not store downloaded data.
    data_dir : Path, optional
        Path to directory where data will be cached.
    """

    def __init__(
        self,
        leagues: Optional[Union[str, List[str]]] = None,
        seasons: Optional[Union[str, int, List]] = None,
        no_cache: bool = NOCACHE,
        no_store: bool = NOSTORE,
        data_dir: Path = MATCH_HISTORY_DATA_DIR,
    ):
        super().__init__(leagues=leagues, no_cache=no_cache, no_store=no_store, data_dir=data_dir)
        self.seasons = seasons  # type: ignore

    def read_games(self) -> pd.DataFrame:
        """Retrieve game history for the selected leagues and seasons.

        Column names are explained here: http://www.football-data.co.uk/notes.txt

        Returns
        -------
        pd.DataFrame
        """
        urlmask = MATCH_HISTORY_API + '/mmz4281/{}/{}.csv'
        filemask = '{}_{}.csv'
        col_rename = {
            'Div': 'league',
            'Date': 'date',
            'Time': 'time',
            'HomeTeam': 'home_team',
            'AwayTeam': 'away_team',
            'Referee': 'referee',
        }

        df_list = []
        for lkey, skey in itertools.product(self._selected_leagues.values(), self.seasons):
            filepath = self.data_dir / filemask.format(lkey, skey)
            url = urlmask.format(skey, lkey)
            current_season = not self._is_complete(lkey, skey)
            reader = self._download_and_save(url, filepath, no_cache=current_season)

            df_list.append(
                pd.read_csv(
                    reader,
                    parse_dates=['Date'],
                    infer_datetime_format=True,
                    dayfirst=True,
                    encoding='ISO-8859-1',
                ).assign(season=skey)
            )

        df = (
            pd.concat(df_list, sort=False)
            .rename(columns=col_rename)
            .pipe(self._translate_league)
            .replace(
                {
                    'home_team': TEAMNAME_REPLACEMENTS,
                    'away_team': TEAMNAME_REPLACEMENTS,
                }
            )
            .dropna(subset=['home_team', 'away_team'])
        )

        df['game_id'] = df.apply(make_game_id, axis=1)
        df.set_index(['league', 'season', 'game_id'], inplace=True)
        df.sort_index(inplace=True)
        return df
