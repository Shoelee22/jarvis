"""Web actions provider (Phase 7 / Worker 5): site x action URL templates.

Namespace: web.<site>.<action>  (risk: low, needs_network: False)

Every template carries a {q} placeholder (a few static pages omit it); the
query is encoded with urllib.parse.quote_plus at call time. Only URL patterns
the author is confident are correct are included.

Handlers are offline-safe: they always return a valid URL, and only
*optionally* try to fetch the page <title> as a snippet when the "web"
config section enables fetch_snippets (default True). ANY fetch failure
still returns {"url": url, "snippet": None, "note": "offline or fetch
failed; URL is still valid"}. Handlers never raise.
"""
from __future__ import annotations

import html as _html
import re
import urllib.parse
import urllib.request

from ..base import Tool
from .providers import Provider

try:  # parallel worker contract
    from .config import section
except ImportError:  # pragma: no cover - defensive; config.py ships alongside
    def section(name, defaults=None):  # type: ignore[no-redef]
        return dict(defaults) if defaults else {}


# site -> {action -> URL template}. {q} is quote_plus-encoded at call time.
SITES: dict[str, dict[str, str]] = {
    # ---- general search engines ----
    "google": {
        "search": "https://www.google.com/search?q={q}",
        "images": "https://www.google.com/search?tbm=isch&q={q}",
        "videos": "https://www.google.com/search?tbm=vid&q={q}",
        "news": "https://www.google.com/search?tbm=nws&q={q}",
        "books": "https://www.google.com/search?tbm=bks&q={q}",
        "shopping": "https://www.google.com/search?tbm=shop&q={q}",
        "patents": "https://patents.google.com/?q={q}",
        "maps": "https://www.google.com/maps/search/{q}",
        "directions": "https://www.google.com/maps/dir/?api=1&destination={q}",
        "translate": "https://translate.google.com/?sl=auto&tl=en&text={q}",
        "scholar": "https://scholar.google.com/scholar?q={q}",
    },
    "bing": {
        "search": "https://www.bing.com/search?q={q}",
        "images": "https://www.bing.com/images/search?q={q}",
        "videos": "https://www.bing.com/videos/search?q={q}",
        "news": "https://www.bing.com/news/search?q={q}",
        "maps": "https://www.bing.com/maps?q={q}",
        "shopping": "https://www.bing.com/shop?q={q}",
    },
    "duckduckgo": {
        "search": "https://duckduckgo.com/?q={q}",
        "images": "https://duckduckgo.com/?q={q}&iax=images&ia=images",
        "videos": "https://duckduckgo.com/?q={q}&iax=videos&ia=videos",
        "news": "https://duckduckgo.com/?q={q}&iar=news&ia=news",
    },
    "brave": {
        "search": "https://search.brave.com/search?q={q}",
        "images": "https://search.brave.com/images?q={q}",
        "news": "https://search.brave.com/news?q={q}",
        "videos": "https://search.brave.com/videos?q={q}",
    },
    "yahoo": {
        "search": "https://search.yahoo.com/search?p={q}",
        "images": "https://images.search.yahoo.com/search/images?p={q}",
        "news": "https://news.search.yahoo.com/search?p={q}",
        "videos": "https://video.search.yahoo.com/search/video?p={q}",
    },
    "yandex": {
        "search": "https://yandex.com/search/?text={q}",
        "images": "https://yandex.com/images/search?text={q}",
    },
    "ecosia": {
        "search": "https://www.ecosia.org/search?q={q}",
        "images": "https://www.ecosia.org/images?q={q}",
    },
    "startpage": {"search": "https://www.startpage.com/sp/search?query={q}"},
    "mojeek": {"search": "https://www.mojeek.com/search?q={q}"},
    "qwant": {"search": "https://www.qwant.com/?q={q}"},
    "swisscows": {"search": "https://swisscows.com/en/web?query={q}"},
    "marginalia": {"search": "https://search.marginalia.nu/search?query={q}"},
    "perplexity": {"search": "https://www.perplexity.ai/search?q={q}"},
    "you": {"search": "https://you.com/search?q={q}"},
    "ask": {"search": "https://www.ask.com/web?q={q}"},
    "aol": {"search": "https://search.aol.com/aol/search?q={q}"},

    # ---- video / social ----
    "youtube": {
        "search": "https://www.youtube.com/results?search_query={q}",
        "music": "https://music.youtube.com/search?q={q}",
        "channel": "https://www.youtube.com/@{q}",
        "trending": "https://www.youtube.com/feed/trending",
    },
    "vimeo": {"search": "https://vimeo.com/search?q={q}"},
    "dailymotion": {"search": "https://www.dailymotion.com/search/{q}"},
    "twitch": {
        "search": "https://www.twitch.tv/search?term={q}",
        "channel": "https://www.twitch.tv/{q}",
    },
    "tiktok": {
        "search": "https://www.tiktok.com/search?q={q}",
        "user": "https://www.tiktok.com/@{q}",
    },
    "twitter": {
        "search": "https://twitter.com/search?q={q}",
        "profile": "https://twitter.com/{q}",
        "hashtag": "https://twitter.com/hashtag/{q}",
    },
    "x": {
        "search": "https://x.com/search?q={q}",
        "profile": "https://x.com/{q}",
    },
    "reddit": {
        "search": "https://www.reddit.com/search/?q={q}",
        "subreddit": "https://www.reddit.com/r/{q}/",
        "user": "https://www.reddit.com/user/{q}/",
    },
    "quora": {
        "search": "https://www.quora.com/search?q={q}",
        "profile": "https://www.quora.com/profile/{q}",
    },
    "linkedin": {
        "search": "https://www.linkedin.com/search/results/all/?keywords={q}",
        "jobs": "https://www.linkedin.com/jobs/search/?keywords={q}",
        "people": "https://www.linkedin.com/search/results/people/?keywords={q}",
        "companies": "https://www.linkedin.com/search/results/companies/?keywords={q}",
        "posts": "https://www.linkedin.com/search/results/content/?keywords={q}",
    },
    "facebook": {"search": "https://www.facebook.com/search/top?q={q}"},
    "instagram": {
        "hashtag": "https://www.instagram.com/explore/tags/{q}/",
        "profile": "https://www.instagram.com/{q}/",
    },
    "bluesky": {"search": "https://bsky.app/search?q={q}"},
    "tumblr": {
        "search": "https://www.tumblr.com/search/{q}",
        "blog": "https://{q}.tumblr.com",
    },
    "pinterest": {
        "search": "https://www.pinterest.com/search/pins/?q={q}",
        "profile": "https://www.pinterest.com/{q}/",
    },
    "flickr": {"search": "https://www.flickr.com/search/?text={q}"},

    # ---- music / audio ----
    "spotify": {"search": "https://open.spotify.com/search/{q}"},
    "soundcloud": {
        "search": "https://soundcloud.com/search?q={q}",
        "artist": "https://soundcloud.com/{q}",
    },
    "applemusic": {"search": "https://music.apple.com/us/search?term={q}"},
    "bandcamp": {"search": "https://bandcamp.com/search?q={q}"},
    "lastfm": {
        "search": "https://www.last.fm/search?q={q}",
        "artist": "https://www.last.fm/music/{q}",
    },
    "genius": {"search": "https://genius.com/search?q={q}"},
    "deezer": {"search": "https://www.deezer.com/search/{q}"},
    "mixcloud": {"search": "https://www.mixcloud.com/search/?q={q}"},
    "freesound": {"search": "https://freesound.org/search/?q={q}"},
    "applepodcasts": {"search": "https://podcasts.apple.com/us/search?term={q}"},
    "audible": {"search": "https://www.audible.in/search?keywords={q}"},

    # ---- dev / docs / packages ----
    "github": {
        "search": "https://github.com/search?q={q}",
        "repos": "https://github.com/search?q={q}&type=repositories",
        "code": "https://github.com/search?q={q}&type=code",
        "issues": "https://github.com/search?q={q}&type=issues",
        "topics": "https://github.com/topics/{q}",
        "user": "https://github.com/{q}",
        "gists": "https://gist.github.com/search?q={q}",
    },
    "gitlab": {"search": "https://gitlab.com/search?search={q}"},
    "stackoverflow": {
        "search": "https://stackoverflow.com/search?q={q}",
        "tagged": "https://stackoverflow.com/questions/tagged/{q}",
    },
    "stackexchange": {"search": "https://stackexchange.com/search?q={q}"},
    "mdn": {"search": "https://developer.mozilla.org/en-US/search?q={q}"},
    "pypi": {
        "search": "https://pypi.org/search/?q={q}",
        "project": "https://pypi.org/project/{q}/",
        "user": "https://pypi.org/user/{q}/",
    },
    "npm": {
        "search": "https://www.npmjs.com/search?q={q}",
        "package": "https://www.npmjs.com/package/{q}",
    },
    "crates": {
        "search": "https://crates.io/search?q={q}",
        "crate": "https://crates.io/crates/{q}",
    },
    "pubmed": {
        "search": "https://pubmed.ncbi.nlm.nih.gov/?term={q}",
        "recent": "https://pubmed.ncbi.nlm.nih.gov/?term={q}&sort=date",
    },
    "arxiv": {
        "search": "https://arxiv.org/search/?query={q}&searchtype=all",
        "paper": "https://arxiv.org/abs/{q}",
    },
    "dockerhub": {
        "search": "https://hub.docker.com/search?q={q}",
        "official": "https://hub.docker.com/_/{q}",
    },
    "huggingface": {
        "models": "https://huggingface.co/models?search={q}",
        "datasets": "https://huggingface.co/datasets?search={q}",
    },
    "kaggle": {"search": "https://www.kaggle.com/search?q={q}"},
    "devto": {
        "search": "https://dev.to/search?q={q}",
        "tag": "https://dev.to/t/{q}",
    },
    "medium": {
        "search": "https://medium.com/search?q={q}",
        "tag": "https://medium.com/tag/{q}",
    },
    "caniuse": {"search": "https://caniuse.com/?search={q}"},
    "pkggodev": {"search": "https://pkg.go.dev/search?q={q}"},
    "docsrs": {"search": "https://docs.rs/releases/search?query={q}"},
    "rubygems": {
        "search": "https://rubygems.org/search?query={q}",
        "gem": "https://rubygems.org/gems/{q}",
    },
    "nuget": {"search": "https://www.nuget.org/packages?q={q}"},
    "pubdev": {"search": "https://pub.dev/packages?q={q}"},
    "maven": {"search": "https://search.maven.org/search?q={q}"},
    "codepen": {"search": "https://codepen.io/search/pens?q={q}"},
    "pythondocs": {"search": "https://docs.python.org/3/search.html?q={q}"},
    "phpnet": {"search": "https://www.php.net/manual-lookup.php?pattern={q}"},
    "postgresdocs": {"search": "https://www.postgresql.org/search/?q={q}"},
    "gitdocs": {"search": "https://git-scm.com/search/results?search={q}"},
    "archwiki": {"search": "https://wiki.archlinux.org/index.php?search={q}"},
    "explainshell": {"explain": "https://explainshell.com/explain?cmd={q}"},
    "cheatsh": {"cheat": "https://cheat.sh/{q}"},
    "appledevdocs": {"search": "https://developer.apple.com/search/?q={q}"},
    "godotdocs": {"search": "https://docs.godotengine.org/en/stable/search.html?q={q}"},
    "githubdocs": {"search": "https://docs.github.com/en/search?query={q}"},
    "sourcegraph": {"search": "https://sourcegraph.com/search?q={q}"},
    "grepapp": {"search": "https://grep.app/search?q={q}"},
    "searchcode": {"search": "https://searchcode.com/?q={q}"},
    "librariesio": {"search": "https://libraries.io/search?q={q}"},
    "jsdelivr": {"package": "https://www.jsdelivr.com/package/npm/{q}"},
    "unpkg": {"package": "https://unpkg.com/{q}/"},
    "bundlephobia": {"package": "https://bundlephobia.com/package/{q}"},
    "googledrive": {"search": "https://drive.google.com/drive/search?q={q}"},

    # ---- shopping ----
    "amazon": {"search": "https://www.amazon.in/s?k={q}"},
    "amazonus": {"search": "https://www.amazon.com/s?k={q}"},
    "flipkart": {"search": "https://www.flipkart.com/search?q={q}"},
    "ebay": {"search": "https://www.ebay.com/sch/i.html?_nkw={q}"},
    "etsy": {"search": "https://www.etsy.com/search?q={q}"},
    "walmart": {"search": "https://www.walmart.com/search?q={q}"},
    "bestbuy": {"search": "https://www.bestbuy.com/site/searchpage.jsp?st={q}"},
    "target": {"search": "https://www.target.com/s?searchTerm={q}"},
    "aliexpress": {"search": "https://www.aliexpress.com/wholesale?SearchText={q}"},
    "newegg": {"search": "https://www.newegg.com/p/pl?d={q}"},
    "bhphoto": {"search": "https://www.bhphotovideo.com/c/search?q={q}"},
    "nike": {"search": "https://www.nike.com/in/w?q={q}"},
    "adidas": {"search": "https://www.adidas.co.in/search?q={q}"},
    "wayfair": {"search": "https://www.wayfair.com/keyword.php?keyword={q}"},
    "homedepot": {"search": "https://www.homedepot.com/s/{q}"},
    "lowes": {"search": "https://www.lowes.com/search?searchTerm={q}"},
    "kohls": {"search": "https://www.kohls.com/search.jsp?search={q}"},
    "macys": {"search": "https://www.macys.com/shop/search?keyword={q}"},
    "nordstrom": {"search": "https://www.nordstrom.com/sr?origin=keywordsearch&keyword={q}"},
    "gap": {"search": "https://www.gap.com/search?text={q}"},
    "ajio": {"search": "https://www.ajio.com/search/?text={q}"},
    "nykaa": {"search": "https://www.nykaa.com/search/result/?q={q}"},
    "bigbasket": {"search": "https://www.bigbasket.com/ps/?q={q}"},
    "reliancedigital": {"search": "https://www.reliancedigital.in/search?q={q}"},
    "croma": {"search": "https://www.croma.com/searchB?q={q}"},
    "pepperfry": {"search": "https://www.pepperfry.com/search?q={q}"},
    "urbanladder": {"search": "https://www.urbanladder.com/search?q={q}"},
    "boat": {"search": "https://www.boat-lifestyle.com/search?q={q}"},

    # ---- travel / maps / food ----
    "openstreetmap": {"search": "https://www.openstreetmap.org/search?query={q}"},
    "booking": {"search": "https://www.booking.com/searchresults.html?ss={q}"},
    "airbnb": {
        "search": "https://www.airbnb.co.in/s/{q}/homes",
        "experiences": "https://www.airbnb.co.in/s/{q}/experiences",
    },
    "tripadvisor": {"search": "https://www.tripadvisor.in/Search?q={q}"},
    "yelp": {"search": "https://www.yelp.com/search?find_desc={q}"},
    "expedia": {"search": "https://www.expedia.com/Hotel-Search?destination={q}"},
    "allrecipes": {"search": "https://www.allrecipes.com/search?q={q}"},

    # ---- reference / learning ----
    "wikipedia": {
        "search": "https://en.wikipedia.org/wiki/Special:Search?search={q}",
        "article": "https://en.wikipedia.org/wiki/{q}",
        "commons": "https://commons.wikimedia.org/w/index.php?search={q}",
        "wiktionary": "https://en.wiktionary.org/wiki/{q}",
    },
    "wikihow": {"search": "https://www.wikihow.com/wikiHowTo?search={q}"},
    "britannica": {"search": "https://www.britannica.com/search?query={q}"},
    "dictionary": {"define": "https://www.dictionary.com/browse/{q}"},
    "thesaurus": {"synonyms": "https://www.thesaurus.com/browse/{q}"},
    "merriamwebster": {
        "define": "https://www.merriam-webster.com/dictionary/{q}",
        "thesaurus": "https://www.merriam-webster.com/thesaurus/{q}",
    },
    "urbandictionary": {"define": "https://www.urbandictionary.com/define.php?term={q}"},
    "wolframalpha": {"compute": "https://www.wolframalpha.com/input?i={q}"},
    "coursera": {"search": "https://www.coursera.org/search?query={q}"},
    "udemy": {"search": "https://www.udemy.com/courses/search/?q={q}"},
    "scholar": {"search": "https://scholar.google.com/scholar?q={q}"},
    "semanticscholar": {"search": "https://www.semanticscholar.org/search?q={q}"},
    "dblp": {"search": "https://dblp.org/search?q={q}"},
    "ted": {"search": "https://www.ted.com/search?q={q}"},
    "howstuffworks": {"search": "https://www.howstuffworks.com/search.php?terms={q}"},
    "instructables": {"search": "https://www.instructables.com/search/?q={q}"},
    "hackaday": {"search": "https://hackaday.com/?s={q}"},
    "thingiverse": {"search": "https://www.thingiverse.com/search?q={q}"},
    "sketchfab": {"search": "https://sketchfab.com/search?q={q}"},
    "itchio": {"search": "https://itch.io/search?q={q}"},
    "steam": {"search": "https://store.steampowered.com/search/?term={q}"},
    "epicgames": {"search": "https://store.epicgames.com/en-US/browse?q={q}"},

    # ---- news ----
    "bbc": {"search": "https://www.bbc.co.uk/search?q={q}"},
    "guardian": {"search": "https://www.theguardian.com/search?q={q}"},
    "nytimes": {"search": "https://www.nytimes.com/search?query={q}"},
    "cnn": {"search": "https://edition.cnn.com/search?q={q}"},
    "techcrunch": {"search": "https://techcrunch.com/?s={q}"},
    "theverge": {"search": "https://www.theverge.com/search?q={q}"},
    "wired": {"search": "https://www.wired.com/search/?q={q}"},
    "hackernews": {"search": "https://hn.algolia.com/?q={q}"},
    "producthunt": {"search": "https://www.producthunt.com/search?q={q}"},
    "thehindu": {"search": "https://www.thehindu.com/search/?q={q}"},
    "indianexpress": {"search": "https://indianexpress.com/?s={q}"},
    "hindustantimes": {"search": "https://www.hindustantimes.com/search?q={q}"},
    "livemint": {"search": "https://www.livemint.com/search?q={q}"},
    "thewire": {"search": "https://thewire.in/?s={q}"},
    "dailymail": {"search": "https://www.dailymail.co.uk/home/search.html?sel=site&searchPhrase={q}"},
    "independent": {"search": "https://www.independent.co.uk/search?query={q}"},
    "telegraph": {"search": "https://www.telegraph.co.uk/search/?queryText={q}"},
    "ft": {"search": "https://www.ft.com/search?q={q}"},
    "wsj": {"search": "https://www.wsj.com/search?query={q}"},
    "washingtonpost": {"search": "https://www.washingtonpost.com/search/?query={q}"},
    "latimes": {"search": "https://www.latimes.com/search?q={q}"},
    "npr": {"search": "https://www.npr.org/search/?query={q}"},
    "foxnews": {"search": "https://www.foxnews.com/search?q={q}"},
    "skynews": {"search": "https://news.sky.com/search?q={q}"},
    "bloomberg": {"search": "https://www.bloomberg.com/search?query={q}"},
    "forbes": {"search": "https://www.forbes.com/search/?q={q}"},
    "businessinsider": {"search": "https://www.businessinsider.com/s?q={q}"},
    "economist": {"search": "https://www.economist.com/search?q={q}"},
    "newyorker": {"search": "https://www.newyorker.com/search/q/{q}"},
    "atlantic": {"search": "https://www.theatlantic.com/search/?q={q}"},
    "vox": {"search": "https://www.vox.com/search?q={q}"},
    "slate": {"search": "https://slate.com/search?q={q}"},
    "buzzfeed": {"search": "https://www.buzzfeed.com/search?q={q}"},
    "mashable": {"search": "https://mashable.com/search/?q={q}"},
    "gizmodo": {"search": "https://gizmodo.com/search?q={q}"},
    "lifehacker": {"search": "https://lifehacker.com/search?q={q}"},
    "engadget": {"search": "https://www.engadget.com/search/?q={q}"},
    "arstechnica": {"search": "https://arstechnica.com/search/?query={q}"},
    "zdnet": {"search": "https://www.zdnet.com/search/?q={q}"},
    "cnet": {"search": "https://www.cnet.com/search/?query={q}"},
    "tomsguide": {"search": "https://www.tomsguide.com/search?searchTerm={q}"},
    "androidauthority": {"search": "https://www.androidauthority.com/?s={q}"},
    "9to5mac": {"search": "https://9to5mac.com/?s={q}"},
    "9to5google": {"search": "https://9to5google.com/?s={q}"},
    "polygon": {"search": "https://www.polygon.com/search?q={q}"},
    "ign": {"search": "https://www.ign.com/search?q={q}"},
    "gamespot": {"search": "https://www.gamespot.com/search/?q={q}"},
    "eurogamer": {"search": "https://www.eurogamer.net/search?q={q}"},
    "kotaku": {"search": "https://kotaku.com/search?q={q}"},
    "pcgamer": {"search": "https://www.pcgamer.com/search?searchTerm={q}"},

    # ---- movies / books / images ----
    "imdb": {"search": "https://www.imdb.com/find?q={q}"},
    "rottentomatoes": {"search": "https://www.rottentomatoes.com/search?search={q}"},
    "netflix": {"search": "https://www.netflix.com/search?q={q}"},
    "justwatch": {"search": "https://www.justwatch.com/us/search?q={q}"},
    "goodreads": {"search": "https://www.goodreads.com/search?query={q}"},
    "openlibrary": {
        "search": "https://openlibrary.org/search?q={q}",
        "authors": "https://openlibrary.org/search/authors?q={q}",
    },
    "archive": {"search": "https://archive.org/search?query={q}"},
    "letterboxd": {"search": "https://letterboxd.com/search/{q}/"},
    "thetvdb": {"search": "https://thetvdb.com/search?query={q}"},
    "tvmaze": {"search": "https://www.tvmaze.com/search?q={q}"},
    "anilist": {"search": "https://anilist.co/search/anime?search={q}"},
    "myanimelist": {"search": "https://myanimelist.net/anime.php?q={q}"},
    "crunchyroll": {"search": "https://www.crunchyroll.com/search?q={q}"},
    "wattpad": {"search": "https://www.wattpad.com/search/{q}"},
    "unsplash": {
        "search": "https://unsplash.com/s/photos/{q}",
        "user": "https://unsplash.com/@{q}",
    },
    "pexels": {"search": "https://www.pexels.com/search/{q}/"},
    "pixabay": {"search": "https://pixabay.com/images/search/{q}/"},
    "giphy": {
        "search": "https://giphy.com/search/{q}",
        "stickers": "https://giphy.com/stickers/{q}",
    },
    "tenor": {"search": "https://tenor.com/search/{q}-gifs"},
    "shutterstock": {"search": "https://www.shutterstock.com/search/{q}"},
    "gettyimages": {"search": "https://www.gettyimages.in/search/2/image?phrase={q}"},
    "adobestock": {"search": "https://stock.adobe.com/in/search?k={q}"},
    "freepik": {"search": "https://www.freepik.com/search?query={q}"},
    "flaticon": {"search": "https://www.flaticon.com/search?word={q}"},
    "thenounproject": {"search": "https://thenounproject.com/search/?q={q}"},
    "googlefonts": {"search": "https://fonts.google.com/?query={q}"},
    "dafont": {"search": "https://www.dafont.com/search.php?q={q}"},
    "artstation": {"search": "https://www.artstation.com/search?q={q}"},
    "deviantart": {"search": "https://www.deviantart.com/search?q={q}"},

    # ---- jobs ----
    "indeed": {
        "search": "https://www.indeed.com/jobs?q={q}",
        "india": "https://in.indeed.com/jobs?q={q}",
        "uk": "https://uk.indeed.com/jobs?q={q}",
    },
    "ziprecruiter": {"search": "https://www.ziprecruiter.com/candidate/search?search={q}"},
    "monster": {"search": "https://www.monster.com/jobs/search?q={q}"},
    "dice": {"search": "https://www.dice.com/jobs?q={q}"},

    # ---- finance ----
    "yahoofinance": {
        "quote": "https://finance.yahoo.com/quote/{q}",
        "news": "https://finance.yahoo.com/quote/{q}/news",
    },
    "marketwatch": {"quote": "https://www.marketwatch.com/investing/stock/{q}"},
    "cnbc": {"quote": "https://www.cnbc.com/quotes/{q}"},
    "investingcom": {"search": "https://www.investing.com/search/?q={q}"},
    "tradingview": {"search": "https://www.tradingview.com/search/?q={q}"},

    # ---- misc ----
    "downforeveryone": {"check": "https://downforeveryoneorjustme.com/{q}"},
    "alternativeto": {"search": "https://alternativeto.net/browse/search?q={q}"},
    "openweathermap": {"search": "https://openweathermap.org/find?q={q}"},
    "dribbble": {"search": "https://dribbble.com/search/{q}"},
}

