"""LangChain agent construction and chat execution helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.messages import AIMessage
from langchain.tools import tool
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_community.utilities.sql_database import SQLDatabase
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .ProcessManager import ProcessManager
from .repositoryParser import parse_repository, refresh_repository_if_changed


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "call_graph.db"
REPOSITORIES_PATH = PROJECT_ROOT / "repositories"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b-q4_K_M")
DATABASE_CONTEXT_LIMIT = 30_000


@tool
def git_clone(repository_url: str, directory_name: str) -> str:
    """Clone an HTTPS GitHub repository into a new local directory.

    Args:
        repository_url: Full HTTPS URL, such as https://github.com/org/repo.git.
        directory_name: A single, new directory name for the cloned repository.
    """

    try:
        result = ProcessManager(REPOSITORIES_PATH).clone(
            repository_url=repository_url,
            directory_name=directory_name,
        )
        return result.as_text()
    except (OSError, ValueError) as exc:
        return f"Clone was not run: {exc}"


@tool
def parse_cloned_repository(directory_name: str) -> str:
    """Parse an already-cloned repository into the call-graph database.

    Args:
        directory_name: The single directory name used by git_clone.
    """

    try:
        repository_path = (REPOSITORIES_PATH / directory_name).resolve()
        if repository_path.parent != REPOSITORIES_PATH.resolve():
            raise ValueError("The repository must be a single directory name.")
        if not repository_path.is_dir():
            raise ValueError(f"Cloned repository does not exist: {repository_path}")
        return parse_repository(repository_path, DATABASE_PATH)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return f"Database parse was not run: {exc}"


def create_repository_agent() -> Any:
    """Build one checkpointed agent shared by all Streamlit chat sessions."""

    model = ChatOllama(
        model=OLLAMA_MODEL,
        temperature=0,
        think=False,
        base_url="http://127.0.0.1:11434",
    )

    database = SQLDatabase.from_uri(f"sqlite:///{DATABASE_PATH.as_posix()}")
    sql_tools = SQLDatabaseToolkit(db=database, llm=model).get_tools()

    return create_agent(
        model=model,
        tools=[*sql_tools, git_clone, parse_cloned_repository],
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "git_clone": {
                        "allowed_decisions": ["approve", "reject"]
                    },
                    "parse_cloned_repository": {
                        "allowed_decisions": ["approve", "reject"]
                    }
                },
                description_prefix="Repository action requires your approval",
            )
        ],
        checkpointer=InMemorySaver(),
        system_prompt="""
        You are a GitHub repository assistant.

        Use the read-only SQL tools to answer questions about the repository
        call-graph database. Only issue SELECT queries.

        The SQLite database contains this schema:

        repo_table(
            id INTEGER PRIMARY KEY,
            repo_name TEXT,
            path TEXT
        )
        file_table(
            id INTEGER PRIMARY KEY,
            repo_id INTEGER REFERENCES repo_table(id),
            file_name TEXT,
            file_path TEXT
        )
        function_table(
            id INTEGER PRIMARY KEY,
            file_id INTEGER REFERENCES file_table(id),
            function_name TEXT,
            function_type TEXT,
            line_start INTEGER,
            line_end INTEGER
        )
        raw_text_table(
            id INTEGER PRIMARY KEY,
            file_id INTEGER REFERENCES file_table(id),
            hash_code TEXT,
            raw_text TEXT
        )
        edge_table(
            id INTEGER PRIMARY KEY,
            repo_id INTEGER REFERENCES repo_table(id),
            source_type TEXT,
            source_id INTEGER,
            target_type TEXT,
            target_id INTEGER,
            relationship_type TEXT
        )

        raw_text_table.raw_text contains the complete source code of each parsed
        Python file. You can and should analyze that source when the user asks
        about implementation details. Access it by joining:

        repo_table r
        JOIN file_table f ON f.repo_id = r.id
        JOIN raw_text_table t ON t.file_id = f.id

        Functions belong to files through function_table.file_id. Call edges use
        edge_table.source_id and edge_table.target_id to reference
        function_table.id. Filter by repo_table.repo_name or repo_table.id so
        results from different repositories are never mixed.

        For repository questions, query the relevant database rows before
        answering. Do not claim that you cannot inspect local code merely
        because a repository path is local: parsed source is available in
        raw_text_table. If the required source is absent, explain which query
        showed it was absent. Do not rely only on an earlier parsing summary.

        Use git_clone when the user asks to clone a GitHub repository. Tell the
        user what you intend to do, and rely on the approval workflow before
        execution. After a successful clone, ask whether the user wants to parse
        that repository into the call-graph database. Do not call
        parse_cloned_repository until the user agrees. That tool has its own
        approval checkpoint in the human-in-the-loop workflow.
        Never claim an action succeeded until its tool result says so.
        When a human rejects a proposed tool call, acknowledge their decision
        and feedback; do not describe the rejection as a command failure.

        For greetings and general conversation, reply directly without tools.
        Use SQL tools only when the user asks about repository code or the
        call-graph database.

        Be concise, acknowledge uncertainty, and use retrieved evidence.
        Make sure you always reply with a response. If you are unsure, ask for clarification. 
        """,
    )


def thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _is_repository_question(message: str) -> bool:
    """Route likely repository questions without asking the model to classify them."""

    normalized = message.strip().lower()
    if not normalized:
        return False
    if normalized in {"hi", "hello", "hey", "thanks", "thank you"}:
        return False
    action_phrases = ("clone ", "git clone", "parse ", "add to the database")
    return not any(phrase in normalized for phrase in action_phrases)


def _database_context(message: str) -> str:
    """Retrieve source for the named, or most recently parsed, repository."""

    if not _is_repository_question(message):
        return ""

    with sqlite3.connect(DATABASE_PATH) as connection:
        repositories = connection.execute(
            "SELECT id, repo_name, path FROM repo_table ORDER BY id DESC"
        ).fetchall()
        if not repositories:
            return "The call-graph database currently contains no parsed repositories."

        lowered_message = message.casefold()
        selected = next(
            (repo for repo in repositories if repo[1].casefold() in lowered_message),
            repositories[0],
        )
        _repo_id, repo_name, repository_path = selected

    refresh_result = refresh_repository_if_changed(
        Path(repository_path), DATABASE_PATH
    )

    with sqlite3.connect(DATABASE_PATH) as connection:
        repo_id = connection.execute(
            "SELECT id FROM repo_table WHERE path = ?", (repository_path,)
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT f.file_name, t.raw_text
            FROM file_table AS f
            JOIN raw_text_table AS t ON t.file_id = f.id
            WHERE f.repo_id = ?
            ORDER BY f.file_name
            """,
            (repo_id,),
        ).fetchall()
        functions = connection.execute(
            """
            SELECT f.file_name, fn.function_name, fn.line_start, fn.line_end
            FROM file_table AS f
            JOIN function_table AS fn ON fn.file_id = f.id
            WHERE f.repo_id = ?
            ORDER BY f.file_name, fn.line_start
            """,
            (repo_id,),
        ).fetchall()

    catalog = ", ".join(repo[1] for repo in repositories)
    function_summary = "\n".join(
        f"- {file_name}: {name} (lines {start}-{end})"
        for file_name, name, start, end in functions
    )
    source_sections: list[str] = []
    used = 0
    truncated = False
    for file_name, source in rows:
        section = f"\n--- FILE: {file_name} ---\n{source}\n"
        remaining = DATABASE_CONTEXT_LIMIT - used
        if remaining <= 0:
            truncated = True
            break
        if len(section) > remaining:
            source_sections.append(section[:remaining])
            truncated = True
            break
        source_sections.append(section)
        used += len(section)

    truncation_note = (
        "\nSource context was truncated; use the SQL tools for any missing sections."
        if truncated
        else ""
    )
    refresh_note = (
        f"\nRepository files changed, so the database was refreshed. {refresh_result}"
        if refresh_result
        else "\nStored hashes match the current repository files."
    )
    return f"""
DATABASE CONTEXT (retrieved by the application before this response)
Available repositories: {catalog}
Selected repository: {repo_name}
Hash synchronization: {refresh_note}

Functions:
{function_summary or "(no functions recorded)"}

Stored source:
{''.join(source_sections) or "(no source recorded)"}
{truncation_note}
Use this database evidence to answer the user's question. If the user clearly
means a different repository, use the SQL tools to query it. Do not say that
you cannot access the parsed source.
""".strip()


