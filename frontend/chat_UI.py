import streamlit as st
import requests
import pandas as pd
from backend.utils.logging_config import logger

API_URL_ASK = "http://127.0.0.1:8000/ask"   # endpoint for direct SQL execution
API_URL_CHAT = "http://127.0.0.1:8000/chat" # endpoint for AI-driven SQL chat

# **Set Page Title and Description**
st.set_page_config(page_title="Schema-Aware AI SQL Agent", page_icon="💬", layout="wide")

# 🎯 **Add Header and Subheader**
st.title("💬 Schema-Aware AI SQL Agent")
st.subheader("Ask natural language questions and get AI-powered answers")
st.markdown("---") 


# Ensure `query_mode` is in session state
if "query_mode" not in st.session_state:
    st.session_state["query_mode"] = "API"  # Default to API

# **User selects query mode**
query_mode = st.radio("## Choose Mode:", ["Use API Point", "Use AI Agent"], 
                      index=0 if st.session_state["query_mode"] == "API" else 1, 
                      horizontal=True)

# Update session state when user changes selection
#st.session_state["query_mode"] = "API" if query_mode == "Use API Point" else "Chat"
if st.session_state["query_mode"] != ("API" if query_mode == "Use API Point" else "Chat"):
    st.session_state["query_mode"] = "API" if query_mode == "Use API Point" else "Chat"
    st.rerun()  # 🔄 Refresh UI instead of stopping execution

# Display selected mode
st.markdown(f"***✅ Selected Mode: `{query_mode}`***")

with st.form(key="question_form"):
    user_question = st.text_input("Ask a question:", placeholder="e.g., who are our top customers by revenue?")
    submit_button = st.form_submit_button("Submit")

    if user_question.strip() == "":
        st.warning("⚠️ Please enter a question.")
    else:
        status = st.status("⏳ Processing your request, please wait...", expanded=False)

        # 🔗 **Choose API based on selected mode**
        API_URL = API_URL_CHAT if st.session_state["query_mode"] == "Chat" else API_URL_ASK

        payload = {"query": user_question} if st.session_state["query_mode"] == "Chat" else {"question": user_question}
        logger.debug(f"📌 Sending request to {API_URL} with payload: {payload}")  # ✅ Log payload before sending

        response = requests.post(API_URL, json=payload)

        if response.status_code == 200:
            data = response.json()

            # 🔍 **Check if AI is asking for clarification**
            if "message" in data and data["message"].startswith("CLARIFY:"):
                clarification = data["message"].replace("CLARIFY:", "").strip()
                st.warning(f"🤔 I needs more details: {clarification}")
                st.stop()  # ⛔ Stop execution and let the user refine the question

            # **Handle API Errors First**
            if "error" in data:
                error_message = data.get("message", "An unexpected error occurred.")
                logger.debug(f"📌 Debug: Received API error -> {data['error']} | Message -> {error_message}")

                if data["error"] == "Unsupported Question":
                    st.warning(f"🤔 {error_message}")
                elif data["error"] == "Clarification Needed":
                    st.error(f"🤔 {error_message}")
                elif data["error"] == "Access Denied":
                    st.error(f"🚫 {error_message}")
                elif data["error"] == "Security Violation":
                    st.error(f"🛑 {error_message}")
                else:
                    st.error(f"❌ {error_message}")
                
                status.update(label="Response received!", state="complete", expanded=False)
                st.stop()

            else:
                with st.expander("🗨️ User Query"):
                    st.write(data.get("question", data.get("query", "No question available")))

                # Show Generated SQL (if available)
                if "generated_sql" in data:
                    with st.expander("🔍 View Generated SQL Query"):
                        st.code(data["generated_sql"], language="sql")

                # Show Query Results
                if "result" in data:
                    if isinstance(data["result"], list) and data["result"]:
                        df = pd.DataFrame(data["result"])
                        st.subheader("📊 Query Results")
                        st.dataframe(df.style.format({"total_sales": "${:,.2f}"}))
                    else:
                        st.warning(" Query executed successfully, but no records were found.")
                else:
                    st.error("❌ Unexpected API response format. No valid data returned.")

        else:
            st.error(f"❌ API Error: {response.json().get('message', 'Unknown error')}")

        status.update(label="Response received!", state="complete", expanded=False)
