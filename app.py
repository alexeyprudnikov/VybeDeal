"""⚡ VybeDeal – Kleinanzeigen Deal Finder (Streamlit UI).

Pipeline: scrape → enrich → score (Gemini oder Heuristik) → vergleichen & handeln.
Alles Teure ist gecacht, alles Dauerhafte liegt in ``data/``.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import streamlit as st

from dealfinder import export, scraper, store
from dealfinder.ai import (
    MODEL_DEFAULT,
    AIUnavailable,
    analyze,
    get_api_key,
    make_client,
    merge_deals,
    resolve_key,
)
from dealfinder.models import Deal, Listing, SearchParams
from dealfinder.pricing import fmt_eur, score_badge
from dealfinder.scoring import score_cohort

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

st.set_page_config(
    page_title="VybeDeal – AI Deal Finder",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.4rem;}
    [data-testid="stMetric"] {
        background: rgba(128,128,128,.08);
        border: 1px solid rgba(128,128,128,.22);
        border-radius: 12px; padding: 10px 14px;
    }
    .vd-tags {margin: -6px 0 8px;}
    .vd-tag {display:inline-block; padding:2px 9px; border-radius:999px;
        font-size:.78rem; margin-right:6px; background:rgba(128,128,128,.16);}
    .vd-tag-good {background:rgba(34,160,90,.20);}
    .vd-tag-bad {background:rgba(215,70,70,.20);}
    </style>
    """,
    unsafe_allow_html=True,
)

SORT_OPTIONS = {
    "Score (beste zuerst)": "score",
    "Marge (€)": "margin",
    "ROI (%)": "roi",
    "Preis aufsteigend": "price_asc",
    "Preis absteigend": "price_desc",
    "Reihenfolge der Anzeige": "listing",
}
SERVER_SORTS = ["Reihenfolge der Anzeige", "Neueste zuerst", "Preis aufsteigend", "Preis absteigend"]
SERVER_SLUG = {
    "Reihenfolge der Anzeige": "relevance",
    "Neueste zuerst": "newest",
    "Preis aufsteigend": "price_asc",
    "Preis absteigend": "price_desc",
}

