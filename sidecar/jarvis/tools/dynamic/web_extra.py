"""Web site expansion (Phase 10 / Workstream D2): 200+ extra sites.

MERGE NOTE (for the parent orchestrator):
    This module intentionally mirrors the data shape of
    ``jarvis.tools.dynamic.web_actions``. To merge, add each site's action
    dict from ``WEB_EXTRA`` into that module's ``SITES`` dict, e.g.::

        from jarvis.tools.dynamic.web_extra import WEB_EXTRA
        SITES.update(WEB_EXTRA)          # no key collisions: verified by tests
        PAIR_COUNT = sum(len(a) for a in SITES.values())

    or, equivalently, the registry's WebActionsProvider can be given a
    second source dict. ``WEB_EXTRA`` is deliberately handler-free data so
    the existing ``_make_handler`` machinery applies unchanged.

CONVENTIONS (same as web_actions.py):
    * site key -> {action -> absolute https:// URL template}
    * every template carries a {q} placeholder (quote_plus-encoded at call
      time); a few entries use a static landing page instead.
    * patterns the author is not fully sure about are marked
      ``# UNVERIFIED PATTERN`` and counted honestly below.
"""

# ---------------------------------------------------------------------------
# video
# ---------------------------------------------------------------------------
WEB_EXTRA: dict[str, dict[str, str]] = {
    "rumble": {
        "search": "https://rumble.com/search/video?q={q}",
    },
    "odysee": {
        "search": "https://odysee.com/$/search?q={q}",
    },
    "peertube": {
        "search": "https://search.joinpeertube.org/search?q={q}",
    },
    "bilibili": {
        "search": "https://search.bilibili.com/all?keyword={q}",
    },
    "niconico": {
        "search": "https://www.nicovideo.jp/search/{q}",
    },
    "rutube": {
        "search": "https://rutube.ru/search/?query={q}",
    },
    "viki": {
        # UNVERIFIED PATTERN
        "search": "https://www.viki.com/search?q={q}",
    },
    "tubitv": {
        "search": "https://tubitv.com/search/{q}",
    },
    "plex": {
        # UNVERIFIED PATTERN
        "search": "https://watch.plex.tv/search?q={q}",
    },
    "mxplayer": {
        # UNVERIFIED PATTERN
        "search": "https://www.mxplayer.in/search?q={q}",
    },
    "zee5": {
        "search": "https://www.zee5.com/search?q={q}",
    },
    "sonyliv": {
        "search": "https://www.sonyliv.com/search?q={q}",
    },
    "hulu": {
        # UNVERIFIED PATTERN
        "search": "https://www.hulu.com/search?q={q}",
    },
    "primevideo": {
        # UNVERIFIED PATTERN
        "search": "https://www.primevideo.com/search?phrase={q}",
    },
    "jiohotstar": {
        # UNVERIFIED PATTERN
        "search": "https://www.jiohotstar.com/search?q={q}",
    },
    "kick": {
        # UNVERIFIED PATTERN
        "search": "https://kick.com/search?q={q}",
    },

    # -------------------------------------------------------------------
    # music
    # -------------------------------------------------------------------
    "pandora": {
        "search": "https://www.pandora.com/search/{q}/all",
    },
    "tidal": {
        # UNVERIFIED PATTERN
        "search": "https://tidal.com/search?q={q}",
    },
    "amazonmusic": {
        "search": "https://music.amazon.com/search/{q}",
    },
    "iheart": {
        # UNVERIFIED PATTERN
        "search": "https://www.iheart.com/search/?query={q}",
    },
    "jiosaavn": {
        # UNVERIFIED PATTERN
        "search": "https://www.jiosaavn.com/search?q={q}",
    },
    "gaana": {
        # UNVERIFIED PATTERN
        "search": "https://gaana.com/search/{q}",
    },
    "musixmatch": {
        "search": "https://www.musixmatch.com/search/{q}",
    },
    "azlyrics": {
        "search": "https://search.azlyrics.com/search.php?q={q}",
    },
    "lyricscom": {
        "search": "https://www.lyrics.com/lyrics/{q}",
    },
    "ultimateguitar": {
        "search": "https://www.ultimate-guitar.com/search.php?search_type=title&value={q}",
    },
    "chordify": {
        "search": "https://chordify.net/search/{q}",
    },
    "audiomack": {
        "search": "https://audiomack.com/search?q={q}",
    },
    "audius": {
        "search": "https://audius.co/search/{q}",
    },
    "tunein": {
        "search": "https://tunein.com/search/?query={q}",
    },
    "listennotes": {
        "search": "https://www.listennotes.com/search/?q={q}",
    },
    "podchaser": {
        "search": "https://www.podchaser.com/search?q={q}",
    },
    "playerfm": {
        "search": "https://player.fm/search/{q}",
    },

    # -------------------------------------------------------------------
    # shopping
    # -------------------------------------------------------------------
    "costco": {
        "search": "https://www.costco.com/CatalogSearch?keyword={q}",
    },
    "ikea": {
        "search": "https://www.ikea.com/us/en/search/products/?q={q}",
    },
    "sephora": {
        "search": "https://www.sephora.com/search?keyword={q}",
    },
    "ulta": {
        "search": "https://www.ulta.com/search?q={q}",
    },
    "shein": {
        "search": "https://www.shein.com/pdsearch/{q}",
    },
    "temu": {
        "search": "https://www.temu.com/search_result.html?search_key={q}",
    },
    "zalando": {
        "search": "https://www.zalando.co.uk/catalog/?q={q}",
    },
    "asos": {
        "search": "https://www.asos.com/us/search/?q={q}",
    },
    "hm": {
        "search": "https://www2.hm.com/en_us/search-results.html?query={q}",
    },
    "zara": {
        # UNVERIFIED PATTERN
        "search": "https://www.zara.com/us/en/search?searchTerm={q}",
    },
    "uniqlo": {
        "search": "https://www.uniqlo.com/us/en/search?q={q}",
    },
    "meesho": {
        "search": "https://www.meesho.com/search?q={q}",
    },
    "snapdeal": {
        "search": "https://www.snapdeal.com/search?keyword={q}",
    },
    "facebookmarketplace": {
        "search": "https://www.facebook.com/marketplace/search/?query={q}",
    },
    "swappa": {
        "search": "https://swappa.com/search?q={q}",
    },
    "microcenter": {
        "search": "https://www.microcenter.com/search/search_results.aspx?Ntt={q}",
    },
    "adorama": {
        "search": "https://www.adorama.com/l/?searchinfo={q}",
    },
    "alibaba": {
        "search": "https://www.alibaba.com/trade/search?searchText={q}",
    },
    "dhgate": {
        "search": "https://www.dhgate.com/wholesale/search.do?searchkey={q}",
    },
    "chewy": {
        "search": "https://www.chewy.com/s?query={q}",
    },
    "sweetwater": {
        "search": "https://www.sweetwater.com/store/search.php?s={q}",
    },
    "rei": {
        "search": "https://www.rei.com/search?q={q}",
    },
    "fanatics": {
        "search": "https://www.fanatics.com/search?q={q}",
    },
    "lululemon": {
        "search": "https://shop.lululemon.com/c/search?q={q}",
    },
    "patagonia": {
        "search": "https://www.patagonia.com/search/?q={q}",
    },
    "instacart": {
        "search": "https://www.instacart.com/store/s?k={q}",
    },
    "wholefoods": {
        "search": "https://www.wholefoodsmarket.com/search?text={q}",
    },
    "lego": {
        "search": "https://www.lego.com/en-us/search?q={q}",
    },
    "gamestop": {
        "search": "https://www.gamestop.com/search/?q={q}",
    },
    "barnesandnoble": {
        "search": "https://www.barnesandnoble.com/s/{q}",
    },
    "abebooks": {
        "search": "https://www.abebooks.com/servlet/SearchResults?kn={q}",
    },
    "waterstones": {
        "search": "https://www.waterstones.com/books/search/term/{q}",
    },
    "officedepot": {
        "search": "https://www.officedepot.com/catalog/search/sku?Ntt={q}",
    },
    "totalwine": {
        "search": "https://www.totalwine.com/search/all?text={q}",
    },
    "cvs": {
        "search": "https://www.cvs.com/search?searchTerm={q}",
    },
    "walgreens": {
        "search": "https://www.walgreens.com/search/results.jsp?Ntt={q}",
    },
    "marksandspencer": {
        "search": "https://www.marksandspencer.com/search?searchTerm={q}",
    },
    "johnlewis": {
        "search": "https://www.johnlewis.com/search?search-term={q}",
    },
    "argos": {
        "search": "https://www.argos.co.uk/search/{q}/",
    },
    "currys": {
        "search": "https://www.currys.co.uk/search?q={q}",
    },
    "boots": {
        "search": "https://www.boots.com/search?text={q}",
    },
    "superdrug": {
        "search": "https://www.superdrug.com/search?q={q}",
    },
    "lookfantastic": {
        "search": "https://www.lookfantastic.com/search?q={q}",
    },
    "harrods": {
        "search": "https://www.harrods.com/en-us/search?query={q}",
    },
    "selfridges": {
        "search": "https://www.selfridges.com/US/en/search/?text={q}",
    },
    "netaporter": {
        "search": "https://www.net-a-porter.com/en-us/shop/search?keywords={q}",
    },
    "ssense": {
        "search": "https://www.ssense.com/search?q={q}",
    },
    "revolve": {
        "search": "https://www.revolve.com/search/?searchTerm={q}",
    },
    "saks": {
        "search": "https://www.saksfifthavenue.com/search?SearchTerm={q}",
    },
    "neimanmarcus": {
        "search": "https://www.neimanmarcus.com/search.jsp?searchText={q}",
    },
    "bloomingdales": {
        "search": "https://www.bloomingdales.com/shop/search?keyword={q}",
    },
    "qvc": {
        "search": "https://www.qvc.com/catalog/search.html?keyword={q}",
    },
    "hsn": {
        "search": "https://www.hsn.com/search?query={q}",
    },
    "allegro": {
        "search": "https://allegro.pl/listing?string={q}",
    },
    "olx": {
        "search": "https://www.olx.in/items/q-{q}",
    },
    # -------------------------------------------------------------------
    # news
    # -------------------------------------------------------------------
    "reuters": {
        "search": "https://www.reuters.com/search/news?blob={q}",
    },
    "apnews": {
        "search": "https://apnews.com/search?q={q}",
    },
    "aljazeera": {
        "search": "https://www.aljazeera.com/search/{q}",
    },
    "dw": {
        "search": "https://www.dw.com/search/?languageCode=en&item={q}",
    },
    "euronews": {
        "search": "https://www.euronews.com/search?query={q}",
    },
    "abcnews": {
        "search": "https://abcnews.go.com/search?searchtext={q}",
    },
    "nbcnews": {
        "search": "https://www.nbcnews.com/search/?q={q}",
    },
    "cbsnews": {
        "search": "https://www.cbsnews.com/search/?q={q}",
    },
    "usatoday": {
        "search": "https://www.usatoday.com/search/?q={q}",
    },
    "politico": {
        "search": "https://www.politico.com/search?q={q}",
    },
    "thehill": {
        "search": "https://thehill.com/search/?s={q}",
    },
    "axios": {
        "search": "https://www.axios.com/search?q={q}",
    },
    "motherjones": {
        "search": "https://www.motherjones.com/search/?q={q}",
    },
    "thenation": {
        "search": "https://www.thenation.com/search/?s={q}",
    },
    "nationalreview": {
        "search": "https://www.nationalreview.com/search/?q={q}",
    },
    "breitbart": {
        "search": "https://www.breitbart.com/search/?q={q}",
    },
    "newsmax": {
        "search": "https://www.newsmax.com/search/?q={q}",
    },
    "nypost": {
        "search": "https://nypost.com/search/{q}/",
    },
    "seattletimes": {
        "search": "https://www.seattletimes.com/search/?q={q}",
    },
    "standard": {
        "search": "https://www.standard.co.uk/search?q={q}",
    },
    "metro": {
        "search": "https://metro.co.uk/?s={q}",
    },
    "mirror": {
        "search": "https://www.mirror.co.uk/search/?q={q}",
    },
    "thesun": {
        "search": "https://www.thesun.co.uk/?s={q}",
    },
    "spiegel": {
        "search": "https://www.spiegel.de/suche/?suchbegriff={q}",
    },
    "smh": {
        "search": "https://www.smh.com.au/search?query={q}",
    },
    "abcau": {
        "search": "https://www.abc.net.au/search?query={q}",
    },
    "straitstimes": {
        "search": "https://www.straitstimes.com/search?searchTerm={q}",
    },
    "japantimes": {
        "search": "https://www.japantimes.co.jp/?s={q}",
    },
    "timesofindia": {
        "search": "https://timesofindia.indiatimes.com/topic/{q}",
    },
    "indiatoday": {
        "search": "https://www.indiatoday.in/search.jsp?searchword={q}",
    },

    # -------------------------------------------------------------------
    # docs / reference
    # -------------------------------------------------------------------
    "cambridgedict": {
        "define": "https://dictionary.cambridge.org/dictionary/english/{q}",
    },
    "collinsdict": {
        "define": "https://www.collinsdictionary.com/dictionary/english/{q}",
    },
    "oxfordlearners": {
        "define": "https://www.oxfordlearnersdictionaries.com/definition/english/{q}",
    },
    "wordreference": {
        "define": "https://www.wordreference.com/definition/{q}",
    },
    "linguee": {
        "search": "https://www.linguee.com/english-german/search?source=auto&query={q}",
    },
    "reversocontext": {
        "translate": "https://context.reverso.net/translation/english-french/{q}",
    },
    "deepl": {
        "translate": "https://www.deepl.com/translator#en/fr/{q}",
    },
    "bingtranslator": {
        "translate": "https://www.bing.com/translator?text={q}",
    },
    "wikidata": {
        "search": "https://www.wikidata.org/w/index.php?search={q}",
    },
    "worldcat": {
        "search": "https://search.worldcat.org/search?q={q}",
    },
    "loc": {
        "search": "https://www.loc.gov/search/?q={q}",
    },
    "gutenberg": {
        "search": "https://www.gutenberg.org/ebooks/search/?query={q}",
    },
    "jstor": {
        "search": "https://www.jstor.org/action/doBasicSearch?Query={q}",
    },
    "core": {
        "search": "https://core.ac.uk/search?q={q}",
    },
    "basesearch": {
        "search": "https://www.base-search.net/Search/Results?lookfor={q}",
    },
    "researchgate": {
        "search": "https://www.researchgate.net/search/publication?q={q}",
    },
    "academiaedu": {
        "search": "https://www.academia.edu/search?q={q}",
    },
    "philpapers": {
        "search": "https://philpapers.org/s/{q}",
    },
    "sep": {
        "search": "https://plato.stanford.edu/search/searcher.py?query={q}",
    },
    "encyclopediacom": {
        "search": "https://www.encyclopedia.com/search?Ntt={q}",
    },
    "ciafactbook": {
        "country": "https://www.cia.gov/the-world-factbook/countries/{q}/",
    },
    "worldbankdata": {
        "search": "https://data.worldbank.org/search?q={q}",
    },
    "datagov": {
        "search": "https://catalog.data.gov/dataset?q={q}",
    },
    "ourworldindata": {
        "search": "https://ourworldindata.org/search?q={q}",
    },
    "statista": {
        "search": "https://www.statista.com/search/?q={q}",
    },
    "sparknotes": {
        "search": "https://www.sparknotes.com/search?q={q}",
    },
    "litcharts": {
        "search": "https://www.litcharts.com/search?query={q}",
    },
    "poetryfoundation": {
        "search": "https://www.poetryfoundation.org/search?query={q}",
    },
    "pubchem": {
        "search": "https://pubchem.ncbi.nlm.nih.gov/#query={q}",
    },
    "oeis": {
        "search": "https://oeis.org/search?q={q}",
    },
    "timeanddate": {
        "search": "https://www.timeanddate.com/search/results.html?query={q}",
    },
    # -------------------------------------------------------------------
    # dev
    # -------------------------------------------------------------------
    "bitbucket": {
        # UNVERIFIED PATTERN
        "search": "https://bitbucket.org/search?q={q}",
    },
    "codeberg": {
        "search": "https://codeberg.org/explore/repos?q={q}",
    },
    "launchpad": {
        "search": "https://launchpad.net/+search?field.text={q}",
    },
    "sourceforge": {
        "search": "https://sourceforge.net/directory/?q={q}",
    },
    "freecodecamp": {
        "search": "https://www.freecodecamp.org/news/search/?query={q}",
    },
    "geeksforgeeks": {
        "search": "https://www.geeksforgeeks.org/?s={q}",
    },
    "devdocs": {
        "search": "https://devdocs.io/#q={q}",
    },
    "webdev": {
        "search": "https://web.dev/search/?query={q}",
    },
    "csstricks": {
        "search": "https://css-tricks.com/?s={q}",
    },
    "smashingmagazine": {
        "search": "https://www.smashingmagazine.com/search/?q={q}",
    },
    "alistapart": {
        "search": "https://alistapart.com/?s={q}",
    },
    "sitepoint": {
        "search": "https://www.sitepoint.com/?s={q}",
    },
    "digitaloceandocs": {
        # UNVERIFIED PATTERN
        "search": "https://docs.digitalocean.com/search/?q={q}",
    },
    "microsoftlearn": {
        "search": "https://learn.microsoft.com/en-us/search/?terms={q}",
    },
    "gcpdocs": {
        "search": "https://cloud.google.com/s/results?q={q}",
    },
    "kubedocs": {
        "search": "https://kubernetes.io/search/?q={q}",
    },
    "djangodocs": {
        "search": "https://docs.djangoproject.com/en/stable/search/?q={q}",
    },
    "flaskdocs": {
        "search": "https://flask.palletsprojects.com/en/stable/search/?q={q}",
    },
    "scikitlearn": {
        "search": "https://scikit-learn.org/stable/search.html?q={q}",
    },
    "pandasdocs": {
        "search": "https://pandas.pydata.org/docs/search.html?q={q}",
    },
    "numpydocs": {
        "search": "https://numpy.org/doc/stable/search.html?q={q}",
    },
    "matplotlibdocs": {
        "search": "https://matplotlib.org/stable/search.html?q={q}",
    },
    "rdocumentation": {
        "search": "https://www.rdocumentation.org/search?q={q}",
    },
    "julia": {
        "search": "https://docs.julialang.org/en/v1/search/?q={q}",
    },
    "hoogle": {
        "search": "https://hoogle.haskell.org/?hoogle={q}",
    },
    "clojuredocs": {
        "search": "https://clojuredocs.org/search?query={q}",
    },
    "perldoc": {
        "search": "https://perldoc.perl.org/search?q={q}",
    },
    "mysqldocs": {
        "search": "https://dev.mysql.com/doc/search/?q={q}",
    },
    "mariadbdocs": {
        "search": "https://mariadb.com/kb/en/search/?q={q}",
    },
    "elasticdocs": {
        "search": "https://www.elastic.co/search?q={q}",
    },
    "herokudocs": {
        "search": "https://devcenter.heroku.com/search?q={q}",
    },
    "cloudflaredocs": {
        # UNVERIFIED PATTERN
        "search": "https://developers.cloudflare.com/search/?q={q}",
    },
    "ansibledocs": {
        "search": "https://docs.ansible.com/search.html?q={q}",
    },
    "httpstatuses": {
        "code": "https://httpstatuses.com/{q}",
    },
    "rustdocs": {
        "search": "https://doc.rust-lang.org/std/?search={q}",
    },
    "elixirdocs": {
        "search": "https://hexdocs.pm/elixir/search.html?q={q}",
    },

    # -------------------------------------------------------------------
    # travel
    # -------------------------------------------------------------------
    "kayak": {
        # UNVERIFIED PATTERN
        "search": "https://www.kayak.com/flights/{q}",
    },
    "skyscanner": {
        # UNVERIFIED PATTERN
        "search": "https://www.skyscanner.co.in/transport/flights/{q}/",
    },
    "googleflights": {
        "flights": "https://www.google.com/travel/flights",
    },
    "momondo": {
        # UNVERIFIED PATTERN
        "search": "https://www.momondo.com/flight-search/{q}",
    },
    "hostelworld": {
        # UNVERIFIED PATTERN
        "search": "https://www.hostelworld.com/s?search_keywords={q}",
    },
    "vrbo": {
        "search": "https://www.vrbo.com/search?destination={q}",
    },
    "agoda": {
        # UNVERIFIED PATTERN
        "search": "https://www.agoda.com/search?city={q}",
    },
    "hotels": {
        "search": "https://www.hotels.com/search?destination={q}",
    },
    "rome2rio": {
        "search": "https://www.rome2rio.com/s/{q}",
    },
    "viator": {
        "search": "https://www.viator.com/searchResults/all?text={q}",
    },
    "getyourguide": {
        # UNVERIFIED PATTERN
        "search": "https://www.getyourguide.com/s/?q={q}",
    },
    "lonelyplanet": {
        "search": "https://www.lonelyplanet.com/search?query={q}",
    },
    "fodors": {
        "search": "https://www.fodors.com/search?q={q}",
    },
    "atlasobscura": {
        "search": "https://www.atlasobscura.com/search?q={q}",
    },
    "wikivoyage": {
        "search": "https://en.wikivoyage.org/wiki/Special:Search?search={q}",
    },
    "googlehotels": {
        "hotels": "https://www.google.com/travel/hotels",
    },

    # -------------------------------------------------------------------
    # food
    # -------------------------------------------------------------------
    "zomato": {
        # UNVERIFIED PATTERN
        "search": "https://www.zomato.com/search?q={q}",
    },
    "swiggy": {
        # UNVERIFIED PATTERN
        "search": "https://www.swiggy.com/search?query={q}",
    },
    "ubereats": {
        # UNVERIFIED PATTERN
        "search": "https://www.ubereats.com/search?q={q}",
    },
    "grubhub": {
        # UNVERIFIED PATTERN
        "search": "https://www.grubhub.com/search?query={q}",
    },
    "deliveroo": {
        # UNVERIFIED PATTERN
        "search": "https://deliveroo.co.uk/search?q={q}",
    },
    "opentable": {
        # UNVERIFIED PATTERN
        "search": "https://www.opentable.com/s?term={q}",
    },
    "resy": {
        # UNVERIFIED PATTERN
        "search": "https://resy.com/cities/{q}",
    },
    "michelinguide": {
        # UNVERIFIED PATTERN
        "search": "https://guide.michelin.com/en/search?q={q}",
    },
    "eater": {
        "search": "https://www.eater.com/search?q={q}",
    },
    "seriouseats": {
        "search": "https://www.seriouseats.com/search?q={q}",
    },
    "bonappetit": {
        "search": "https://www.bonappetit.com/search?query={q}",
    },
    "epicurious": {
        "search": "https://www.epicurious.com/search/{q}",
    },
    "foodnetwork": {
        "search": "https://www.foodnetwork.com/search/{q}-",
    },
    "bbcgoodfood": {
        "search": "https://www.bbcgoodfood.com/search?q={q}",
    },
    "jamieoliver": {
        "search": "https://www.jamieoliver.com/search/?s={q}",
    },
    "delish": {
        "search": "https://www.delish.com/search/?q={q}",
    },
    "thekitchn": {
        "search": "https://www.thekitchn.com/search?query={q}",
    },
    "simplyrecipes": {
        "search": "https://www.simplyrecipes.com/search?q={q}",
    },
    "budgetbytes": {
        "search": "https://www.budgetbytes.com/?s={q}",
    },
    "recipetineats": {
        "search": "https://www.recipetineats.com/?s={q}",
    },
    "skinnytaste": {
        "search": "https://www.skinnytaste.com/?s={q}",
    },
    "minimalistbaker": {
        "search": "https://minimalistbaker.com/?s={q}",
    },
    "hebbarskitchen": {
        "search": "https://www.hebbarskitchen.com/?s={q}",
    },
    "tarladalal": {
        # UNVERIFIED PATTERN
        "search": "https://www.tarladalal.com/search-recipe?search={q}",
    },
    "indianhealthyrecipes": {
        "search": "https://www.indianhealthyrecipes.com/?s={q}",
    },
    "untappd": {
        "search": "https://untappd.com/search?q={q}",
    },
    "vivino": {
        "search": "https://www.vivino.com/search/wines?q={q}",
    },

    # -------------------------------------------------------------------
    # maps
    # -------------------------------------------------------------------
    "applemaps": {
        "search": "https://maps.apple.com/?q={q}",
    },
    "mapquest": {
        "search": "https://www.mapquest.com/search/results?query={q}",
    },
    "here": {
        "search": "https://wego.here.com/search/{q}",
    },
    "geonames": {
        "search": "https://www.geonames.org/search.html?q={q}",
    },
    "alltrails": {
        # UNVERIFIED PATTERN
        "search": "https://www.alltrails.com/explore?location={q}",
    },
    "waze": {
        # UNVERIFIED PATTERN
        "search": "https://www.waze.com/live-map/directions?to={q}",
    },
    # -------------------------------------------------------------------
    # social
    # -------------------------------------------------------------------
    "threads": {
        # UNVERIFIED PATTERN
        "search": "https://www.threads.com/search?q={q}",
    },
    "mastodon": {
        "search": "https://mastodon.social/search?q={q}",
    },
    "nextdoor": {
        # UNVERIFIED PATTERN
        "search": "https://nextdoor.com/search/?query={q}",
    },
    "meetup": {
        "search": "https://www.meetup.com/find/?keywords={q}",
    },
    "eventbrite": {
        "search": "https://www.eventbrite.com/d/online/{q}/",
    },
    "rateyourmusic": {
        # UNVERIFIED PATTERN
        "search": "https://rateyourmusic.com/search?searchterm={q}&searchtype=l",
    },
    "discogs": {
        "search": "https://www.discogs.com/search/?q={q}",
    },
    "musicbrainz": {
        "search": "https://musicbrainz.org/search?query={q}&type=artist",
    },
    "fandom": {
        # UNVERIFIED PATTERN
        "search": "https://www.fandom.com/search?q={q}",
    },
    "ao3": {
        "search": "https://archiveofourown.org/works/search?work_search%5Bquery%5D={q}",
    },
    "tapas": {
        # UNVERIFIED PATTERN
        "search": "https://tapas.io/search?q={q}",
    },
    "webtoon": {
        # UNVERIFIED PATTERN
        "search": "https://www.webtoons.com/en/search?keyword={q}",
    },
    "imgur": {
        # UNVERIFIED PATTERN
        "search": "https://imgur.com/search?q={q}",
    },
    "9gag": {
        # UNVERIFIED PATTERN
        "search": "https://9gag.com/search?query={q}",
    },
    "behance": {
        "search": "https://www.behance.net/search?search={q}",
    },
    "500px": {
        # UNVERIFIED PATTERN
        "search": "https://500px.com/search?q={q}",
    },
    "substack": {
        # UNVERIFIED PATTERN
        "search": "https://substack.com/search/publication?q={q}",
    },
    "hashnode": {
        # UNVERIFIED PATTERN
        "search": "https://hashnode.com/search?q={q}",
    },
    "lobsters": {
        "search": "https://lobste.rs/search?q={q}",
    },
    "slashdot": {
        # UNVERIFIED PATTERN
        "search": "https://slashdot.org/index2.pl?fhfilter={q}",
    },
    "digg": {
        # UNVERIFIED PATTERN
        "search": "https://digg.com/search?q={q}",
    },
    "flipboard": {
        # UNVERIFIED PATTERN
        "search": "https://flipboard.com/search/{q}",
    },
    "truthsocial": {
        # UNVERIFIED PATTERN
        "search": "https://truthsocial.com/search?q={q}",
    },
    "patreon": {
        "search": "https://www.patreon.com/search?q={q}",
    },
    "cameo": {
        "search": "https://www.cameo.com/search?q={q}",
    },

    # -------------------------------------------------------------------
    # finance
    # -------------------------------------------------------------------
    "morningstar": {
        "search": "https://www.morningstar.com/search?query={q}",
    },
    "fool": {
        "search": "https://www.fool.com/search/?q={q}",
    },
    "seekingalpha": {
        # UNVERIFIED PATTERN
        "search": "https://seekingalpha.com/search?query={q}",
    },
    "stocktwits": {
        "search": "https://stocktwits.com/search?q={q}",
    },
    "coinmarketcap": {
        "currency": "https://coinmarketcap.com/currencies/{q}/",
    },
    "coingecko": {
        "search": "https://www.coingecko.com/en/search?query={q}",
    },
    "coinbase": {
        "price": "https://www.coinbase.com/price/{q}",
    },
    "etherscan": {
        "search": "https://etherscan.io/search?f=0&q={q}",
    },
    "bankrate": {
        "search": "https://www.bankrate.com/search/?q={q}",
    },
    "nerdwallet": {
        # UNVERIFIED PATTERN
        "search": "https://www.nerdwallet.com/search?query={q}",
    },
    "investopedia": {
        "search": "https://www.investopedia.com/search?q={q}",
    },
    "kiplinger": {
        # UNVERIFIED PATTERN
        "search": "https://www.kiplinger.com/search?q={q}",
    },
    "screener": {
        "company": "https://www.screener.in/company/{q}/",
    },
    "stockanalysis": {
        "quote": "https://stockanalysis.com/stocks/{q}/",
    },
    "investorplace": {
        "search": "https://investorplace.com/?s={q}",
    },
    "fred": {
        "search": "https://fred.stlouisfed.org/search?st={q}",
    },
    "xe": {
        "convert": "https://www.xe.com/currencyconverter/",
    },
    "secedgar": {
        "search": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={q}",
    },
    "companieshouse": {
        "search": "https://find-and-update.company-information.service.gov.uk/search?q={q}",
    },
    "opencorporates": {
        "search": "https://opencorporates.com/search?q={q}",
    },

    # -------------------------------------------------------------------
    # education
    # -------------------------------------------------------------------
    "khanacademy": {
        # UNVERIFIED PATTERN
        "search": "https://www.khanacademy.org/search?page_search_query={q}",
    },
    "edx": {
        "search": "https://www.edx.org/search?q={q}",
    },
    "udacity": {
        # UNVERIFIED PATTERN
        "search": "https://www.udacity.com/courses/all?search={q}",
    },
    "masterclass": {
        # UNVERIFIED PATTERN
        "search": "https://www.masterclass.com/search?q={q}",
    },
    "datacamp": {
        # UNVERIFIED PATTERN
        "search": "https://www.datacamp.com/search?query={q}",
    },
    "codecademy": {
        # UNVERIFIED PATTERN
        "search": "https://www.codecademy.com/search?query={q}",
    },
    "pluralsight": {
        "search": "https://www.pluralsight.com/search?q={q}",
    },
    "linkedinlearning": {
        "search": "https://www.linkedin.com/learning/search?keywords={q}",
    },
    "brilliant": {
        # UNVERIFIED PATTERN
        "search": "https://brilliant.org/search/?q={q}",
    },
    "quizlet": {
        "search": "https://quizlet.com/search?query={q}",
    },
    "scribd": {
        "search": "https://www.scribd.com/search?query={q}",
    },
    "slideshare": {
        # UNVERIFIED PATTERN
        "search": "https://www.slideshare.net/search/slideshow?searchfrom=header&q={q}",
    },
    "mitocw": {
        "search": "https://ocw.mit.edu/search/?q={q}",
    },
    "futurelearn": {
        "search": "https://www.futurelearn.com/search?q={q}",
    },
    "alison": {
        # UNVERIFIED PATTERN
        "search": "https://alison.com/search?query={q}",
    },
    "classcentral": {
        "search": "https://www.classcentral.com/search?q={q}",
    },
    "leetcode": {
        # UNVERIFIED PATTERN
        "search": "https://leetcode.com/problemset/all/?search={q}",
    },
    "codeforces": {
        # UNVERIFIED PATTERN
        "search": "https://codeforces.com/search?query={q}",
    },
    "codechef": {
        # UNVERIFIED PATTERN
        "search": "https://www.codechef.com/search?query={q}",
    },
    "wyzant": {
        # UNVERIFIED PATTERN
        "search": "https://www.wyzant.com/search?q={q}",
    },

    # -------------------------------------------------------------------
    # jobs
    # -------------------------------------------------------------------
    "glassdoor": {
        "search": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}",
    },
    "wellfound": {
        # UNVERIFIED PATTERN
        "search": "https://wellfound.com/jobs?search={q}",
    },
    "workatastartup": {
        "search": "https://www.workatastartup.com/jobs?q={q}",
    },
    "weworkremotely": {
        "search": "https://weworkremotely.com/remote-jobs/search?term={q}",
    },
    "remoteok": {
        # UNVERIFIED PATTERN
        "search": "https://remoteok.com/remote-jobs/search/{q}",
    },
    "simplyhired": {
        "search": "https://www.simplyhired.com/search?q={q}",
    },
    "careerbuilder": {
        "search": "https://www.careerbuilder.com/jobs?keywords={q}",
    },
    "usajobs": {
        "search": "https://www.usajobs.gov/Search/Results?k={q}",
    },
    "builtin": {
        "search": "https://builtin.com/jobs?search={q}",
    },
    "naukri": {
        "search": "https://www.naukri.com/{q}-jobs",
    },
    "totaljobs": {
        "search": "https://www.totaljobs.com/jobs/{q}",
    },
    "reed": {
        "search": "https://www.reed.co.uk/jobs/{q}-jobs",
    },
    "seek": {
        "search": "https://www.seek.com.au/{q}-jobs",
    },
    "adzuna": {
        "search": "https://www.adzuna.com/search?q={q}",
    },
}

#: Total site x action pairs in this expansion module.
PAIR_COUNT = sum(len(actions) for actions in WEB_EXTRA.values())
