import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_session() -> requests.Session:
    """A requests.Session shared by every outbound service client.

    Session reuse gives connection keep-alive across calls (instead of a
    fresh TCP+TLS handshake per request), and the mounted Retry handles
    transient failures automatically: connection resets/timeouts and 5xx
    responses get retried with backoff before giving up. We hit exactly
    this class of transient failure against Nominatim during development
    (connection resets under load), which is what motivated this.
    """
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# One shared session per process, reused across requests.
SESSION = build_session()
