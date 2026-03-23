"""Main entry point for the Macro Dashboard."""

import streamlit as st

st.set_page_config(
    page_title="Macro Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("Macro Dashboard")
st.markdown(
    """
Welcome to the Macro Dashboard. Use the sidebar to navigate between sections.

### Sections

**Economic Analysis**
- 🏦 DI Curve — Brazilian interbank yield curve (B3 DI futures)
- *(more coming)*

**Market Analysis**
- *(coming soon)*
"""
)