def send_message(agent: Any, thread_id: str, message: str) -> Any:
    context = _database_context(message)
    model_message = message
    if context:
        model_message = f"{message}\n\n<application_database_context>\n{context}\n</application_database_context>"
    return agent.invoke(
        {"messages": [{"role": "user", "content": model_message}]},
        config=thread_config(thread_id),
        version="v2",
    )


def resume_tool_call(
    agent: Any,
    thread_id: str,
    decision: str,
    feedback: str = "",
) -> Any:
    decision_payload: dict[str, str] = {"type": decision}
    if feedback:
        decision_payload["message"] = feedback

    return agent.invoke(
        Command(resume={"decisions": [decision_payload]}),
        config=thread_config(thread_id),
        version="v2",
    )


def get_interrupt(result: Any) -> Any | None:
    """Handle both current result objects and legacy dictionary results."""

    interrupts = getattr(result, "interrupts", None)
    if interrupts:
        return interrupts[0]
    if isinstance(result, dict):
        legacy_interrupts = result.get("__interrupt__", ())
        if legacy_interrupts:
            return legacy_interrupts[0]
    return None


def interrupt_details(interrupt: Any) -> dict[str, Any]:
    value = getattr(interrupt, "value", interrupt)
    if not isinstance(value, dict):
        return {"description": str(value), "name": "tool", "args": {}}

    requests = value.get("action_requests") or value.get("actionRequests") or []
    request = requests[0] if requests else {}
    return {
        "description": request.get("description", "The agent wants to run a tool."),
        "name": request.get("name") or request.get("action") or "tool",
        "args": request.get("args", {}),
    }


def final_text(result: Any) -> str:
    values = getattr(result, "value", result)
    messages = values.get("messages", []) if isinstance(values, dict) else []
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            if isinstance(message.content, str):
                return message.content
            return str(message.content)
    return "The agent completed the request without a text response."