# --- Phase 10: extra sites (web_extra.py; +313 pairs, 621 total) ---
# SITES_BASE stays pristine so tests can verify the extra file independently.
SITES_BASE = {k: dict(v) for k, v in SITES.items()}
try:
    from .web_extra import WEB_EXTRA
    for _k, _v in WEB_EXTRA.items():
        SITES.setdefault(_k, {}).update(_v)
except ImportError:
    pass

#: Total site x action pairs; tests assert this is >= 300.
PAIR_COUNT = sum(len(actions) for actions in SITES.values())

_UA = "Mozilla/5.0 (compatible; JarvisSidecar/1.0)"
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _fetch_title(url: str, timeout: int = 8) -> str | None:
    """Best-effort <title> extraction. Raises on any failure; caller swallows."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read(200_000)
    text = raw.decode("utf-8", errors="replace")
    m = _TITLE_RE.search(text)
    if not m:
        return None
    title = re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()
    return title or None


def _make_handler(site: str, action: str, template: str):
    def handler(args: dict) -> dict:
        try:
            q = args.get("q", "") if isinstance(args, dict) else ""
            q = "" if q is None else str(q)
            url = (template.replace("{q}", urllib.parse.quote_plus(q))
                   if "{q}" in template else template)
            snippet: str | None = None
            try:
                cfg = section("web", {"fetch_snippets": True})
                if cfg.get("fetch_snippets", True):
                    snippet = _fetch_title(url)
            except Exception:
                snippet = None
            if snippet:
                return {"url": url, "snippet": snippet}
            return {"url": url, "snippet": None,
                    "note": "offline or fetch failed; URL is still valid"}
        except Exception as e:  # never raise
            return {"error": f"{type(e).__name__}: {e}"}
    return handler


class WebActionsProvider(Provider):
    """Exposes web.<site>.<action> tools built from URL templates."""

    namespace = "web"

    def expand(self) -> int:
        """Total site x action pairs. Cheap: precomputed constant."""
        return PAIR_COUNT

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        parts = name.split(".")
        if len(parts) != 3:
            return None
        site, action = parts[1], parts[2]
        actions = SITES.get(site)
        if not actions or action not in actions:
            return None
        template = actions[action]
        return Tool(
            name=name,
            description=f"Open {site} '{action}' for a query (URL template).",
            schema={"q": "string"},
            handler=_make_handler(site, action, template),
            risk="low",
            needs_network=False,
        )

    def sample_names(self, n: int = 5) -> list[str]:
        names: list[str] = []
        for site in sorted(SITES):
            for action in sorted(SITES[site]):
                names.append(f"web.{site}.{action}")
                if len(names) >= max(0, n):
                    return names
        return names
