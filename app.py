import json
import os
import streamlit as st
from bs4 import BeautifulSoup
from curl_cffi import requests
from google import genai
from google.genai import types

# Page setup
st.set_page_config(page_title="VybeDeal - AI Deal Finder", layout="wide")
st.title("⚡ VybeDeal")
st.caption("AI-powered bargain finder & deal scorer for Kleinanzeigen")

# ---------------------------------------------------------
# Configuration: Load API Key from Secrets / Env Vars
# ---------------------------------------------------------
GEMINI_API_KEY = st.secrets.get(
    "GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", "")
)

if not GEMINI_API_KEY:
    st.error(
        "⚠️ Missing `GEMINI_API_KEY`. Please configure it in"
        " `.streamlit/secrets.toml` or set environment variable."
    )

# ---------------------------------------------------------
# UI: Top Search & Filter Bar (No Sidebar)
# ---------------------------------------------------------
col1, col2, col3 = st.columns([3, 1, 1])

with col1:
    query = st.text_input("Search query", value="iPhone 13")
with col2:
    max_price = st.number_input("Max price (€)", value=450, step=10)
with col3:
    min_score = st.slider(
        "Min Deal Score", min_value=1, max_value=10, value=6, step=1
    )

start_button = st.button("🚀 Find Deals", use_container_width=True)


# ---------------------------------------------------------
# Scraper Function with Data Enrichment
# ---------------------------------------------------------
def fetch_item_detail(url: str, headers: dict) -> str:
    """Fetches the detailed description from individual listing page."""
    try:
        resp = requests.get(url, headers=headers, impersonate="chrome120")
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            desc_container = soup.find("p", id="viewad-description-text")
            if desc_container:
                return desc_container.text.strip()
    except Exception:
        pass
    return ""


def fetch_kleinanzeigen_items(search_query: str, max_p: int):
    formatted_query = search_query.strip().replace(" ", "-")
    url = f"https://www.kleinanzeigen.de/s-preis::{max_p}/{formatted_query}/k0"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    response = requests.get(url, headers=headers, impersonate="chrome120")

    if response.status_code != 200:
        st.error(
            f"Failed to fetch data from Kleinanzeigen (Status Code:"
            f" {response.status_code})"
        )
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    articles = soup.find_all("article", class_="aditem")

    items = []
    for article in articles[:10]:
        try:
            title_elem = article.find("a", class_="ellipsis")
            price_elem = article.find(
                "p", class_="aditem-main--middle--price-shipping--price"
            )
            desc_elem = article.find(
                "p", class_="aditem-main--middle--description"
            )

            # Additional Enriched Fields
            location_elem = article.find(
                "div", class_="aditem-main--top--left"
            )
            date_elem = article.find("div", class_="aditem-main--top--right")
            img_elem = article.find("div", class_="imagebox")

            link = (
                "https://www.kleinanzeigen.de" + title_elem["href"]
                if title_elem and "href" in title_elem.attrs
                else ""
            )
            title = title_elem.text.strip() if title_elem else "Untitled"
            price = price_elem.text.strip() if price_elem else "N/A"
            snippet = desc_elem.text.strip() if desc_elem else ""
            location = (
                location_elem.text.strip().replace("\n", " ")
                if location_elem
                else "Unknown"
            )
            posted_date = (
                date_elem.text.strip().replace("\n", " ")
                if date_elem
                else "Recently"
            )

            image_url = ""
            if img_elem and img_elem.find("img"):
                image_url = img_elem.find("img").get("src", "")

            # Fetch enriched full body text if link is available
            full_description = (
                fetch_item_detail(link, headers) if link else snippet
            )
            description = (
                full_description if full_description else snippet
            )

            items.append(
                {
                    "title": title,
                    "price": price,
                    "location": location,
                    "posted_date": posted_date,
                    "description": description,
                    "url": link,
                    "image_url": image_url,
                }
            )
        except Exception:
            continue

    return items


