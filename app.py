import os
import json
import streamlit as st
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime

# --- 1. INITIALIZATION ---
load_dotenv()
client = OpenAI(api_key=os.getenv("XAI_API_KEY"), base_url="https://api.x.ai/v1")
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

st.set_page_config(page_title="Home Inventory Agent", layout="wide")
st.title("🏠 Home Storage Agent")

# --- 2. DATABASE TOOLS ---
def upsert_item(item_name, quantity_change, category=None, location=None, min_stock=1):
    item_name = item_name.lower().strip()
    res = supabase.table("inventory").select("*").eq("item_name", item_name).execute()
    now = datetime.now().isoformat()
    
    if res.data:
        new_qty = res.data[0]['quantity'] + quantity_change
        data = {"quantity": max(0, new_qty), "last_updated": now}
        if category: data["category"] = category
        if location: data["location"] = location
        supabase.table("inventory").update(data).eq("item_name", item_name).execute()
        return f"Successfully updated **{item_name}**. New balance: **{max(0, new_qty)}**."
    else:
        data = {
            "item_name": item_name,
            "quantity": max(0, quantity_change),
            "category": category or "General",
            "location": location or "Home",
            "min_stock_alert": min_stock,
            "last_updated": now
        }
        supabase.table("inventory").insert(data).execute()
        return f"Successfully added new item: **{item_name}** (Qty: {quantity_change})."

def delete_item(item_name):
    supabase.table("inventory").delete().eq("item_name", item_name.lower()).execute()
    return f"Removed **{item_name}** from the database completely."

# --- 3. AGENT DEFINITIONS ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "upsert_item",
            "description": "Add, update, or remove stock. Positive to add, negative to remove.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string"},
                    "quantity_change": {"type": "integer"},
                    "category": {"type": "string"},
                    "location": {"type": "string"},
                    "min_stock": {"type": "integer"}
                },
                "required": ["item_name", "quantity_change"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_item",
            "description": "Delete an item record.",
            "parameters": {
                "type": "object",
                "properties": {"item_name": {"type": "string"}},
                "required": ["item_name"]
            }
        }
    }
]

# --- 4. SESSION STATE & SIDEBAR ---
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "system", 
            "content": """You are a home organization assistant. 
            MAKER-CHECKER PROTOCOL:
            1. Extract item_name, quantity_change, category, and location from user input.
            2. Before calling a tool, summarize these details and ASK the user to confirm.
            3. DO NOT execute the tool until the user says 'Yes' or 'Confirm'."""
        },
        {"role": "assistant", "content": "Hello! I'm ready to manage your inventory. What did you add or remove today?"}
    ]

if "pending_action" not in st.session_state:
    st.session_state.pending_action = None

with st.sidebar:
    st.header("📋 Live Inventory")
    inv_data = supabase.table("inventory").select("*").order("item_name").execute()
    if inv_data.data:
        st.dataframe(inv_data.data, hide_index=True)
        if st.button("🔄 Refresh List"):
            st.rerun()
    else:
        st.write("Inventory is empty.")

# --- 5. CHAT UI & AGENTIC WORKFLOW ---
# FIXED: Cleaner loop to hide system and internal tool messages
for msg in st.session_state.messages:
    # 1. Skip system messages
    if msg["role"] == "system":
        continue
    
    # 2. Extract content (handles both dicts and SDK objects)
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    
    # 3. Only display if there is actual text to show
    if content:
        with st.chat_message(msg["role"]):
            st.markdown(content)

# User Input
if prompt := st.chat_input("e.g., I put 10 cans of soup in the Pantry"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # CASE A: Confirmation logic
        if prompt.lower() in ["yes", "confirm", "go ahead", "y", "do it"] and st.session_state.pending_action:
            pending = st.session_state.pending_action
            
            with st.status("Updating database...", expanded=False) as status:
                result = upsert_item(**pending["args"]) if pending["type"] == "upsert" else delete_item(**pending["args"])
                status.update(label="Complete!", state="complete")
            
            st.success(result)
            st.session_state.messages.append({"role": "assistant", "content": f"✅ {result}"})
            st.session_state.pending_action = None 
            st.rerun()

        # CASE B: Standard Agent Logic
        else:
            response = client.chat.completions.create(
                model="grok-4.3",
                messages=st.session_state.messages,
                tools=tools,
                tool_choice="auto"
            )
            
            response_message = response.choices[0].message
            
            if response_message.tool_calls:
                tool_call = response_message.tool_calls[0]
                args = json.loads(tool_call.function.arguments)
                
                st.session_state.pending_action = {
                    "type": "upsert" if tool_call.function.name == "upsert_item" else "delete",
                    "args": args
                }
                
                confirm_text = f"""**Inventory Update Proposal:**
- **Item:** `{args.get('item_name')}`
- **Action:** {'Add/Update' if tool_call.function.name == 'upsert_item' else 'Delete'}
- **Quantity:** `{args.get('quantity_change', 'N/A')}`
- **Category:** `{args.get('category', 'Not specified')}`
- **Location:** `{args.get('location', 'Not specified')}`

**Proceed?** (Yes/No)"""
                
                st.markdown(confirm_text)
                st.session_state.messages.append({"role": "assistant", "content": confirm_text})
            
            else:
                bot_text = response_message.content
                st.markdown(bot_text)
                st.session_state.messages.append({"role": "assistant", "content": bot_text})