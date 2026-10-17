"""LinkedIn: OAuth (openid profile w_member_social), text + document posts, comments. Stdlib HTTP, sync."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.linkedin.com"
VERSION = "202608"
SCOPES = "openid profile w_member_social"
LTF_SPECIAL = r"\()[]{}<>@#*_~|"


class LinkedInError(RuntimeError):
    pass


def ltf_escape(text: str) -> str:
    s = "".join("\\" + ch if ch in LTF_SPECIAL else ch for ch in text)
    return re.sub(r"\\#(\w+)", r"{hashtag|\\#|\1}", s)


def _call(method, url, body=None, headers=None, raw=None, tries=4):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode() or "{}"
                return (json.loads(txt) if txt.strip().startswith("{") else txt), r.headers
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:1000]
            if e.code == 401:
                raise LinkedInError("LinkedIn token expired or invalid: reconnect LinkedIn in Settings")
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise LinkedInError(f"LinkedIn {e.code} on {method} {url}: {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise LinkedInError(f"LinkedIn unreachable: {e}")


def _h(token, extra=None):
    h = {"Authorization": "Bearer " + token, "LinkedIn-Version": VERSION, "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    return "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(
        {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state, "scope": SCOPES})


def exchange(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Returns {access_token, person_urn, name, expires_at (epoch seconds)}."""
    tok, _ = _call("POST", "https://www.linkedin.com/oauth/v2/accessToken",
                   raw=urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                               "client_id": client_id, "client_secret": client_secret}).encode(),
                   headers={"Content-Type": "application/x-www-form-urlencoded"}, tries=1)
    me, _ = _call("GET", f"{API}/v2/userinfo", headers={"Authorization": "Bearer " + tok["access_token"]})
    return {"access_token": tok["access_token"], "person_urn": f"urn:li:person:{me['sub']}", "name": me.get("name", ""),
            "expires_at": int(time.time()) + int(tok.get("expires_in", 0))}


def publish(token: str, author: str, text: str, pdf: bytes | None = None, title: str = "") -> str:
    body = {"author": author, "commentary": ltf_escape(text), "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    if pdf:
        init, _ = _call("POST", f"{API}/rest/documents?action=initializeUpload", {"initializeUploadRequest": {"owner": author}}, headers=_h(token))
        v = init["value"]
        _call("PUT", v["uploadUrl"], raw=pdf, headers={"Authorization": "Bearer " + token, "Content-Type": "application/pdf"})
        body["content"] = {"media": {"title": (title or "Carousel")[:100], "id": v["document"]}}
    _, hdrs = _call("POST", f"{API}/rest/posts", body, headers=_h(token), tries=1)  # never retry a create: a lost response must not double-post
    urn = hdrs.get("x-restli-id")
    if not urn:
        raise LinkedInError("post created but LinkedIn returned no x-restli-id; check your feed before retrying")
    return urn


def comment(token: str, author: str, post_urn: str, text: str) -> str:
    url = f"{API}/rest/socialActions/{urllib.parse.quote(post_urn, safe='')}/comments"
    out, _ = _call("POST", url, {"actor": author, "message": {"text": text}}, headers=_h(token), tries=1)
    return out.get("$URN", "") if isinstance(out, dict) else ""
