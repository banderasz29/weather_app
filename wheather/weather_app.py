import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import sqlite3
from datetime import datetime

# Page config
st.set_page_config(page_title="Weather Dashboard", layout="wide")

# Custom CSS for styling
st.markdown(
    """
    <style>
        .metric-container {
            background-color: #f0f8ff;
            padding: 15px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
        }
        .metric-icon {
            font-size: 40px;
            color: #1f77b4;
        }
    </style>
""",
    unsafe_allow_html=True,
)

# API key from secrets
api_key = st.secrets["openweathermap"]["api_key"]

# SQLite setup
conn = sqlite3.connect("weather_logs.db")
cursor = conn.cursor()
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS searches (
    city TEXT,
    temperature REAL,
    humidity INTEGER,
    wind_speed REAL,
    timestamp TEXT
)
"""
)
conn.commit()


# Cached functions
@st.cache_data(ttl=600)
def get_current_weather(city):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
    return requests.get(url).json()


@st.cache_data(ttl=3600)
def get_forecast(city):
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={api_key}&units=metric"
    return requests.get(url).json()


# Sidebar input
st.sidebar.title("Weather Settings")
city = st.sidebar.text_input("Enter city name", "Marrakesh")

st.title("🌤 Weather Dashboard")

if city:
    weather_data = get_current_weather(city)
    if weather_data.get("cod") == 200:
        temp = weather_data["main"]["temp"]
        humidity = weather_data["main"]["humidity"]
        wind_speed = weather_data["wind"]["speed"]
        description = weather_data["weather"][0]["description"].title()
        lat = weather_data["coord"]["lat"]
        lon = weather_data["coord"]["lon"]

        # Log search
        cursor.execute(
            "INSERT INTO searches VALUES (?, ?, ?, ?, ?)",
            (
                city,
                temp,
                humidity,
                wind_speed,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()

        # KPI cards
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(
                f"<div class='metric-container'><div class='metric-icon'>🌡️</div><h3>{temp:.1f} °C</h3><p>Temperature</p></div>",
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(
                f"<div class='metric-container'><div class='metric-icon'>💧</div><h3>{humidity}%</h3><p>Humidity</p></div>",
                unsafe_allow_html=True,
            )
        with col3:
            st.markdown(
                f"<div class='metric-container'><div class='metric-icon'>🌬️</div><h3>{wind_speed} m/s</h3><p>Wind Speed</p></div>",
                unsafe_allow_html=True,
            )

        st.write(f"**Condition:** {description}")

        # Map
        st.subheader("📍 Location")
        st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}))

        # Tabs
        tab1, tab2, tab3 = st.tabs(["Forecast", "Raw Data", "Search Logs"])
        forecast_data = get_forecast(city)
        if forecast_data.get("cod") == "200":
            df = pd.DataFrame(
                [
                    {
                        "datetime": item["dt_txt"],
                        "temp": item["main"]["temp"],
                        "humidity": item["main"]["humidity"],
                    }
                    for item in forecast_data["list"]
                ]
            )

            with tab1:
                st.subheader("📈 Temperature Forecast")
                fig_temp = px.line(
                    df,
                    x="datetime",
                    y="temp",
                    title="Temperature Forecast",
                    markers=True,
                    color_discrete_sequence=["#FF5733"],
                )
                st.plotly_chart(fig_temp, use_container_width=True)

                st.subheader("💧 Humidity Forecast")
                fig_hum = px.line(
                    df,
                    x="datetime",
                    y="humidity",
                    title="Humidity Forecast",
                    markers=True,
                    color_discrete_sequence=["#1f77b4"],
                )
                st.plotly_chart(fig_hum, use_container_width=True)

            with tab2:
                st.dataframe(df)

        with tab3:
            logs_df = pd.read_sql_query("SELECT * FROM searches", conn)
            st.dataframe(logs_df)
    else:
        st.warning("City not found or API error.")