# --------------------------------------------------------------------------- #
# Cached pipeline steps
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=600, show_spinner=False)
def run_search(cache_key: str, params_json: str) -> dict:
    """cache_key is just a short readable fingerprint of the search."""
    params = SearchParams(**json.loads(params_json))
    result = scraper.search(params, enrich=True, workers=3)
    return {
        "listings": [l.to_dict() for l in result.listings],
        "total": result.total_results,
        "pages": result.pages_fetched,
        "urls": result.urls,
        "error": result.error,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def run_ai_batch(batch_json: str, query: str, model: str) -> list[dict]:
    """Ein Gemini-Batch – pro Batch gecacht, Re-Runs zahlen nur neue Inserate."""
    listings = [Listing(**d) for d in json.loads(batch_json)]
    client = make_client(get_api_key(st.secrets))
    verdicts = analyze(listings, query=query, api_key="", model=model, client=client)
    return list(verdicts.values())


# --------------------------------------------------------------------------- #
# Sidebar: search definition
# --------------------------------------------------------------------------- #
try:
    api_key, key_source, key_stale = resolve_key(st.secrets)
except AIUnavailable as exc:          # mehrere Quellen → nicht raten, sondern melden
    api_key, key_source, key_stale = "", "", str(exc)
    st.error(f"🔑 **Key-Konflikt:** {exc}")
W = {  # widget keys, also the persistence keys for saved searches
    "query": "w_query", "min": "w_min", "max": "w_max", "plz": "w_plz",
    "radius": "w_radius", "results": "w_results", "sort": "w_sort",
    "pro": "w_pro", "wanted": "w_wanted", "private": "w_private",
    "excl": "w_excl",
}
DEFAULTS = {
    W["query"]: "iPhone 13", W["min"]: 0, W["max"]: 450, W["plz"]: "",
    W["radius"]: 0, W["results"]: 30, W["sort"]: SERVER_SORTS[0],
    W["pro"]: True, W["wanted"]: True, W["private"]: False,
    W["excl"]: "Reparatur, Hülle, Case, Display",
}

# apply a saved search before the widgets are created
if "preset" in st.session_state:
    preset = st.session_state.pop("preset")
    mapping = {
        "query": W["query"], "min_price": W["min"], "max_price": W["max"],
        "plz": W["plz"], "radius_km": W["radius"], "max_results": W["results"],
        "exclude_pro": W["pro"], "hide_wanted": W["wanted"],
        "private_only": W["private"],
    }
    for field, key in mapping.items():
        if field in preset and preset[field] is not None:
            st.session_state[key] = preset[field]
    if preset.get("sort"):
        inverse = {v: k for k, v in SERVER_SLUG.items()}
        st.session_state[W["sort"]] = inverse.get(preset["sort"], SERVER_SORTS[0])
    if preset.get("exclude_keywords") is not None:
        st.session_state[W["excl"]] = ", ".join(preset["exclude_keywords"])

with st.sidebar:
    st.markdown("## ⚡ VybeDeal")
    st.caption("Kleinanzeigen durchsuchen, Preise vergleichen, Deals bewerten lassen.")

    with st.form("search_form"):
        query = st.text_input("Suchbegriff", key=W["query"], help="'leica m6', 'cannondale rennrad', 'ps5 slim' …")
        c1, c2 = st.columns(2)
        with c1:
            min_price = st.number_input("ab (€)", key=W["min"], min_value=0, max_value=200000, step=10, help="0 = ohne Untergrenze")
        with c2:
            max_price = st.number_input("bis (€)", key=W["max"], min_value=0, max_value=200000, step=10, help="0 = ohne Obergrenze")

        c3, c4 = st.columns(2)
        with c3:
            plz = st.text_input("PLZ", key=W["plz"], max_chars=5, help="Leer = ganz Deutschland")
        with c4:
            radius = st.select_slider("Umkreis", key=W["radius"], options=[0, 5, 10, 20, 30, 50, 75, 100], format_func=lambda v: "ganz DE" if v == 0 else f"{v} km")

        max_results = st.slider("Inserate laden", key=W["results"], min_value=5, max_value=100, step=5, help="Mehr Inserate = bessere Marktbasis, aber langsamer")
        sort_label = st.selectbox("Vorsortierung (Server)", SERVER_SORTS, key=W["sort"])

        with st.expander("Filter & Feinschliff"):
            exclude_pro = st.checkbox("Gewerbliche ausblenden", key=W["pro"])
            hide_wanted = st.checkbox("Kauf-/Suchgesuche ausblenden", key=W["wanted"], help="‚Kaufe iPhone 13‘ ist kein Angebot")
            private_only = st.checkbox("Nur Privatverkäufer", key=W["private"])
            exclude_raw = st.text_input("Keywords ausschließen (Komma)", key=W["excl"], help="Treffer in Titel/Kurzbeschreibung fliegen raus")

        submitted = st.form_submit_button("🔍 Inserate laden & bewerten", type="primary", width="stretch")

    st.divider()
    ai_enabled = st.toggle(
        "🤖 KI-Bewertung (Gemini)",
        value=bool(api_key),
        disabled=not api_key,
        help="Ohne Schlüssel bewertet die eingebaute Heuristik – gleiche Metriken, andere Quelle.",
    )
    if api_key:
        st.caption(f"🔑 Key-Quelle: `{key_source}`")
    else:
        st.caption(
            "Kein `GEMINI_API_KEY` gefunden → Heuristik-Modus. "
            "Erwünscht: **`secrets/gemini.key`** (lokal und im Container)."
        )
    if key_stale:
        st.warning(f"🔑 {key_stale}")
    model_name = st.selectbox("Modell", [MODEL_DEFAULT, "gemini-2.5-pro"], disabled=not (ai_enabled and api_key))

    st.divider()
    st.markdown("### 🧭 Gespeicherte Suchen")
    saved = store.load_searches()
    if not saved:
        st.caption("Noch keine gespeichert.")
    for item in saved:
        b1, b2 = st.columns([5, 1])
        with b1:
            if st.button(item["name"], key=f"load_{item['name']}", width="stretch"):
                st.session_state["preset"] = item["params"]
                st.rerun()
        with b2:
            if st.button("🗑", key=f"del_{item['name']}", help="Löschen"):
                store.delete_search(item["name"])
                st.rerun()


def current_params() -> SearchParams:
    return SearchParams(
        query=(query or "").strip() or "iphone",
        min_price=int(min_price) or None,
        max_price=int(max_price) or None,
        plz=(plz or "").strip() or None,
        radius_km=int(radius),
        sort=SERVER_SLUG.get(sort_label, "relevance"),
        max_results=int(max_results),
        private_only=bool(private_only),
        exclude_pro=bool(exclude_pro),
        hide_wanted=bool(hide_wanted),
        exclude_keywords=tuple(k.strip() for k in (exclude_raw or "").split(",") if k.strip()),
    )


# --------------------------------------------------------------------------- #
# Header + run trigger
# --------------------------------------------------------------------------- #
st.title("⚡ VybeDeal")
st.caption(
    "Kleinanzeigen-Monitor: Marktpreise aus den echten Suchergebnissen, "
    "KI-Bewertung mit Marge & ROI, Preisfall-Erkennung über mehrere Läufe, "
    "Favoriten und Export."
)

if submitted:
    st.session_state["run"] = {
        "params": json.dumps(vars(current_params())),
        "ai": bool(ai_enabled and api_key),
        "model": model_name,
    }
    st.session_state.pop("result", None)

if "result" not in st.session_state and "run" in st.session_state:
    run = st.session_state["run"]
    params = SearchParams(**json.loads(run["params"]))
    status = st.status("Starte …", expanded=True)
    status.write(f"🔎 `{params.query}` wird bei Kleinanzeigen gesucht …")

    try:
        raw = run_search(params.key(), run["params"])
    except Exception as exc:
        status.update(label="❌ Scrape fehlgeschlagen", state="error")
        st.error(
            f"Kleinanzeigen konnte nicht gelesen werden: {exc}\n\n"
            "Tipps: Seite im Browser öffnen (Bot-Schutz?), später erneut versuchen, "
            "oder `docker logs` prüfen."
        )
        st.stop()

    listings = [Listing(**d) for d in raw["listings"]]
    if raw["error"]:
        status.write(f"⚠️ {raw['error']}")
    if not listings:
        status.update(label="Keine Inserate gefunden", state="error")
        st.warning(
            "Keine Treffer. Suchbegriff lockern, Preisspanne erweitern oder Filter "
            "(gewerblich / Keywords) zurückschrauben."
        )
        with st.expander("Debug: aufgerufene URLs"):
            st.code("\n".join(raw["urls"]) or "–")
        st.stop()

    status.write(
        f"📦 {len(listings)} Inserate geladen"
        + (f" · Marktgröße: {raw['total']:,}".replace(",", ".") if raw["total"] else "")
        + f" · {raw['pages']} Seite(n)"
    )

    previous_prices = store.record_sightings(listings)
    heuristic_deals = score_cohort(listings)
    deals = heuristic_deals
    ai_used = False

    if run["ai"]:
        status.write("🤖 Gemini bewertet die Inserate …")
        verdicts: dict[str, dict] = {}
        try:
            client = make_client(get_api_key(st.secrets))
            size = 12
            for i in range(0, len(listings), size):
                batch = listings[i : i + size]
                rows = run_ai_batch(
                    json.dumps([l.to_dict() for l in batch], ensure_ascii=False),
                    params.query,
                    run["model"],
                )
                verdicts.update({str(r["ad_id"]): r for r in rows if r.get("ad_id")})
                status.write(f"🤖 {min(i + size, len(listings))}/{len(listings)} bewertet")
        except AIUnavailable as exc:
            status.write(f"⚠️ KI abgebrochen: {exc}")
            st.error(f"🤖 **KI-Bewertung nicht möglich:** {exc}")
        except Exception as exc:
            status.write(f"⚠️ KI teilweise fehlgeschlagen: {exc}")
        deals = merge_deals(listings, verdicts, heuristic_deals)
        ai_used = any(d.source == "ai" for d in deals)
        if not ai_used:
            st.info("Die Bewertung lieferte die eingebaute Heuristik – Preise, Marge, ROI und Preisfall-Erkennung funktionieren genauso.")

    status.update(label=f"Fertig: {len(deals)} Inserate bewertet", state="complete")
    st.session_state["result"] = {
        "deals": deals,
        "query": params.query,
        "ai_used": ai_used,
        "total_market": raw["total"],
        "params": json.loads(run["params"]),
        "model": run["model"],
        "ai_requested": run["ai"],
    }
    st.session_state["drops"] = store.price_drops(deals, previous_prices)

if "result" not in st.session_state:
    st.info(
        "Los geht's links: Suchbegriff einstellen → **Inserate laden & bewerten**. "
        "Ergebnisse bleiben bei Reruns erhalten (Favoriten, Sortierung, Filter). "
        "Ab dem zweiten Lauf erkennt VybeDeal **📉 Preisfälle** und merkt sich "
        "Favoriten über Sitzungen hinweg.",
        icon="👋",
    )
    st.stop()

result = st.session_state["result"]
params = SearchParams(**result["params"])
deals: list[Deal] = result["deals"]
drops: dict[str, float] = st.session_state.get("drops", {})
favourites = store.load_favourites()

# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #
scored = [d for d in deals if d.deal_score]
margins = [d.margin_eur for d in deals if d.margin_eur is not None]
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Inserate bewertet", len(deals), f"{'KI' if result['ai_used'] else 'Heuristik'}", delta_color="off")
m2.metric(
    "Ø Deal-Score",
    f"{sum(d.deal_score for d in scored) / len(scored):.1f}/10" if scored else "–",
    f"≥7: {sum(1 for d in deals if d.deal_score >= 7)} Stück",
    delta_color="off",
)
m3.metric("Beste Marge", fmt_eur(max(margins)) if margins else "–", "Wiederverkauf − Preis", delta_color="off")
m4.metric("📉 Preisfälle", len(drops), "seit letztem Lauf", delta_color="off")
m5.metric("⭐ Favoriten", len(favourites), "sitzungsübergreifend", delta_color="off")

st.divider()

# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #
c1, c2, c3, c4 = st.columns([2, 1, 1.4, 1.4])
with c1:
    sort_label_res = st.selectbox("Sortieren nach", list(SORT_OPTIONS))
with c2:
    min_score = st.slider("Min. Score", 1, 10, 6, help="Zeigt nur Inserate ab diesem Deal-Score (1–10)")
with c3:
    only_margin = st.checkbox("Nur positive Marge", value=False)
with c4:
    only_drops = st.checkbox(f"Nur Preisfälle ({len(drops)})", value=False, disabled=not drops)

sort_key = SORT_OPTIONS[sort_label_res]


def sort_value(d: Deal):
    if sort_key == "score":
        return (-d.deal_score, -(d.margin_eur or -9999))
    if sort_key == "margin":
        return -(d.margin_eur or -9999)
    if sort_key == "roi":
        return -(d.roi_pct or -9999)
    if sort_key == "price_asc":
        return d.listing.price_eur if d.listing.price_eur is not None else 1e12
    if sort_key == "price_desc":
        return -(d.listing.price_eur or 0)
    return 0


visible = [d for d in deals if d.deal_score >= min_score]
if only_margin:
    visible = [d for d in visible if (d.margin_eur or 0) > 0]
if only_drops:
    visible = [d for d in visible if d.listing.ad_id in drops]
if sort_key != "listing":
    visible = sorted(visible, key=sort_value)

tab_cards, tab_table, tab_market, tab_favs, tab_check = st.tabs(
    ["🔥 Deals", "📋 Tabelle", "📊 Markt-Analyse", "⭐ Favoriten", "🎯 Einzel-Check"]
)


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def render_card(deal: Deal, position: int) -> None:
    lst = deal.listing
    drop = drops.get(lst.ad_id)
    fav = lst.ad_id in favourites

    tags = [f'<span class="vd-tag">{score_badge(deal.deal_score)} {deal.deal_score}/10</span>']
    if deal.margin_eur is not None:
        cls = "vd-tag-good" if deal.margin_eur > 0 else "vd-tag-bad"
        roi = f" · {deal.roi_pct:.0f} %" if deal.roi_pct is not None else ""
        tags.append(f'<span class="vd-tag {cls}">Marge {fmt_eur(deal.margin_eur)}{roi}</span>')
    if deal.condition:
        tags.append(f'<span class="vd-tag">Zustand: {deal.condition}</span>')
    if deal.liquidity:
        tags.append(f'<span class="vd-tag">Liquidität {deal.liquidity}</span>')
    if deal.confidence:
        tags.append(f'<span class="vd-tag">Sicherheit {deal.confidence}</span>')
    if lst.is_pro:
        tags.append('<span class="vd-tag vd-tag-bad">gewerblich</span>')
    if drop:
        tags.append(f'<span class="vd-tag vd-tag-good">📉 {fmt_eur(drop)} günstiger</span>')
    if lst.shipping:
        tags.append(f'<span class="vd-tag">{lst.shipping}</span>')
    tags.append(f'<span class="vd-tag">Quelle: {deal.source}</span>')

    st.markdown(
        "<div class='vd-tags'>" + "".join(tags) + "</div>", unsafe_allow_html=True
    )

    col_img, col_body = st.columns([1, 3.2])
    with col_img:
        if lst.image_url:
            st.image(lst.image_url, width="stretch")
        else:
            st.caption("🖼️ kein Bild")
    with col_body:
        st.markdown(f"#### [{lst.title}]({lst.url})")
        info = st.columns([1, 1, 1.5])
        with info[0]:
            st.markdown(f"**{lst.price_label or '–'}**\n\n*Angebot*")
        with info[1]:
            st.markdown(f"**{fmt_eur(deal.resale_estimate_eur)}**\n\n*Wiederverkauf (geschätzt)*")
        with info[2]:
            st.markdown(f"**{lst.location or '–'}**\n\n*{lst.posted_date or 'Datum unbekannt'}*")

        if deal.reason:
            st.success(f"💡 {deal.reason}")
        if deal.red_flags:
            st.error("⚠️ " + " · ".join(deal.red_flags))
        if deal.negotiation_tip:
            st.info(f"🗣 {deal.negotiation_tip}")

        actions = st.columns([1, 1, 3])
        with actions[0]:
            if st.button(
                "⭐ Merken" if not fav else "✅ Gemerkt",
                key=f"fav_{lst.ad_id}_{position}",
                width="stretch",
            ):
                store.toggle_favourite(lst)
                st.rerun()
        with actions[1]:
            st.link_button("🔗 Inserat", lst.url, width="stretch")
        with actions[2]:
            if lst.attributes:
                st.caption(" · ".join(f"{k}: {v}" for k, v in list(lst.attributes.items())[:5]))
            if lst.description:
                with st.expander("Beschreibung", key=f"desc_{lst.ad_id}"):
                    st.write(lst.description[:1800])


with tab_cards:
    if not visible:
        st.warning(f"Keine Treffer mit Score ≥ {min_score}. Slider runter oder Filter rausnehmen.")
    else:
        st.caption(
            f"{len(visible)} von {len(deals)} Inseraten · Bewertung: "
            f"{'Gemini (' + result['model'] + ')' if result['ai_used'] else 'Heuristik (Preis vs. Markt + Signale)'}"
        )
        for position, deal in enumerate(visible):
            render_card(deal, position)
            st.markdown("---")

# --------------------------------------------------------------------------- #
# Table + export
# --------------------------------------------------------------------------- #
with tab_table:
    if not visible:
        st.info("Nichts zu zeigen – Filter anpassen.")
    else:
        df = pd.DataFrame([d.to_row() for d in visible])
        df["price_drop_eur"] = [drops.get(d.listing.ad_id) for d in visible]
        df.insert(0, "ad_id", [d.listing.ad_id for d in visible])
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={
                "score": st.column_config.ProgressColumn("Score", min_value=1, max_value=10, format="%d"),
                "title": st.column_config.TextColumn("Titel", width="large"),
                "price_eur": st.column_config.NumberColumn("Preis €", format="%.0f"),
                "resale_eur": st.column_config.NumberColumn("Wiederverkauf €", format="%.0f"),
                "margin_eur": st.column_config.NumberColumn("Marge €", format="%.0f"),
                "roi_pct": st.column_config.NumberColumn("ROI %", format="%.0f"),
                "price_drop_eur": st.column_config.NumberColumn("📉 Drop €", format="%.0f"),
                "url": st.column_config.LinkColumn("Link", display_text="öffnen"),
            },
        )
        b1, b2, b3 = st.columns([1, 1, 4])
        with b1:
            st.download_button("⬇️ CSV", export.to_csv(visible, drops), file_name=f"vybedeal-{export.stamp()}.csv", mime="text/csv", width="stretch")
        with b2:
            st.download_button("⬇️ JSON", export.to_json(visible, drops), file_name=f"vybedeal-{export.stamp()}.json", mime="application/json", width="stretch")