# ---------------------------------------------------------
# LLM Evaluation via Gemini (With Enriched Context)
# ---------------------------------------------------------
def analyze_with_llm(items: list, search_term: str, client: genai.Client):
    prompt = f"""
    You are an expert resale analyst for the German second-hand market (Kleinanzeigen).
    The user search query is: "{search_term}".

    Below is a list of scraped listings with enriched information (title, price, location, publication date, description):
    {json.dumps(items, ensure_ascii=False)}

    Analyze each item's market value, condition, seller signals, and price.
    Filter and return ONLY items that represent good deals or potential flip opportunities.
    For each recommended deal, provide:
    - deal_score (1 to 10 integer rating)
    - reason (Concise explanation of why this is a bargain based on price, condition, or context)
    - resale_estimate (Estimated fair market resale price in EUR, e.g. "520 €")
    """

    response_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "price": {"type": "STRING"},
                "resale_estimate": {"type": "STRING"},
                "deal_score": {"type": "INTEGER"},
                "reason": {"type": "STRING"},
                "url": {"type": "STRING"},
                "location": {"type": "STRING"},
                "posted_date": {"type": "STRING"},
                "image_url": {"type": "STRING"},
            },
            "required": [
                "title",
                "price",
                "deal_score",
                "reason",
                "url",
                "resale_estimate",
            ],
        },
    }

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )

        return json.loads(response.text)
    except Exception as e:
        st.error(f"Error during AI analysis: {e}")
        return []


# ---------------------------------------------------------
# Execution & UI Display
# ---------------------------------------------------------
if start_button:
    if not GEMINI_API_KEY:
        st.error("Please configure GEMINI_API_KEY before running.")
    else:
        client = genai.Client(api_key=GEMINI_API_KEY)

        with st.spinner(
            "1/2 Fetching & enriching listings from Kleinanzeigen..."
        ):
            parsed_items = fetch_kleinanzeigen_items(query, max_price)

        if not parsed_items:
            st.warning("No listings found matching your search criteria.")
        else:
            st.info(
                f"Fetched {len(parsed_items)} enriched listings. Analyzing with"
                " Gemini..."
            )

            with st.spinner("2/2 Gemini is scoring bargains..."):
                deal_results = analyze_with_llm(parsed_items, query, client)

            # Filter by minimum score defined in UI slider
            filtered_deals = [
                d
                for d in deal_results
                if d.get("deal_score", 0) >= min_score
            ]

            st.markdown("---")
            st.subheader(
                f"🔥 Found {len(filtered_deals)} Bargains (Score ≥"
                f" {min_score}/10)"
            )

            if not filtered_deals:
                st.write(
                    f"No deals met your score threshold of **{min_score}/10**."
                    " Try lowering the minimum deal score slider."
                )
            else:
                for deal in filtered_deals:
                    with st.container():
                        card_col1, card_col2 = st.columns([1, 4])

                        with card_col1:
                            if deal.get("image_url"):
                                st.image(
                                    deal.get("image_url"),
                                    use_container_width=True,
                                )
                            else:
                                st.write("🖼️ *No Image*")

                        with card_col2:
                            st.markdown(
                                f"### [{deal.get('title')}]({deal.get('url')})"
                            )

                            m_col1, m_col2, m_col3 = st.columns(3)
                            with m_col1:
                                st.markdown(
                                    f"💰 **Listed Price:** `{deal.get('price')}`"
                                )
                                st.markdown(
                                    "📈 **Resale Value:**"
                                    f" `{deal.get('resale_estimate', 'N/A')}`"
                                )
                            with m_col2:
                                score = deal.get("deal_score", 0)
                                st.markdown(
                                    f"⭐ **Deal Score:** `{score}/10`"
                                )
                                st.markdown(
                                    "📍 **Location:**"
                                    f" {deal.get('location', 'Germany')}"
                                )
                            with m_col3:
                                st.markdown(
                                    "🕒 **Posted:**"
                                    f" {deal.get('posted_date', 'Recent')}"
                                )
                                st.markdown(
                                    f"[🔗 View on Kleinanzeigen↗]({deal.get('url')})"
                                )

                            st.success(
                                f"💡 **AI Insight:** {deal.get('reason')}"
                            )

                        st.markdown("---")