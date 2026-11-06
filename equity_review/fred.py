"""Optional FRED retrieval. Offline analysis does not require a credential."""
import os
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
import pandas as pd


def fetch_observations(series_id: str, start: str, end: str, opener=urlopen) -> pd.DataFrame:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise ValueError("Set FRED_API_KEY in the environment to refresh data")
    params = {"api_key": key, "series_id": series_id, "file_type": "json",
              "observation_start": start, "observation_end": end}
    try:
        with opener("https://api.stlouisfed.org/fred/series/observations?" + urlencode(params), timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError):
        # Request exceptions can include a URL containing the credential.
        raise RuntimeError("FRED request failed; check connectivity and configuration") from None
    if "observations" not in payload:
        raise ValueError("FRED response has no observations")
    frame = pd.DataFrame(payload["observations"])
    if not {"date", "value"}.issubset(frame):
        raise ValueError("Unexpected FRED observation schema")
    return pd.DataFrame({"Date": pd.to_datetime(frame.date, errors="raise"),
                         series_id: pd.to_numeric(frame.value.replace(".", float("nan")), errors="raise")})