# --------------------------------------------------------------------------- #
# Market analysis
# --------------------------------------------------------------------------- #
with tab_market:
    priced = [d for d in deals if d.listing.price_eur is not None]
    if not priced:
        st.info("Keine numerischen Preise in dieser Ergebnisliste.")
    else:
        scatter_df = pd.DataFrame(
            [
                {
                    "Preis": d.listing.price_eur,
                    "Wiederverkauf": d.resale_estimate_eur,
                    "Score": d.deal_score,
                    "Titel": d.listing.title[:38],
                    "Marge": d.margin_eur,
                }
                for d in priced
                if d.resale_estimate_eur
            ]
        )
        prices = sorted(d.listing.price_eur for d in priced)
        q1 = prices[int(len(prices) * 0.25)]
        q2 = prices[len(prices) // 2]
        q3 = prices[int(len(prices) * 0.75)]
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Median", fmt_eur(q2))
        s2.metric("Unteres Quartil", fmt_eur(q1))
        s3.metric("Oberes Quartil", fmt_eur(q3))
        s4.metric("Spanne", f"{fmt_eur(prices[0])} – {fmt_eur(prices[-1])}")
        st.caption("Alles unter dem unteren Quartil ist ein Kandidat – wenn Zustand und Verkäufertyp stimmen.")

        m1, m2 = st.columns(2)
        with m1:
            st.markdown("**Angebot vs. Wiederverkaufswert**")
            if not scatter_df.empty:
                st.scatter_chart(scatter_df, x="Preis", y="Wiederverkauf", color="Score", height=320)
        with m2:
            st.markdown("**Score-Verteilung**")
            hist = pd.Series([d.deal_score for d in deals], name="Score").value_counts().sort_index().rename_axis("Score").reset_index(name="Anzahl")
            st.bar_chart(hist, x="Score", y="Anzahl", color="#3b82f6", height=320)

        st.markdown("**Top 10 nach Marge (€)**")
        if not scatter_df.empty:
            top = scatter_df.dropna(subset=["Marge"]).sort_values("Marge", ascending=False).head(10)
            if not top.empty:
                st.bar_chart(top, x="Titel", y="Marge", horizontal=True, height=300)

        st.markdown("**📈 Preis-Historie bekannter Inserate**")
        history = store.load_history()
        rows = []
        for entry in history.values():
            points = entry.get("prices") or []
            if not points:
                continue
            first = points[0].get("price") or 0
            last = entry.get("last_price") or 0
            rows.append(
                {
                    "Titel": (entry.get("title") or "")[:48],
                    "Erster Preis": first,
                    "Letzter Preis": last,
                    "Δ €": round(last - first),
                    "Kontakte": len(points),
                    "Link": entry.get("url"),
                }
            )
        if rows:
            st.dataframe(
                sorted(rows, key=lambda r: r["Δ €"])[:25],
                width="stretch",
                hide_index=True,
                column_config={
                    "Erster Preis": st.column_config.NumberColumn(format="%.0f €"),
                    "Letzter Preis": st.column_config.NumberColumn(format="%.0f €"),
                    "Δ €": st.column_config.NumberColumn(format="%+d"),
                    "Link": st.column_config.LinkColumn("Link", display_text="öffnen"),
                },
            )
        else:
            st.caption("Noch keine Historie – sie füllt sich mit jedem Lauf.")

# --------------------------------------------------------------------------- #
# Favourites
# --------------------------------------------------------------------------- #
with tab_favs:
    if not favourites:
        st.info("Noch keine Favoriten. Im Reiter **Deals** mit ⭐ merken.")
    else:
        st.caption(f"{len(favourites)} gemerkte Inserate – Preise werden bei jedem Lauf abgeglichen.")
        for ad_id, fav in list(favourites.items()):
            known = next((d.listing for d in deals if d.listing.ad_id == ad_id), None)
            current = known.price_eur if known else None
            base = fav.get("price_eur")
            c1, c2, c3, c4 = st.columns([3.4, 1, 1.2, 1])
            c1.markdown(f"[{fav.get('title', ad_id)}]({fav.get('url', '#')})")
            c2.markdown(fmt_eur(base))
            c3.metric(
                "Aktuell",
                fmt_eur(current if current is not None else base),
                round(current - base, 2) if current is not None and base else None,
            )
            if c4.button("Entfernen", key=f"unfav_{ad_id}", width="stretch"):
                store.toggle_favourite(Listing(ad_id=ad_id, title=fav.get("title", ""), url=fav.get("url", "")))
                st.rerun()
        st.download_button(
            "⬇️ Favoriten als JSON",
            json.dumps(list(favourites.values()), ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"vybedeal-favs-{export.stamp()}.json",
            mime="application/json",
        )

# --------------------------------------------------------------------------- #
# Single-link check
# --------------------------------------------------------------------------- #
def single_check(url: str, query: str, use_ai: bool, model: str) -> None:
    if not url.startswith("https://www.kleinanzeigen.de/s-anzeige/"):
        st.error("Bitte eine echte Kleinanzeigen-Anzeigen-URL einfügen.")
        return
    ad_id = url.rstrip("/").rsplit("/", 1)[-1].split("-")[0]
    lst = Listing(ad_id=ad_id, title="", url=url)
    try:
        with st.spinner("Lade Anzeige …"):
            resp = scraper.fetch_html(url, retries=2)
            scraper.parse_detail(resp.text, lst)
    except Exception as exc:
        st.error(f"Anzeige nicht erreichbar: {exc}")
        return
    if not lst.title:
        lst.title = (lst.description or "Anzeige")[:70]

    deal = score_cohort([lst])[0]
    if use_ai and api_key:
        try:
            client = make_client(get_api_key(st.secrets))
            verdicts = analyze([lst], query=query, api_key="", model=model, client=client)
            deal = merge_deals([lst], verdicts, [deal])[0]
        except Exception as exc:
            st.warning(f"KI nicht genutzt: {exc}")

    st.subheader(lst.title)
    st.markdown(f"**{lst.price_label or '–'}** · {lst.location or '–'} · {lst.posted_date or ''}")
    st.metric("Deal-Score", f"{deal.deal_score}/10", f"Marge {fmt_eur(deal.margin_eur)}")
    st.write(deal.reason)
    if deal.red_flags:
        st.error("⚠️ " + " · ".join(deal.red_flags))
    if deal.negotiation_tip:
        st.info(f"🗣 {deal.negotiation_tip}")
    left, right = st.columns([1, 2])
    with left:
        if lst.image_url:
            st.image(lst.image_url, width="stretch")
    with right:
        if lst.attributes:
            st.dataframe(pd.DataFrame(list(lst.attributes.items()), columns=["Merkmal", "Wert"]), hide_index=True, width="stretch")
        if lst.description:
            with st.expander("Vollständige Beschreibung"):
                st.write(lst.description[:2000])
    st.link_button("Zum Inserat", lst.url)


with tab_check:
    st.caption("Eine konkrete Anzeige prüfen, ohne vorher zu suchen.")
    check_url = st.text_input("Kleinanzeigen-URL", key="check_url", placeholder="https://www.kleinanzeigen.de/s-anzeige/…")
    if st.button("🔎 Anzeige prüfen", key="do_check", type="primary"):
        single_check(check_url.strip(), result["query"], bool(result["ai_requested"]), result["model"])

# --------------------------------------------------------------------------- #
# Save current search
# --------------------------------------------------------------------------- #
with st.sidebar, st.expander("💾 Aktuelle Suche speichern"):
    default_name = f"{result['query']} ≤{params.max_price or '∞'}€"
    name = st.text_input("Name", value=default_name)
    if st.button("Speichern", width="stretch"):
        store.save_search((name or default_name).strip(), result["params"])
        st.toast("Suche gespeichert 🎉")
        st.rerun()
