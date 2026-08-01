"""Streamlit interface for the repository assistant."""

from __future__ import annotations

from uuid import uuid4

import streamlit as st

from src.bot import (
    create_repository_agent,
    final_text,
    get_interrupt,
    interrupt_details,
    resume_tool_call,
    send_message,
)


st.set_page_config(page_title="Repository Assistant", page_icon="💬", layout="wide")


@st.cache_resource
def load_agent():
    return create_repository_agent()


def new_chat() -> str:
    client_id = str(uuid4())
    st.session_state.chats[client_id] = {
        "client_id": client_id,
        "thread_id": f"repository-chat-{uuid4()}",
        "title": "New chat",
        "messages": [],
        "pending": None,
    }
    return client_id


def initialize_state() -> None:
    if "chats" not in st.session_state:
        st.session_state.chats = {}
    if "active_client_id" not in st.session_state:
        st.session_state.active_client_id = new_chat()


def apply_agent_result(chat: dict, result) -> None:
    interrupt = get_interrupt(result)
    if interrupt is not None:
        chat["pending"] = interrupt_details(interrupt)
    else:
        chat["pending"] = None
        chat["messages"].append({"role": "assistant", "content": final_text(result)})


initialize_state()
agent = load_agent()

with st.sidebar:
    st.title("Chats")
    if st.button("＋ New chat", use_container_width=True):
        st.session_state.active_client_id = new_chat()
        st.rerun()

    for client_id, saved_chat in reversed(st.session_state.chats.items()):
        if st.button(
            saved_chat["title"],
            key=f"select-{client_id}",
            use_container_width=True,
            type="primary" if client_id == st.session_state.active_client_id else "secondary",
        ):
            st.session_state.active_client_id = client_id
            st.rerun()

chat = st.session_state.chats[st.session_state.active_client_id]

st.title("Repository Assistant")
st.caption(f"Chat ID: {chat['client_id']}")

for saved_message in chat["messages"]:
    with st.chat_message(saved_message["role"]):
        st.markdown(saved_message["content"])

if chat["pending"]:
    pending = chat["pending"]
    with st.chat_message("assistant"):
        st.warning(pending["description"])
        st.code(
            f"{pending['name']}({pending['args']})",
            language="python",
        )

        feedback = st.text_input(
            "Optional feedback if you reject this action",
            key=f"feedback-{chat['client_id']}",
        )
        approve_column, reject_column = st.columns(2)

        if approve_column.button(
            "Approve",
            type="primary",
            use_container_width=True,
            key=f"approve-{chat['client_id']}",
        ):
            with st.spinner("Running approved action..."):
                result = resume_tool_call(
                    agent, chat["thread_id"], decision="approve"
                )
            apply_agent_result(chat, result)
            st.rerun()

        if reject_column.button(
            "Reject",
            use_container_width=True,
            key=f"reject-{chat['client_id']}",
        ):
            with st.spinner("Sending your feedback to the agent..."):
                result = resume_tool_call(
                    agent,
                    chat["thread_id"],
                    decision="reject",
                    feedback=feedback or "The user rejected this action.",
                )
            apply_agent_result(chat, result)
            st.rerun()

prompt = st.chat_input(
    "Ask about a repository or request a GitHub clone...",
    disabled=chat["pending"] is not None,
)

if prompt:
    chat["messages"].append({"role": "user", "content": prompt})
    if chat["title"] == "New chat":
        chat["title"] = prompt[:32] + ("…" if len(prompt) > 32 else "")

    with st.spinner("Thinking..."):
        try:
            result = send_message(agent, chat["thread_id"], prompt)
            apply_agent_result(chat, result)
        except Exception as exc:
            chat["messages"].append(
                {
                    "role": "assistant",
                    "content": f"I could not reach the model: `{exc}`",
                }
            )
    st.rerun()
